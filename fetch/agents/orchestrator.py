"""Amyna orchestrator: the only PUBLIC agent (ASI:One-discoverable).

Thin uAgents wrapper around ``logic/orchestrate.py``:
- chat protocol (ChatMessage / ChatAcknowledgement), manifest published;
  every ChatMessage is acknowledged first;
- typed requests to the Visit / Claim / Dispute agents, replies correlated by
  ``request_id`` in ``ctx.storage``; an interval handler enforces timeouts;
- REST ``POST /trigger`` for Core (FETCH_MODE=agentverse);
- HELLO_WORLD=1 replies only "Hello from Amyna" (validate discovery first).

Run:  python -m fetch.agents.orchestrator    (needs ORCHESTRATOR_SEED)
"""

import asyncio
import hmac
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from uagents import Agent, Context, Protocol

# VERIFY against current docs: chat protocol import path (uagents-core 0.4.x).
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

from claimshield_contracts.agent_messages import (
    ClaimAnalysisResponse,
    DisputeResponse,
    OrchestratorTrigger,
    OrchestratorTriggerAck,
    VisitEvidenceResponse,
)
from claimshield_contracts.enums import AgentName

from .core_client import CoreClient
from .logic.orchestrate import AgentResponse, ChatOrchestrator, Step, trigger_to_outbound
from .protocols import REPLIES_PROTOCOL, protocol
from .session import (
    CORE_TRIGGER_OWNER,
    ChatSession,
    forget_request,
    load_session,
    owner_of_request,
    pending_agent,
    pending_senders,
    register_request,
    save_session,
)
from .settings import AgentSettings, require_seed

PORT = 8000
HELLO_REPLY = "Hello from Amyna"
PROFILE_PATH = (
    Path(__file__).resolve().parents[1] / "agentverse" / "agent_profiles" / "orchestrator.md"
)
DESCRIPTION = (
    "Amyna helps patients review healthcare bills by comparing billed lines against "
    "patient-authorized health records and visit evidence, identifying discrepancies and "
    "preparing evidence-backed dispute packages."
)
TIMEOUT_CHECK_S = 5.0
ERROR_REPLY = "Sorry, something went wrong on my side. Reply STATUS to see where your case is."


def chat_text(text: str, *, end_session: bool = False) -> ChatMessage:
    content: list = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=content)


