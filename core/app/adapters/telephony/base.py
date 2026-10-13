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
    # True when a patient can only be added through the call's conference (live Telnyx).
    merges_through_conference: bool = False

    @abstractmethod
    def dial(self, call_id: str, to_number: str) -> DialResult:
        """Start an outbound call. The allowlist is enforced by the caller AND here."""

    @abstractmethod
    def hangup(self, provider_call_ref: str) -> None: ...

    def dial_human(
        self,
        call_id: str,
        to_number: str,
        insurer_ref: str | None = None,
        *,
        extra_allowed: frozenset[str] = frozenset(),
    ) -> DialResult | None:
        """Dial a human to merge them into the paused call. None = unsupported.

        ``extra_allowed`` = the case's own patient-confirmed callback number, which Core
        permits beyond TELEPHONY_ALLOWED_NUMBERS only when TELEPHONY_ALLOW_PATIENT_PHONES."""
        return None

    def bridge_calls(self, human_ref: str, insurer_ref: str) -> None:
        """Join two call legs into one call. Default: unsupported."""
        return None

    def hold_participants(
        self, conference_ref: str, leg_refs: list[str], *, audio_url: str | None = None
    ) -> None:
        """Hold conference participants (the AI while the patient verifies). Default: no-op."""
        return None

    def unhold_participants(self, conference_ref: str, leg_refs: list[str]) -> None:
        return None


def ensure_allowed(
    number: str, allowed: frozenset[str], extra: frozenset[str] = frozenset()
) -> None:
    """Second line of defense for the dial allowlist (VoiceCallManager checks first)."""
    if number not in allowed and number not in extra:
        raise SafetyBlocked(
            "Refusing to dial a number outside TELEPHONY_ALLOWED_NUMBERS.",
            code="number_not_allowed",
        )
