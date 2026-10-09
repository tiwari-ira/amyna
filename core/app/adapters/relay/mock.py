"""NOTIFY_MODE=outbox: notifications stay in Core's DB (GET /cases/{id}/notifications)."""

import logging

from claimshield_contracts.models import Case, PatientNotification

from .base import DeliveryResult, PatientNotifier

log = logging.getLogger("claimshield.notify")


class OutboxNotifier(PatientNotifier):
    mode = "outbox"

    def send(self, notification: PatientNotification, case: Case) -> DeliveryResult:
        log.info(
            "patient notification stored in outbox",
            extra={"fields": {"case_id": case.case_id, "kind": notification.kind.value}},
        )
        return DeliveryResult(delivered=True, ref=f"outbox:{notification.notification_id}")
