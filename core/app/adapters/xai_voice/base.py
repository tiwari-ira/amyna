"""Voice session interface.

The adapter owns transport (realtime WebSocket / SIP media); Core's
``VoiceCallManager`` implements :class:`VoiceEventHandler` and owns every
decision: safety checks, tool execution, state transitions.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from claimshield_contracts.enums import TurnAction
from claimshield_contracts.tool_api import DisputeCaseView


@dataclass(frozen=True)
class TurnDecision:
    """Core's verdict on an insurer utterance."""

    action: TurnAction
    say: str | None = None
    intervention_id: str | None = None


@dataclass(frozen=True)
class GuardrailVerdict:
    """Core's verdict on something the agent is about to say (or just said)."""

    allowed: bool
    text: str
    violations: tuple[str, ...] = ()


class VoiceEventHandler(Protocol):
    """Callbacks the adapter invokes. Implemented by VoiceCallManager."""

    def on_insurer_utterance(self, call_id: str, text: str) -> TurnDecision: ...

    def on_agent_utterance(self, call_id: str, text: str) -> GuardrailVerdict: ...

    def on_tool_call(
        self, call_id: str, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...

    def on_call_ended(self, call_id: str, reason: str) -> None: ...


@dataclass(frozen=True)
class VoiceSessionConfig:
    call_id: str
    case_id: str
    instructions: str
    tools: list[dict[str, Any]]
    case_context: DisputeCaseView
    model: str | None = None
    voice: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    # SIP bridge mode (task 15): the xAI-side call id from the inbound
    # ``realtime.call.incoming`` webhook; when set, the realtime WS attaches with
    # ``?call_id=...`` instead of starting a fresh ``?model=...`` conversation.
    bridge_call_id: str | None = None


class VoiceAdapter(ABC):
    mode: str
    # True for SIP-bridged sessions: the WS only starts after the carrier leg
    # has arrived at the voice vendor and produced an inbound-call webhook.
    needs_sip_attach: bool = False

    @abstractmethod
    def start_session(self, config: VoiceSessionConfig, handler: VoiceEventHandler) -> None:
        """Begin the conversation. Mock runs synchronously until paused or ended."""

    @abstractmethod
    def resume_session(self, call_id: str, handler: VoiceEventHandler) -> None:
        """Continue after a human resolved the pause."""

    @abstractmethod
    def pause_session(self, call_id: str) -> None:
        """Stop speaking and hold (Core created a human intervention)."""

    @abstractmethod
    def end_session(self, call_id: str, reason: str) -> None: ...
