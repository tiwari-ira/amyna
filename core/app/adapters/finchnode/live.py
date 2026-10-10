"""FINCHNODE_MODE=demo|sandbox|production: FinchNode's HTTP API via finchnode.client.

Records come from the normalized ``/users/{subject}/records`` snapshot in every
mode. Sandbox and production also link patients through hosted Connect sessions.
"""

from typing import Any

from finchnode.client import HttpFinchNodeClient

from .tools_adapter import FinchNodeToolsAdapter


class LiveFinchNodeAdapter(FinchNodeToolsAdapter):
    def __init__(
        self,
        *,
        mode: str,
        base_url: str,
        api_key: str | None,
        client: HttpFinchNodeClient | None = None,
    ) -> None:
        super().__init__(
            client or HttpFinchNodeClient(mode=mode, base_url=base_url, api_key=api_key),
            mode=mode,
        )
        self.supports_connect = mode in ("sandbox", "production")

    def create_connect_session(self, **kwargs: Any) -> dict[str, Any]:
        if not self.supports_connect:
            return super().create_connect_session(**kwargs)
        return self._guard(lambda: self._client.create_connect_session(**kwargs))

    def get_connect_session(self, session_id: str) -> dict[str, Any]:
        if not self.supports_connect:
            return super().get_connect_session(session_id)
        return self._guard(lambda: self._client.get_connect_session(session_id))
