"""Patient notifier adapters: outbox (mock) | relay_service (live)."""

from claimshield_contracts.enums import ServiceName

from ...config import Settings
from .base import PatientNotifier


def build_notifier(settings: Settings) -> PatientNotifier:
    if settings.notify_mode == "relay_service":
        from .live import RelayServiceNotifier

        return RelayServiceNotifier(
            settings.relay_service_url, settings.service_tokens()[ServiceName.RELAY]
        )
    from .mock import OutboxNotifier

    return OutboxNotifier()
