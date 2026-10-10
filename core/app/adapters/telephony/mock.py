"""TELEPHONY_MODE=mock: pretends to dial; records what it would have done."""

from .base import DialResult, TelephonyAdapter, ensure_allowed


class MockTelephony(TelephonyAdapter):
    mode = "mock"

    def __init__(self, allowed: frozenset[str]) -> None:
        self._allowed = allowed
        self.dialed: list[str] = []
        self.hung_up: list[str] = []
        self.human_dialed: list[str] = []
        self.human_numbers: list[str] = []
        self.bridged: list[tuple[str, str]] = []

    def dial(self, call_id: str, to_number: str) -> DialResult:
        ensure_allowed(to_number, self._allowed)
        self.dialed.append(call_id)
        return DialResult(provider_call_ref=f"MOCK-CA-{call_id[-8:]}", status="initiated")

    def hangup(self, provider_call_ref: str) -> None:
        self.hung_up.append(provider_call_ref)

    def dial_human(
        self,
        call_id: str,
        to_number: str,
        insurer_ref: str | None = None,
        *,
        extra_allowed: frozenset[str] = frozenset(),
    ) -> DialResult:
        ensure_allowed(to_number, self._allowed, extra_allowed)
        ref = f"MOCK-HU-{call_id[-8:]}"
        self.human_dialed.append(call_id)
        self.human_numbers.append(to_number)
        # The mock human answers instantly and is bridged into the insurer call.
        self.bridged.append((ref, insurer_ref or ""))
        return DialResult(provider_call_ref=ref, status="answered")

    def bridge_calls(self, human_ref: str, insurer_ref: str) -> None:
        self.bridged.append((human_ref, insurer_ref))
