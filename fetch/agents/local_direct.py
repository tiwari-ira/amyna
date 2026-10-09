"""LOCAL_DIRECT mode: the full ASI:One workflow with no Agentverse.

The orchestrator logic and the Visit/Claim/Dispute logic run in this process;
Core is reached over HTTP exactly like the real agents (each specialist uses its
own agent identity, so Core's per-agent allowlist still applies).

    make chat                                   # interactive, needs Core running
    python -m fetch.agents.local_direct --script demo
"""

import argparse

import httpx

from claimshield_contracts.agent_messages import (
    ClaimAnalysisRequest,
    DisputeRequest,
    VisitEvidenceRequest,
)
from claimshield_contracts.enums import AgentName
from intelligence.discrepancy.engine import EngineConfig
from intelligence.extraction.extractor import build_extractor

from .core_client import CoreClient
from .logic.claim_logic import ClaimLogicDeps, handle_claim_analysis_request
from .logic.dispute_logic import handle_dispute_request
from .logic.orchestrate import AgentResponse, ChatOrchestrator, Outbound, Step
from .logic.visit_logic import VisitLogicDeps, handle_visit_evidence_request
from .session import ChatSession, MemoryStore, load_session, save_session
from .settings import AgentSettings

DEMO_CONVERSATION = (
    "Hi Amyna, can you review my bill? My FinchNode patient ID is fn_pat_demo_001 "
    "and the claim is CLM-2026-0915-001.",
    "Yes",
    "No",
    "Not sure",
    "Yes",
    "APPROVE",
    "Done",
)
AGENTS = (
    AgentName.ORCHESTRATOR,
    AgentName.VISIT_AGENT,
    AgentName.CLAIM_AGENT,
    AgentName.DISPUTE_AGENT,
)


class LocalDirectRunner:
    """One simulated ASI:One user talking to the orchestrator, specialists in-process."""

    def __init__(
        self,
        *,
        settings: AgentSettings | None = None,
        http: httpx.Client | None = None,
        sender: str = "local-direct-user",
    ) -> None:
        settings = settings or AgentSettings.from_env()
        shared = http or httpx.Client(base_url=settings.core_base_url, timeout=60.0)
        self._clients = {
            agent: CoreClient("", settings.fetch_token, agent=agent, http=shared)
            for agent in AGENTS
        }
        self.orchestrator = ChatOrchestrator(
            self._clients[AgentName.ORCHESTRATOR], timeout_s=settings.request_timeout_s
        )
        self._visit_deps = VisitLogicDeps(
            extractor=build_extractor(
                settings.extraction_mode,
                ollama_url=settings.ollama_url,
                ollama_model=settings.ollama_model,
            )
        )
        self._claim_deps = ClaimLogicDeps(
            config=EngineConfig(confirmation_threshold=settings.confirmation_threshold)
        )
        self.store = MemoryStore()
        self.sender = sender

    @property
    def session(self) -> ChatSession:
        return load_session(self.store, self.sender)

    def send(self, text: str) -> list[str]:
        """Deliver one user message; returns every chat reply it produced."""
        session = load_session(self.store, self.sender)
        replies = self._drain(session, self.orchestrator.on_user_message(session, text))
        save_session(self.store, session)
        return replies

    def _drain(self, session: ChatSession, step: Step) -> list[str]:
        replies = list(step.replies)
        queue = list(step.outbound)
        while queue:
            follow = self.orchestrator.on_agent_response(session, self._deliver(queue.pop(0)))
            replies.extend(follow.replies)
            queue.extend(follow.outbound)
        return replies

    def _deliver(self, outbound: Outbound) -> AgentResponse:
        message = outbound.message
        tools = self._clients[outbound.agent].for_request(message.request_id)
        if isinstance(message, VisitEvidenceRequest):
            return handle_visit_evidence_request(tools, message, self._visit_deps)
        if isinstance(message, ClaimAnalysisRequest):
            return handle_claim_analysis_request(tools, message, self._claim_deps)
        if isinstance(message, DisputeRequest):
            return handle_dispute_request(tools, message)
        raise TypeError(f"unsupported message {type(message).__name__}")


def _print(replies: list[str]) -> None:
    for reply in replies:
        print("\nAmyna>", reply.replace("\n", "\n             "))


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with Amyna locally (no Agentverse)")
    parser.add_argument("--script", choices=["demo"], help="run the canned demo conversation")
    args = parser.parse_args()
    runner = LocalDirectRunner()
    if args.script == "demo":
        for text in DEMO_CONVERSATION:
            print(f"\nyou> {text}")
            _print(runner.send(text))
        return
    print("Amyna local chat (Ctrl-D to exit). Try:", DEMO_CONVERSATION[0])
    while True:
        try:
            text = input("\nyou> ").strip()
        except EOFError:
            print()
            return
        if text:
            _print(runner.send(text))


if __name__ == "__main__":
    main()
