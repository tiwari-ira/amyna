"""Core -> Fetch bridges: local_direct (in-process logic) | agentverse (REST trigger)."""

from typing import TYPE_CHECKING

from claimshield_contracts.enums import ServiceName

from .base import FetchBridge

if TYPE_CHECKING:
    from ...container import Container


def build_fetch_bridge(container: "Container") -> FetchBridge:
    settings = container.settings
    if settings.fetch_mode == "agentverse":
        from .live import AgentverseFetchBridge

        return AgentverseFetchBridge(
            settings.orchestrator_url, settings.service_tokens()[ServiceName.FETCH]
        )
    from .mock import LocalDirectFetchBridge

    return LocalDirectFetchBridge(container)
