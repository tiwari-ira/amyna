"""Patient notifier interface (Core -> patient, via the Relay service)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from claimshield_contracts.models import Case, PatientNotification


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    ref: str | None = None
    error: str | None = None


class PatientNotifier(ABC):
    """Delivers a notification already stored in Core's outbox."""

    mode: str

    @abstractmethod
    def send(self, notification: PatientNotification, case: Case) -> DeliveryResult:
        """Must not raise for delivery failures; return ``delivered=False`` instead."""
