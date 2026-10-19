"""Live xAI Grok Voice adapter: realtime WebSocket session manager.

# VERIFY against current docs: everything protocol-specific in this file. It
# assumes xAI's realtime voice API follows the OpenAI-Realtime-compatible event
# schema (session.update / response.create / function_call_output ...). Confirm
# the endpoint, auth header, model + voice names, and each event name below
# before the first live call. Listed in docs/VERIFY_LIST.md.

Media: with TELEPHONY_MODE=live the insurer audio is bridged insurer <-> Telnyx <->
xAI over SIP (telephony/telnyx_sip.py + the /voice/telnyx/webhook route); this
WebSocket is the control channel (instructions, tools, transcripts). Each call
runs on its own thread + event loop; every handler callback opens its own DB
session inside VoiceCallManager.

Guardrails here are post-hoc for audio (decision D15): agent transcripts are
checked when they complete and a violation interrupts the response.

Check connectivity:  cd core && python -m app.adapters.xai_voice.live --check
"""

import argparse
import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from claimshield_contracts.enums import TurnAction

from ...services.security_policy import HOLD_LINE
from .base import (
    RESUME_UNREACHABLE,
    RESUME_VERIFIED,
    VoiceAdapter,
    VoiceEventHandler,
    VoiceSessionConfig,
)
from .dispatcher import CONTROL_KEY, CONTROL_PAUSE

log = logging.getLogger("claimshield.voice.xai")

# VERIFY against current docs: realtime event names.
EVT_SESSION_UPDATED = "session.updated"
EVT_INPUT_TRANSCRIPT_DONE = "conversation.item.input_audio_transcription.completed"
EVT_OUTPUT_TRANSCRIPT_DONE = (
    "response.output_audio_transcript.done",
    "response.audio_transcript.done",
)
EVT_FUNCTION_ARGS_DONE = "response.function_call_arguments.done"
EVT_ERROR = "error"


#: Without a "greet" command (the AI leg joined the conference) the agent still
#: speaks after this many seconds, so a lost webhook never leaves a silent call.
GREET_FALLBACK_S = 8.0
DEFAULT_GREETING = (
    "The call just connected. Speak first, before they say anything. Introduce yourself "
    "as Amyna, an automated assistant calling for the member about a medical bill they "
    "authorized you to discuss. Then ask who you are speaking with. One short turn."
)
RESUME_NOTES = {
    RESUME_VERIFIED: (
        "The member joined this call and handled the identity verification directly with "
        "the representative. They may still be on the line. Do not ask for the member or "
        "for that information again. Briefly thank the representative and continue the dispute."
    ),
    RESUME_UNREACHABLE: (
        "The member could not be reached by phone just now. Tell the representative, in one "
        "short turn, that you couldn't reach the member and that the member will verify "
        "directly later. Ask whether they can continue with the dispute without it or note a "
        "callback. Never provide or guess the information yourself."
    ),
}


@dataclass
class _LiveCall:
    config: VoiceSessionConfig
    commands: asyncio.Queue[str] | None = None
    loop: asyncio.AbstractEventLoop | None = None
    paused: bool = False
    greeted: bool = False
    thread: threading.Thread | None = None
    pending_commands: list[str] = field(default_factory=list)


