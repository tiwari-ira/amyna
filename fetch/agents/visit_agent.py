"""Visit Agent (internal): VisitEvidenceRequest -> VisitEvidenceResponse.

Run alone:  python -m fetch.agents.visit_agent   (needs VISIT_AGENT_SEED)
"""

from uagents import Agent

from claimshield_contracts.agent_messages import VisitEvidenceRequest, VisitEvidenceResponse
from claimshield_contracts.enums import AgentName
from intelligence.extraction.extractor import build_extractor

from .core_client import CoreClient
from .logic.visit_logic import VisitLogicDeps, handle_visit_evidence_request
from .protocols import VISIT_PROTOCOL
from .settings import AgentSettings
from .specialist import build_specialist

PORT = 8001


def build_visit_agent(
    settings: AgentSettings | None = None,
    *,
    tools: CoreClient | None = None,
    mailbox: bool = True,
    agentverse: str | None = None,
    orchestrator_address: str | None = None,
) -> Agent:
    settings = settings or AgentSettings.from_env()
    deps = VisitLogicDeps(
        extractor=build_extractor(
            settings.extraction_mode,
            ollama_url=settings.ollama_url,
            ollama_model=settings.ollama_model,
        )
    )
    return build_specialist(
        name="amyna-visit-agent",
        agent_name=AgentName.VISIT_AGENT,
        seed_env="VISIT_AGENT_SEED",
        port=PORT,
        protocol_name=VISIT_PROTOCOL,
        request_model=VisitEvidenceRequest,
        response_model=VisitEvidenceResponse,
        handle=lambda client, msg: handle_visit_evidence_request(client, msg, deps),
        on_error=lambda msg, error: VisitEvidenceResponse(
            request_id=msg.request_id, case_id=msg.case_id, ok=False, error=error
        ),
        settings=settings,
        orchestrator_address=orchestrator_address or settings.orchestrator_address,
        tools=tools,
        mailbox=mailbox,
        agentverse=agentverse,
    )


if __name__ == "__main__":
    build_visit_agent().run()
