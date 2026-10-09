"""Core -> Fetch agent work bridge.

``local_direct`` runs fetch/agents/logic in-process; ``agentverse`` triggers the
orchestrator over REST and agents write results back via /internal/tools/*.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from claimshield_contracts.enums import WorkStatus


@dataclass(frozen=True)
class BridgeResult:
    status: WorkStatus
    detail: str | None = None


class FetchBridge(ABC):
    mode: str

    @abstractmethod
    def extract_visit_evidence(self, case_id: str, visit_id: str) -> BridgeResult: ...

    @abstractmethod
    def analyze_claim(self, case_id: str, claim_id: str) -> BridgeResult: ...

    @abstractmethod
    def prepare_dispute(self, case_id: str) -> BridgeResult: ...
