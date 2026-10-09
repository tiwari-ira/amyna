"""FETCH_MODE=agentverse: trigger the orchestrator's REST endpoint; agents write
results back through /internal/tools/*, so Core answers ``pending`` now.

# VERIFY against current docs: uAgents REST handlers (``@agent.on_rest_post``)
# are served on the agent's local port (8000); confirm the path and that REST
# stays enabled when ``mailbox=True`` (uagents 0.25.5 keeps the server running
# when REST handlers exist -- checked in the installed package source).
"""

import httpx

from claimshield_contracts.agent_messages import (
    TRIGGER_CLAIM_ANALYSIS,
    TRIGGER_DISPUTE_PREPARE,
    TRIGGER_VISIT_EVIDENCE,
    OrchestratorTrigger,
)
from claimshield_contracts.enums import WorkStatus

from ...errors import UpstreamError
from .base import BridgeResult, FetchBridge

TRIGGER_PATH = "/trigger"


class AgentverseFetchBridge(FetchBridge):
    mode = "agentverse"

    def __init__(self, orchestrator_url: str, token: str, http: httpx.Client | None = None) -> None:
        self._url = orchestrator_url.rstrip("/") + TRIGGER_PATH
        self._token = token
        self._http = http or httpx.Client(timeout=10.0)

    def _trigger(self, kind: str, case_id: str, **ids: str) -> BridgeResult:
        body = OrchestratorTrigger(kind=kind, case_id=case_id, auth_token=self._token, **ids)
        try:
            response = self._http.post(self._url, json=body.dict())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"Orchestrator trigger failed ({type(exc).__name__}). Is `make agents` running?",
                code="orchestrator_unreachable",
            ) from exc
        ack = response.json()
        if not ack.get("accepted"):
            raise UpstreamError(f"Orchestrator rejected trigger: {ack.get('error')}")
        return BridgeResult(status=WorkStatus.PENDING, detail=ack.get("request_id"))

    def extract_visit_evidence(self, case_id: str, visit_id: str) -> BridgeResult:
        return self._trigger(TRIGGER_VISIT_EVIDENCE, case_id, visit_id=visit_id)

    def analyze_claim(self, case_id: str, claim_id: str) -> BridgeResult:
        return self._trigger(TRIGGER_CLAIM_ANALYSIS, case_id, claim_id=claim_id)

    def prepare_dispute(self, case_id: str) -> BridgeResult:
        return self._trigger(TRIGGER_DISPUTE_PREPARE, case_id)
