"""Voice adapters: mock (scripted insurer) | live (xAI realtime)."""

from ...config import Settings
from .base import VoiceAdapter


def build_voice_adapter(settings: Settings) -> VoiceAdapter:
    if settings.xai_voice_mode == "live":
        from .live import XaiRealtimeVoiceAdapter

        return XaiRealtimeVoiceAdapter(
            api_key=settings.xai_api_key,
            url=settings.xai_realtime_url,
            model=settings.xai_voice_model or None,
            voice=settings.xai_voice_name or None,
        )
    from .mock import MockVoiceAdapter

    return MockVoiceAdapter()
