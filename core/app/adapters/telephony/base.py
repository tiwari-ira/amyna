"""Telephony adapter interface (places/ends the PSTN leg of an insurer call)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...errors import SafetyBlocked


@dataclass(frozen=True)
class DialResult:
    provider_call_ref: str
    status: str


class TelephonyAdapter(ABC):
    mode: str

    @abstractmethod
    def dial(self, call_id: str, to_number: str) -> DialResult:
        """Start an outbound call. The allowlist is enforced by the caller AND here."""

    @abstractmethod
    def hangup(self, provider_call_ref: str) -> None: ...

    def dial_human(
        self, call_id: str, to_number: str, insurer_ref: str | None = None
    ) -> DialResult | None:
        """Dial a human to merge them into the paused call. None = unsupported."""
        return None

    def bridge_calls(self, human_ref: str, insurer_ref: str) -> None:
        """Join two call legs into one call. Default: unsupported."""
        return None


def ensure_allowed(number: str, allowed: frozenset[str]) -> None:
    """Second line of defense for the dial allowlist (VoiceCallManager checks first)."""
    if number not in allowed:
        raise SafetyBlocked(
            "Refusing to dial a number outside TELEPHONY_ALLOWED_NUMBERS.",
            code="number_not_allowed",
        )
