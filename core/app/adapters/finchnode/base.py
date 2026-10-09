"""FinchNode adapter interface. Core is the only caller (agents use Core tools)."""

from abc import ABC, abstractmethod

from claimshield_contracts.models import Claim, HealthRecord, PatientRef


class FinchNodeAdapter(ABC):
    """Read-only, patient-authorized health data. Callers must check consent first."""

    mode: str

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
