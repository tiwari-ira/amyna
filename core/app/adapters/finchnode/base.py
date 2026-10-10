"""FinchNode adapter interface. Core is the only caller (agents use Core tools)."""

from abc import ABC, abstractmethod
from typing import Any

from claimshield_contracts.enums import RecordType
from claimshield_contracts.models import Claim, HealthRecord, PatientRef

from ...errors import Conflict


class FinchNodeAdapter(ABC):
    """Read-only, patient-authorized health data. Callers must check consent first."""

    mode: str
    #: True when patients link through FinchNode's hosted Connect sessions.
    supports_connect: bool = False

    @abstractmethod
    def get_patient_profile(self, patient_id: str) -> PatientRef | None:
        """Demographics/coverage used to enrich a case after consent."""

    @abstractmethod
    def get_patient_claims(self, patient_id: str) -> list[Claim]: ...

    @abstractmethod
    def get_patient_encounters(self, patient_id: str) -> list[HealthRecord]: ...

    @abstractmethod
    def get_patient_procedures(self, patient_id: str) -> list[HealthRecord]: ...

    @abstractmethod
    def get_patient_medications(self, patient_id: str) -> list[HealthRecord]: ...

    def record_gaps(self, patient_id: str) -> dict[RecordType, str]:
        """Record types FinchNode could not provide completely, with the reason."""
        return {}

    def claims_gap(self, patient_id: str) -> str | None:
        """Why the claims category may be incomplete (None when complete)."""
        return None

    def invalidate(self, patient_id: str | None = None) -> None:  # noqa: B027 (optional hook)
        """Drop cached records (after FinchNode says they changed). Default: no cache."""
        return None

    # -- hosted Connect (sandbox / production) -----------------------------------

    def create_connect_session(self, **kwargs: Any) -> dict[str, Any]:
        raise Conflict(
            f"FINCHNODE_MODE={self.mode} links patients by typed id, not Connect.",
            code="connect_unavailable",
        )

    def get_connect_session(self, session_id: str) -> dict[str, Any]:
        raise Conflict(
            f"FINCHNODE_MODE={self.mode} has no Connect sessions.", code="connect_unavailable"
        )
