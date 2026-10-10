"""Dependency container: settings, DB, token registry, adapters, managers.

Built once per app (``create_app``); tests build their own with test settings.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from .adapters.fetch_bridge import build_fetch_bridge
from .adapters.fetch_bridge.base import FetchBridge
from .adapters.finchnode import build_finchnode_adapter
from .adapters.finchnode.base import FinchNodeAdapter
from .adapters.relay import build_notifier
from .adapters.relay.base import PatientNotifier
from .adapters.telephony import build_telephony
from .adapters.telephony.base import TelephonyAdapter
from .adapters.xai_voice import build_voice_adapter
from .adapters.xai_voice.base import VoiceAdapter
from .auth import TokenRegistry
from .config import Settings
from .db import make_engine, make_session_factory, session_scope
from .services.notification_service import NotificationDispatcher
from .services.voice_service import VoiceCallManager


@dataclass
class Container:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    tokens: TokenRegistry
    finchnode: FinchNodeAdapter
    notifier: PatientNotifier
    voice: VoiceAdapter
    telephony: TelephonyAdapter
    fetch_bridge: FetchBridge = field(init=False)
    voice_manager: VoiceCallManager = field(init=False)
    notification_dispatcher: NotificationDispatcher = field(default_factory=NotificationDispatcher)

    def session_scope(self) -> AbstractContextManager[Session]:
        """One committed-or-rolled-back transaction."""
        return session_scope(self.session_factory)


def build_container(settings: Settings) -> Container:
    engine = make_engine(settings.resolved_database_url)
    container = Container(
        settings=settings,
        engine=engine,
        session_factory=make_session_factory(engine),
        tokens=TokenRegistry(settings.service_tokens()),
        finchnode=build_finchnode_adapter(settings),
        notifier=build_notifier(settings),
        voice=build_voice_adapter(settings),
        telephony=build_telephony(settings),
    )
    container.fetch_bridge = build_fetch_bridge(container)
    container.voice_manager = VoiceCallManager(container)
    return container
