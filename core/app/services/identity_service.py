"""Patient identity: authenticated accounts, demo sessions, FinchNode links.

Prompt tasks 5/19:
- A typed ``fn_pat_...`` ID is an IDENTIFIER, never a credential.
- Cases belong to authenticated Amyna users.
- A user-bound case may only be read or advanced by its owner (verifiable via
  the patient session token that Relay forwards on every patient request).
- Demo sessions (self-issued tokens) exist only outside production; production
  requires the identity provider to issue the session (blocked externally).

Tokens are opaque, hashed at rest (sha256), never logged.
"""

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.enums import LinkStatus
from claimshield_contracts.ids import new_id

from ..audit import record_audit
from ..db import utcnow
from ..errors import Forbidden
from ..models_db import CaseRow, FinchLinkRow, PatientSessionRow, UserRow

PATIENT_TOKEN_HEADER = "X-Amyna-Patient-Token"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


RELAY_SUBJECT_PREFIX = "relay:"


def create_demo_session(
    db: Session, *, app_env: str, subject: str, display_name: str | None = None
) -> tuple[UserRow, str]:
    """Issue a demo session outside production. Returns (user, raw token)."""
    if app_env not in ("dev", "test", "staging"):
        raise Forbidden(
            "Demo sessions are disabled in production; the identity provider must "
            "issue the patient's session token.",
            code="dev_sessions_disabled",
        )
    return _issue(db, subject=subject, display_name=display_name)


def create_relay_session(
    db: Session, *, subject: str, display_name: str | None = None
) -> tuple[UserRow, str]:
    """Session for a Relay messaging handle, in any environment.

    The caller is the authenticated Relay service, and the handle comes from a Relay
    platform event whose signature it verified: Relay is the identity provider for
    ``relay:<handle>`` subjects (one account per handle)."""
    if not subject.startswith(RELAY_SUBJECT_PREFIX) or len(subject) <= len(RELAY_SUBJECT_PREFIX):
        raise Forbidden("Relay sessions need a relay:<handle> subject.", code="invalid_subject")
    return _issue(db, subject=subject, display_name=display_name)


def _issue(db: Session, *, subject: str, display_name: str | None) -> tuple[UserRow, str]:
    user = db.scalar(select(UserRow).where(UserRow.auth_subject == subject))
    if user is None:
        user = UserRow(id=new_id("user"), auth_subject=subject, display_name=display_name)
        db.add(user)
        db.flush()
    raw = f"pt_{secrets.token_urlsafe(32)}"
    db.add(PatientSessionRow(token_hash=_hash(raw), user_id=user.id))
    db.flush()
    return user, raw


def resolve_session_user(db: Session, token: str | None) -> UserRow | None:
    if not token:
        return None
    row = db.get(PatientSessionRow, _hash(token))
    return db.get(UserRow, row.user_id) if row else None


def link_finch(db: Session, *, case: CaseRow, actor: str) -> FinchLinkRow | None:
    """Bind the case's (verified-by-consent) FinchNode identity to its owner."""
    if case.user_id is None:
        return None
    existing = db.scalar(
        select(FinchLinkRow).where(
            FinchLinkRow.user_id == case.user_id,
            FinchLinkRow.finchnode_patient_id == case.finchnode_patient_id,
            FinchLinkRow.status == LinkStatus.LINKED.value,
        )
    )
    if existing:
        return existing
    link = FinchLinkRow(
        id=new_id("finch_link"),
        user_id=case.user_id,
        finchnode_patient_id=case.finchnode_patient_id,
        status=LinkStatus.LINKED.value,
        created_at=utcnow(),
    )
    db.add(link)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="identity.finch_linked",
        case_id=case.id,
        resource_type="finch_link",
        resource_id=link.id,
        details={"user_id": case.user_id},
    )
    return link


def assert_case_access(case: CaseRow, user: UserRow | None) -> None:
    """If a case belongs to a user, only that user may touch it."""
    if case.user_id is None:
        return  # legacy/demo cases stay reachable via the scoped service tokens
    if user is None or user.id != case.user_id:
        raise Forbidden(
            "This case belongs to a different signed-in patient.", code="case_forbidden"
        )
