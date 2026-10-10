"""FinchNode adapters: mock (fixtures) | demo | sandbox | production (one HTTP API)."""

from ...config import Settings
from .base import FinchNodeAdapter


def build_finchnode_adapter(settings: Settings) -> FinchNodeAdapter:
    if settings.finchnode_mode == "mock":
        from .mock import MockFinchNodeAdapter

        return MockFinchNodeAdapter()
    from .live import LiveFinchNodeAdapter

    return LiveFinchNodeAdapter(
        mode=settings.finchnode_mode,
        base_url=settings.finchnode_base_url,
        api_key=settings.finchnode_api_key or None,
    )
