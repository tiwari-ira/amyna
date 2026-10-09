"""NOTIFY_MODE=relay_service: forward notifications to the Relay service.

The Relay service (relay/, Member 2) owns every Relay SDK call; Core only POSTs
``{notification, case}`` to ``RELAY_SERVICE_URL/internal/notify`` with the Relay
service token. Delivery failures never fail the Core transaction.
"""

import logging

import httpx

from claimshield_contracts.models import Case, PatientNotification

from .base import DeliveryResult, PatientNotifier

log = logging.getLogger("claimshield.notify")


class RelayServiceNotifier(PatientNotifier):
    mode = "relay_service"

    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None) -> None:
        self._url = base_url.rstrip("/") + "/internal/notify"
        self._token = token
        self._http = http or httpx.Client(timeout=5.0)

    def send(self, notification: PatientNotification, case: Case) -> DeliveryResult:
        payload = {
            "notification": notification.model_dump(mode="json"),
            "case": {
                "case_id": case.case_id,
                "channel": case.channel.value,
                "finchnode_patient_id": case.patient.finchnode_patient_id,
            },
        }
        try:
            response = self._http.post(
                self._url, json=payload, headers={"Authorization": f"Bearer {self._token}"}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning(
                "relay service delivery failed",
                extra={"fields": {"case_id": case.case_id, "error": type(exc).__name__}},
            )
            return DeliveryResult(delivered=False, error=type(exc).__name__)
        body = response.json()
        return DeliveryResult(delivered=bool(body.get("delivered", True)), ref=body.get("delivery_ref"))
