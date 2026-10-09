"""FinchNodeAdapter backed by the ``finchnode`` library (any client: fixtures or HTTP).

Transport/remote failures surface as UpstreamError (HTTP 502), never a raw 500.
"""

from collections.abc import Callable
from typing import TypeVar

import httpx

from claimshield_contracts.models import Claim, HealthRecord, PatientRef
from finchnode import tools
from finchnode.client import FinchNodeClient, FinchNodeError

from ...errors import UpstreamError
from .base import FinchNodeAdapter

T = TypeVar("T")


class FinchNodeToolsAdapter(FinchNodeAdapter):
    def __init__(self, client: FinchNodeClient, mode: str) -> None:
        self._client = client
        self.mode = mode

    @staticmethod
    def _guard(call: Callable[[], T]) -> T:
        try:
            return call()
        except (FinchNodeError, httpx.HTTPError) as exc:
            raise UpstreamError(
                "FinchNode is unavailable right now.", code="finchnode_unavailable"
            ) from exc

    def get_patient_profile(self, patient_id: str) -> PatientRef | None:
        return self._guard(lambda: tools.get_patient_profile(self._client, patient_id))

    def get_patient_claims(self, patient_id: str) -> list[Claim]:
        return self._guard(lambda: tools.get_patient_claims(self._client, patient_id))

    def get_patient_encounters(self, patient_id: str) -> list[HealthRecord]:
        return self._guard(lambda: tools.get_patient_encounters(self._client, patient_id))

    def get_patient_procedures(self, patient_id: str) -> list[HealthRecord]:
        return self._guard(lambda: tools.get_patient_procedures(self._client, patient_id))

    def get_patient_medications(self, patient_id: str) -> list[HealthRecord]:
        return self._guard(lambda: tools.get_patient_medications(self._client, patient_id))