class XaiRealtimeVoiceAdapter(VoiceAdapter):
    """One realtime session per insurer call.

    SIP mode (docs-verified 2026-10): the carrier bridges the insurer leg to
    ``sip:{number}@sip.voice.x.ai;transport=tls``; xAI then fires a signed
    realtime.call.incoming webhook including ``data.call_id``; this adapter
    attaches with ``?call_id=...``. See routers/voice.py /voice/xai/webhook.
    """

    mode = "live"
    needs_sip_attach = True

    def __init__(self, *, api_key: str, url: str, model: str | None, voice: str | None) -> None:
        self._api_key = api_key
        self._url = url
        self._model = model
        self._voice = voice
        self._calls: dict[str, _LiveCall] = {}
        # Commands that arrived before the session attached (e.g. "greet" when the
        # AI leg joined the conference before xAI's inbound webhook was processed).
        self._early: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ VoiceAdapter

    def start_session(self, config: VoiceSessionConfig, handler: VoiceEventHandler) -> None:
        call = _LiveCall(config=config)
        call.thread = threading.Thread(
            target=lambda: asyncio.run(self._run(call, handler)),
            name=f"xai-voice-{config.call_id}",
            daemon=True,
        )
        with self._lock:
            call.pending_commands.extend(self._early.pop(config.call_id, []))
            self._calls[config.call_id] = call
        call.thread.start()

    def resume_session(
        self, call_id: str, handler: VoiceEventHandler, note: str = RESUME_VERIFIED
    ) -> None:
        self._command(call_id, f"resume:{note}")

    def start_speaking(self, call_id: str) -> None:
        self._command(call_id, "greet")

    def pause_session(self, call_id: str) -> None:
        self._command(call_id, "pause")

    def end_session(self, call_id: str, reason: str) -> None:
        self._command(call_id, "close")

    # ------------------------------------------------------------------ protocol

    def session_update(self, config: VoiceSessionConfig) -> dict[str, Any]:
        """Session configuration. VERIFY against current docs: every field name."""
        session: dict[str, Any] = {
            "instructions": config.instructions,
            "tools": config.tools,
            "tool_choice": "auto",
            "turn_detection": {"type": "server_vad"},
            "input_audio_transcription": {"enabled": True},
        }
        if config.voice or self._voice:
            session["voice"] = config.voice or self._voice
        return {"type": "session.update", "session": session}

    def _endpoint(self, config: VoiceSessionConfig) -> str:
        # SIP attach (verified): attach to the provider-side call; otherwise a
        # fresh realtime conversation is created for `?model=`.
        if config.bridge_call_id:
            return f"{self._url}?call_id={config.bridge_call_id}"
        model = config.model or self._model
        return f"{self._url}?model={model}" if model else self._url

    async def _run(self, call: _LiveCall, handler: VoiceEventHandler) -> None:
        import websockets  # live-only dependency

        call.loop = asyncio.get_running_loop()
        call.commands = asyncio.Queue()
        for command in call.pending_commands:
            call.commands.put_nowait(command)
        call_id = call.config.call_id
        headers = {"Authorization": f"Bearer {self._api_key}"}  # VERIFY auth scheme
        try:
            async with websockets.connect(
                self._endpoint(call.config), additional_headers=headers
            ) as ws:
                await ws.send(json.dumps(self.session_update(call.config)))
                if call.config.metadata.get("wait_for_join") == "1":
                    # Conference mode: greet when Core reports the AI leg is audible.
                    call.loop.call_later(GREET_FALLBACK_S, call.commands.put_nowait, "greet")
                else:
                    await self._greet(ws, call)
                commands = asyncio.create_task(self._consume_commands(ws, call))
                try:
                    async for raw in ws:
                        await self._on_event(ws, call, handler, json.loads(raw))
                finally:
                    commands.cancel()
        except Exception as exc:  # network/protocol failure ends the call safely
            log.exception("xAI realtime session failed", extra={"fields": {"call_id": call_id}})
            handler.on_call_ended(call_id, f"voice session error: {type(exc).__name__}")
            return
        handler.on_call_ended(call_id, "remote closed")

    async def _on_event(
        self, ws: Any, call: _LiveCall, handler: VoiceEventHandler, event: dict[str, Any]
    ) -> None:
        call_id = call.config.call_id
        event_type = event.get("type")
        if event_type == EVT_SESSION_UPDATED:
            if call.config.metadata.get("wait_for_join") != "1":
                await self._greet(ws, call)
        elif event_type == EVT_INPUT_TRANSCRIPT_DONE:
            decision = handler.on_insurer_utterance(call_id, event.get("transcript", ""))
            if decision.action == TurnAction.PAUSE_FOR_HUMAN:
                await self._hold(ws, call, decision.say or HOLD_LINE)
        elif event_type == EVT_FUNCTION_ARGS_DONE:
            try:
                arguments = json.loads(event.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = None
            result = (
                handler.on_tool_call(call_id, event.get("name", ""), arguments)
                if isinstance(arguments, dict)
                else {"error": "invalid_arguments", "message": "Arguments must be a JSON object."}
            )
            await ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": event.get("call_id"),
                            "output": json.dumps(result),
                        },
                    }
                )
            )
            if result.get(CONTROL_KEY) == CONTROL_PAUSE:
                # The model already said it can't share that (prompt rule 3); a hold
                # that is already in place is not repeated.
                await self._hold(ws, call, None)
            elif not call.paused:
                await ws.send(json.dumps({"type": "response.create"}))
        elif event_type in EVT_OUTPUT_TRANSCRIPT_DONE:
            verdict = handler.on_agent_utterance(call_id, event.get("transcript", ""))
            if not verdict.allowed:
                await self._say(ws, verdict.text, interrupt=True)
        elif event_type == EVT_ERROR:
            log.error("xAI realtime error", extra={"fields": {"call_id": call_id, "event": event}})

    async def _greet(self, ws: Any, call: _LiveCall) -> None:
        """Speak as soon as the call is connected. Do not wait for the other person."""
        if call.greeted or call.paused:
            return
        call.greeted = True
        instructions = call.config.metadata.get("greeting") or DEFAULT_GREETING
        await ws.send(
            json.dumps({"type": "response.create", "response": {"instructions": instructions}})
        )

    async def _say(self, ws: Any, text: str, *, interrupt: bool) -> None:
        """Make the agent say an exact line. VERIFY the recommended pattern."""
        if interrupt:
            await ws.send(json.dumps({"type": "response.cancel"}))
        await ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {"instructions": f"Say exactly this and nothing else: {text}"},
                }
            )
        )

    async def _hold(self, ws: Any, call: _LiveCall, line: str | None) -> None:
        """Stop replying on its own. Idempotent: one hold line per pause."""
        if call.paused:
            return
        call.paused = True
        if line:
            await self._say(ws, line, interrupt=True)
        await ws.send(json.dumps(self._auto_response(enabled=False)))

    @staticmethod
    def _auto_response(*, enabled: bool) -> dict[str, Any]:
        """Stop/start the server replying on its own while a human is needed.

        VERIFY against current docs: the turn-detection flag that disables automatic
        responses (OpenAI-compatible APIs use ``turn_detection.create_response``).
        """
        return {
            "type": "session.update",
            "session": {"turn_detection": {"type": "server_vad", "create_response": enabled}},
        }

    async def _consume_commands(self, ws: Any, call: _LiveCall) -> None:
        assert call.commands is not None
        while True:
            command = await call.commands.get()
            if command == "pause":
                await self._hold(ws, call, HOLD_LINE)
            elif command == "greet":
                await self._greet(ws, call)
            elif command.startswith("resume"):
                if not call.paused:
                    continue  # duplicate resume: say nothing twice
                call.paused = False
                note = command.partition(":")[2] or RESUME_VERIFIED
                await ws.send(json.dumps(self._auto_response(enabled=True)))
                await ws.send(
                    json.dumps(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "system",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": RESUME_NOTES.get(
                                            note, RESUME_NOTES[RESUME_VERIFIED]
                                        ),
                                    }
                                ],
                            },
                        }
                    )
                )
                await ws.send(json.dumps({"type": "response.create"}))
            elif command == "close":
                await ws.close()
                return

    def _command(self, call_id: str, command: str) -> None:
        with self._lock:
            call = self._calls.get(call_id)
            if call is None:
                if command == "greet":
                    self._early.setdefault(call_id, []).append(command)
                return
        if call.loop is None or call.commands is None:
            call.pending_commands.append(command)
            return
        call.loop.call_soon_threadsafe(call.commands.put_nowait, command)


