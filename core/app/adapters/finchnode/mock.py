"""FINCHNODE_MODE=mock: synthetic fixtures from finchnode/fixtures (no network)."""

from finchnode.client import FixtureClient

from .tools_adapter import FinchNodeToolsAdapter


class MockFinchNodeAdapter(FinchNodeToolsAdapter):
    def __init__(self) -> None:
        super().__init__(FixtureClient(), mode="mock")
