"""Per-sender chat state and pending-request correlation.

Works with uAgents ``ctx.storage`` (get/set of JSON values) or ``MemoryStore``
(local_direct + tests). Pending requests are correlated by ``request_id``.
"""

from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from typing import Any, Protocol

SESSION_PREFIX = "session:"
REQUEST_PREFIX = "request:"
PENDING_SENDERS = "pending_senders"
CORE_TRIGGER_OWNER = "core-trigger"


class Stage(StrEnum):
    NEW = "new"
    AWAITING_IDS = "awaiting_ids"
    AWAITING_CONSENT = "awaiting_consent"
    GATHERING_EVIDENCE = "gathering_evidence"
    ANALYZING = "analyzing"
    CONFIRMING = "confirming"
    AWAITING_DISPUTE_OK = "awaiting_dispute_ok"
    PREPARING_DISPUTE = "preparing_dispute"
    AWAITING_APPROVAL = "awaiting_approval"
    CALLING = "calling"
    AWAITING_HANDOFF = "awaiting_handoff"
    DONE = "done"
    ERROR = "error"


class KeyValueStore(Protocol):
    """Subset of uagents' KeyValueStore used here."""

    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: Any) -> None: ...

    def remove(self, key: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def remove(self, key: str) -> None:
        self._data.pop(key, None)


@dataclass
class PendingRequest:
    request_id: str
    agent: str
    kind: str
    sent_at: float


@dataclass
class ChatSession:
    sender: str
    stage: Stage = Stage.NEW
    finchnode_patient_id: str | None = None
    external_claim_id: str | None = None
    case_id: str | None = None
    claim_id: str | None = None
    dispute_id: str | None = None
    call_id: str | None = None
    open_question_id: str | None = None
    handoff_id: str | None = None
    pending: list[PendingRequest] = field(default_factory=list)

    def reset(self) -> None:
        """Start over in place (keeps the sender)."""
        fresh = ChatSession(sender=self.sender)
        for item in fields(self):
            setattr(self, item.name, getattr(fresh, item.name))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatSession":
        pending = [PendingRequest(**p) for p in data.get("pending", [])]
        return cls(**{**data, "stage": Stage(data["stage"]), "pending": pending})


def load_session(store: KeyValueStore, sender: str) -> ChatSession:
    data = store.get(SESSION_PREFIX + sender)
    return ChatSession.from_dict(data) if data else ChatSession(sender=sender)


def save_session(store: KeyValueStore, session: ChatSession) -> None:
    """Persist the session; index new pending requests and drop finished ones."""
    previous = store.get(SESSION_PREFIX + session.sender) or {}
    current = {p.request_id for p in session.pending}
    for old in previous.get("pending", []):
        if old["request_id"] not in current:
            forget_request(store, old["request_id"])
    store.set(SESSION_PREFIX + session.sender, session.to_dict())
    for pending in session.pending:
        register_request(store, pending.request_id, session.sender)
    senders = set(store.get(PENDING_SENDERS) or [])
    if session.pending:
        senders.add(session.sender)
    else:
        senders.discard(session.sender)
    store.set(PENDING_SENDERS, sorted(senders))


def register_request(store: KeyValueStore, request_id: str, owner: str) -> None:
    store.set(REQUEST_PREFIX + request_id, owner)


def owner_of_request(store: KeyValueStore, request_id: str) -> str | None:
    return store.get(REQUEST_PREFIX + request_id)


def forget_request(store: KeyValueStore, request_id: str) -> None:
    """uAgents rewrites its whole storage file per write, so keys must not pile up."""
    if store.get(REQUEST_PREFIX + request_id) is not None:
        store.remove(REQUEST_PREFIX + request_id)


def pending_agent(session: ChatSession, request_id: str) -> str | None:
    """Which agent a pending request was sent to (used to verify the replier)."""
    return next((p.agent for p in session.pending if p.request_id == request_id), None)


def pending_senders(store: KeyValueStore) -> list[str]:
    return list(store.get(PENDING_SENDERS) or [])