def _check() -> None:
    """Open a session, send session.update, print a few server events, close."""
    from ...config import get_settings

    settings = get_settings()
    if not settings.xai_api_key:
        raise SystemExit("Set XAI_API_KEY in .env first (docs/SETUP_KEYS.md#4-xai-grok-voice).")

    async def run() -> None:
        import websockets

        adapter = XaiRealtimeVoiceAdapter(
            api_key=settings.xai_api_key,
            url=settings.xai_realtime_url,
            model=settings.xai_voice_model or None,
            voice=settings.xai_voice_name or None,
        )
        url = f"{settings.xai_realtime_url}" + (
            f"?model={settings.xai_voice_model}" if settings.xai_voice_model else ""
        )
        headers = {"Authorization": f"Bearer {settings.xai_api_key}"}
        async with websockets.connect(url, additional_headers=headers) as ws:
            config = VoiceSessionConfig(
                call_id="check",
                case_id="check",
                instructions="Connectivity check. Do not speak.",
                tools=[],
                case_context=None,  # type: ignore[arg-type]
            )
            await ws.send(json.dumps(adapter.session_update(config)))
            for _ in range(3):
                print(await asyncio.wait_for(ws.recv(), timeout=10))

    asyncio.run(run())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="xAI realtime voice connectivity check")
    parser.add_argument("--check", action="store_true", help="open a session and print events")
    if parser.parse_args().check:
        _check()
