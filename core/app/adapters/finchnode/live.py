"""FINCHNODE_MODE=demo|sandbox: FinchNode HTTP API via finchnode.client.

All endpoint/auth details live in finchnode/client.py (tagged VERIFY there).
"""

from finchnode.client import HttpFinchNodeClient

from .tools_adapter import FinchNodeToolsAdapter


class LiveFinchNodeAdapter(FinchNodeToolsAdapter):
    def __init__(self, *, mode: str, base_url: str, api_key: str | None) -> None:
        super().__init__(
            HttpFinchNodeClient(mode=mode, base_url=base_url, api_key=api_key), mode=mode
        )