def build_orchestrator(
    settings: AgentSettings | None = None,
    *,
    tools: CoreClient | None = None,
    addresses: dict[AgentName, str] | None = None,
    mailbox: bool = True,
    publish: bool = True,
    agentverse: str | None = None,
) -> Agent:
    """Build the public orchestrator. Offline runs pass mailbox/publish False + agentverse."""
    settings = settings or AgentSettings.from_env()
    agent = Agent(
        name="amyna-orchestrator",
        seed=require_seed("ORCHESTRATOR_SEED"),
        port=PORT,
        mailbox=mailbox,
        agentverse=agentverse,
        readme_path=str(PROFILE_PATH),
        description=DESCRIPTION,
        handle=settings.orchestrator_handle,
        publish_agent_details=publish,
    )
    client = tools or CoreClient(
        settings.core_base_url, settings.fetch_token, agent=AgentName.ORCHESTRATOR
    )
    brain = ChatOrchestrator(client, timeout_s=settings.request_timeout_s)
    routes = addresses or {
        AgentName.VISIT_AGENT: settings.visit_agent_address,
        AgentName.CLAIM_AGENT: settings.claim_agent_address,
        AgentName.DISPUTE_AGENT: settings.dispute_agent_address,
    }

    async def execute(ctx: Context, session: ChatSession, step: Step) -> None:
        """Send typed requests, persist the session, then reply in chat."""
        replies = list(step.replies)
        for outbound in step.outbound:
            address = routes.get(outbound.agent)
            if not address:
                replies.append(f"Configuration error: no address for {outbound.agent.value}.")
                continue
            await ctx.send(address, outbound.message)
        save_session(ctx.storage, session)
        for index, reply in enumerate(replies):
            last = index == len(replies) - 1
            await ctx.send(session.sender, chat_text(reply, end_session=step.end_session and last))

    # ---------------------------------------------------------------- chat (public)
    chat = Protocol(spec=chat_protocol_spec)

    # One lock per chat sender: message handlers and the timeout sweep never
    # interleave on the same session (to_thread() yields the event loop).
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        await ctx.send(
            sender, ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=msg.msg_id)
        )
        if settings.hello_world:
            await ctx.send(sender, chat_text(HELLO_REPLY))
            return
        text = msg.text()
        if not text.strip():
            return
        async with locks[sender]:
            session = load_session(ctx.storage, sender)
            try:
                step = await asyncio.to_thread(brain.on_user_message, session, text)
            except Exception:  # the user must always get an answer
                ctx.logger.exception("chat handling failed")
                step = Step([ERROR_REPLY])
            await execute(ctx, session, step)

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        ctx.logger.debug(f"chat ack for {msg.acknowledged_msg_id}")

    agent.include(chat, publish_manifest=publish)

    # ---------------------------------------------------------------- specialist replies
    replies = protocol(REPLIES_PROTOCOL)

    async def on_reply(ctx: Context, sender: str, msg: AgentResponse) -> None:
        owner = owner_of_request(ctx.storage, msg.request_id)
        if owner is None:
            ctx.logger.info(f"ignoring unsolicited {type(msg).__name__}")
            return
        if owner == CORE_TRIGGER_OWNER:
            ctx.logger.info(f"{type(msg).__name__} for a Core trigger (ok={msg.ok})")
            forget_request(ctx.storage, msg.request_id)
            return
        async with locks[owner]:
            session = load_session(ctx.storage, owner)
            agent_name = pending_agent(session, msg.request_id)
            if agent_name is None or routes.get(AgentName(agent_name)) != sender:
                ctx.logger.warning(f"ignoring {type(msg).__name__} from an unexpected sender")
                return
            try:
                step = await asyncio.to_thread(brain.on_agent_response, session, msg)
            except Exception:
                ctx.logger.exception("agent reply handling failed")
                step = Step([ERROR_REPLY])
            await execute(ctx, session, step)

    for model in (VisitEvidenceResponse, ClaimAnalysisResponse, DisputeResponse):
        replies.on_message(model)(on_reply)
    agent.include(replies)

    @agent.on_interval(period=TIMEOUT_CHECK_S)
    async def check_timeouts(ctx: Context) -> None:
        for sender in pending_senders(ctx.storage):
            async with locks[sender]:
                session = load_session(ctx.storage, sender)
                step = brain.on_timeouts(session)
                if step.replies:
                    await execute(ctx, session, step)

    # ---------------------------------------------------------------- Core trigger (REST)
    @agent.on_rest_post("/trigger", OrchestratorTrigger, OrchestratorTriggerAck)
    async def on_trigger(ctx: Context, req: OrchestratorTrigger) -> OrchestratorTriggerAck:
        if not hmac.compare_digest(req.auth_token.encode(), settings.fetch_token.encode()):
            return OrchestratorTriggerAck(accepted=False, error="unauthorized")
        try:
            outbound = trigger_to_outbound(req)
        except ValueError as exc:
            return OrchestratorTriggerAck(accepted=False, error=str(exc))
        address = routes.get(outbound.agent)
        if not address:
            return OrchestratorTriggerAck(accepted=False, error=f"no address for {outbound.agent}")
        register_request(ctx.storage, outbound.message.request_id, CORE_TRIGGER_OWNER)
        await ctx.send(address, outbound.message)
        return OrchestratorTriggerAck(accepted=True, request_id=outbound.message.request_id)

    return agent


if __name__ == "__main__":
    build_orchestrator().run()
