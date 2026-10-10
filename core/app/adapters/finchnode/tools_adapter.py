"""FinchNodeAdapter backed by the ``finchnode`` library (any client: fixtures or HTTP).

FinchNode and data failures surface as typed domain errors, never a raw 500:

* ``finchnode_consent_inactive`` (409) -- the patient's sharing ended; they must reconnect
* ``finchnode_unavailable`` (502)      -- outage, timeout or rate limit; retry later
* ``finchnode_not_authorized`` (502)   -- FINCHNODE_API_KEY is wrong for this environment
* ``finchnode_request_rejected`` (502) -- FinchNode refused the request itself
* ``finchnode_bad_data`` (502)         -- a record could not be normalized
"""

import logging
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
from pydantic import ValidationError

from claimshield_contracts.enums import RecordType
from claimshield_contracts.models import Claim, HealthRecord, PatientRef
from finchnode import tools
from finchnode.client import FinchNodeConsentInactive, FinchNodeError

from ...errors import Conflict, UpstreamError
from .base import FinchNodeAdapter

T = TypeVar("T")
log = logging.getLogger("claimshield.finchnode")


class FinchNodeToolsAdapter(FinchNodeAdapter):
    def __init__(self, client: Any, mode: str) -> None:
        self._client = client
        self.mode = mode

    @staticmethod
    def _guard(call: Callable[[], T]) -> T:
        try:
            return call()
        except FinchNodeConsentInactive as exc:
            raise Conflict(
                "This patient's FinchNode sharing has ended. They need to reconnect "
                "their health records.",
                code="finchnode_consent_inactive",
            ) from exc
        except FinchNodeError as exc:
            log.warning("FinchNode request failed", extra={"fields": {"code": exc.code}})
            message = (
                "FinchNode is unavailable right now."
                if exc.code == "finchnode_unavailable"
                else "FinchNode refused the request."
            )
            raise UpstreamError(message, code=exc.code) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "FinchNode is unavailable right now.", code="finchnode_unavailable"
            ) from exc
        except (ValidationError, ValueError, KeyError, TypeError, IndexError) as exc:
            log.warning(
                "FinchNode record could not be normalized",
                extra={"fields": {"error": type(exc).__name__}},
            )
            raise UpstreamError(
                "FinchNode returned a record Amyna could not read.", code="finchnode_bad_data"
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

    def record_gaps(self, patient_id: str) -> dict[RecordType, str]:
        return self._guard(lambda: tools.record_gaps(self._client, patient_id))

    def claims_gap(self, patient_id: str) -> str | None:
        return self._guard(lambda: tools.claims_gap(self._client, patient_id))

    def invalidate(self, patient_id: str | None = None) -> None:
        reset = getattr(self._client, "invalidate", None)
        if reset is not None:
            reset(patient_id)
