"""Shared builder for the internal specialist agents (Visit / Claim / Dispute).

Specialists are NOT published (no chat protocol, no manifest, no README); only the
orchestrator is discoverable. They answer ONLY the orchestrator's address (case
data is PHI), and they always reply -- an unexpected error becomes ``ok=False``
instead of a silent timeout. Each request runs in a worker thread because the
logic makes blocking HTTPS calls to Core.
"""

import asyncio
import logging
from collections.abc import Callable

from uagents import Agent, Context, Model

from claimshield_contracts.enums import AgentName

from .core_client import CoreClient
from .protocols import protocol
from .settings import AgentSettings, require_seed

Handler = Callable[[CoreClient, Model], Model]
ErrorReply = Callable[[Model, str], Model]
log = logging.getLogger("claimshield.agents")


def build_specialist(
    *,
    name: str,
    agent_name: AgentName,
    seed_env: str,
    port: int,
    protocol_name: str,
    request_model: type[Model],
    response_model: type[Model],
    handle: Handler,
    on_error: ErrorReply,
    settings: AgentSettings,
    orchestrator_address: str | None,
    tools: CoreClient | None = None,
    mailbox: bool = True,
    agentverse: str | None = None,
) -> Agent:
    if mailbox and not orchestrator_address:
        raise RuntimeError(
            f"{name} is reachable through Agentverse but ORCHESTRATOR_ADDRESS is not set; "
            "refusing to answer arbitrary senders. Run `make agent-addresses`."
        )
    agent = Agent(
        name=name,
        seed=require_seed(seed_env),
        port=port,
        mailbox=mailbox,
        agentverse=agentverse,
        publish_agent_details=False,
    )
    client = tools or CoreClient(settings.core_base_url, settings.fetch_token, agent=agent_name)
    proto = protocol(protocol_name)

    @proto.on_message(request_model, replies=response_model)
    async def on_request(ctx: Context, sender: str, msg: Model) -> None:
        if orchestrator_address and sender != orchestrator_address:
            ctx.logger.warning(f"ignoring {request_model.__name__} from an unknown sender")
            return
        try:
            response = await asyncio.to_thread(handle, client.for_request(msg.request_id), msg)
        except Exception as exc:  # never leave the orchestrator waiting for a timeout
            log.exception("%s failed", request_model.__name__)
            response = on_error(msg, f"internal error ({type(exc).__name__})")
        await ctx.send(sender, response)

    agent.include(proto)
    return agent
