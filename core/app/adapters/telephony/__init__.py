"""Telephony adapters: mock | live (Telnyx Call Control -> xAI SIP), with
fallback Telnyx accounts (TELNYX_*_2/_3) and a human-merge dial target."""

from ...config import ConfigError, Settings
from .base import TelephonyAdapter


def build_telephony(settings: Settings) -> TelephonyAdapter:
    allowed = settings.allowed_numbers()
    if settings.human_handoff_phone and settings.human_handoff_phone not in allowed:
        raise ConfigError(
            "HUMAN_HANDOFF_PHONE must also be allowlisted in TELEPHONY_ALLOWED_NUMBERS."
        )
    if settings.telephony_mode == "live":
        from .telnyx_sip import TelnyxSipTelephony

        return TelnyxSipTelephony(
            api_key=settings.telnyx_api_key,
            connection_id=settings.telnyx_connection_id,
            from_number=settings.telnyx_phone_number,
            sip_uri=settings.xai_sip_uri,
            public_base_url=settings.public_base_url,
            allowed=allowed,
            fallbacks=settings.telnyx_fallback_profiles(),
            voicemail_detection=settings.telephony_voicemail_detection,
        )
    from .mock import MockTelephony

    return MockTelephony(allowed)
