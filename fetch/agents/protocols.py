"""uAgents protocols between the orchestrator and the specialists (imports uagents).

Plain (non-spec) protocols: specialists register request handlers that declare
their reply type; the orchestrator registers handlers for the responses.
Messages are routed by schema digest (pinned in
shared/python/claimshield_contracts/fixtures/agent_message_digests.json).
"""

from uagents import Protocol

from claimshield_contracts.version import AGENT_PROTOCOL_VERSION

VISIT_PROTOCOL = "claimshield-visit-evidence"
CLAIM_PROTOCOL = "claimshield-claim-analysis"
DISPUTE_PROTOCOL = "claimshield-dispute"
REPLIES_PROTOCOL = "claimshield-orchestrator-replies"


def protocol(name: str) -> Protocol:
    return Protocol(name=name, version=AGENT_PROTOCOL_VERSION)
