"""Dispute Agent (internal): DisputeRequest -> DisputeResponse.

Prepares the dispute package and *requests* the insurer call from Core
(``start_insurance_call``). Core owns the call; audio never touches this agent.

Run alone:  python -m fetch.agents.dispute_agent   (needs DISPUTE_AGENT_SEED)
"""

from uagents import Agent

from claimshield_contracts.agent_messages import DisputeRequest, DisputeResponse
from claimshield_contracts.enums import AgentName

from .core_client import CoreClient
from .logic.dispute_logic import handle_dispute_request
from .protocols import DISPUTE_PROTOCOL
from .settings import AgentSettings
from .specialist import build_specialist

PORT = 8003


def build_dispute_agent(
    settings: AgentSettings | None = None,
    *,
    tools: CoreClient | None = None,
    mailbox: bool = True,
    agentverse: str | None = None,
    orchestrator_address: str | None = None,
) -> Agent:
    settings = settings or AgentSettings.from_env()
    return build_specialist(
        name="amyna-dispute-agent",
        agent_name=AgentName.DISPUTE_AGENT,
        seed_env="DISPUTE_AGENT_SEED",
        port=PORT,
        protocol_name=DISPUTE_PROTOCOL,
        request_model=DisputeRequest,
        response_model=DisputeResponse,
        handle=handle_dispute_request,
        on_error=lambda msg, error: DisputeResponse(
            request_id=msg.request_id, case_id=msg.case_id, ok=False, error=error, action=msg.action
        ),
        settings=settings,
        orchestrator_address=orchestrator_address or settings.orchestrator_address,
        tools=tools,
        mailbox=mailbox,
        agentverse=agentverse,
    )


if __name__ == "__main__":
    build_dispute_agent().run()
