"""Claim Agent (internal): ClaimAnalysisRequest -> ClaimAnalysisResponse.

Run alone:  python -m fetch.agents.claim_agent   (needs CLAIM_AGENT_SEED)
"""

from uagents import Agent

from claimshield_contracts.agent_messages import ClaimAnalysisRequest, ClaimAnalysisResponse
from claimshield_contracts.enums import AgentName
from intelligence.discrepancy.engine import EngineConfig

from .core_client import CoreClient
from .logic.claim_logic import ClaimLogicDeps, handle_claim_analysis_request
from .protocols import CLAIM_PROTOCOL
from .settings import AgentSettings
from .specialist import build_specialist

PORT = 8002


def build_claim_agent(
    settings: AgentSettings | None = None,
    *,
    tools: CoreClient | None = None,
    mailbox: bool = True,
    agentverse: str | None = None,
    orchestrator_address: str | None = None,
) -> Agent:
    settings = settings or AgentSettings.from_env()
    deps = ClaimLogicDeps(
        config=EngineConfig(confirmation_threshold=settings.confirmation_threshold)
    )
    return build_specialist(
        name="amyna-claim-agent",
        agent_name=AgentName.CLAIM_AGENT,
        seed_env="CLAIM_AGENT_SEED",
        port=PORT,
        protocol_name=CLAIM_PROTOCOL,
        request_model=ClaimAnalysisRequest,
        response_model=ClaimAnalysisResponse,
        handle=lambda client, msg: handle_claim_analysis_request(client, msg, deps),
        on_error=lambda msg, error: ClaimAnalysisResponse(
            request_id=msg.request_id, case_id=msg.case_id, ok=False, error=error
        ),
        settings=settings,
        orchestrator_address=orchestrator_address or settings.orchestrator_address,
        tools=tools,
        mailbox=mailbox,
        agentverse=agentverse,
    )


if __name__ == "__main__":
    build_claim_agent().run()
