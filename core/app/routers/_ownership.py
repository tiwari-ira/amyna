"""Case ownership for routes that act on a case directly or through a child id.

The Relay service acts for one signed-in patient at a time and forwards that
patient's session token (``X-Amyna-Patient-Token``); a user-owned case only
accepts its owner's token. Internal callers (agents via /internal/tools, the voice
bridge) carry no patient token and are trusted by scope instead.
"""

from fastapi import Request
from sqlalchemy.orm import Session

from claimshield_contracts.enums import ServiceName

from ..auth import ServiceIdentity
from ..models_db import CallRow, CaseRow, ClaimRow, DisputeRow, InterventionRow, VisitRow
from ..services.identity_service import (
    PATIENT_TOKEN_HEADER,
    assert_case_access,
    resolve_session_user,
)

_PARENT = {
    "claim": ClaimRow,
    "visit": VisitRow,
    "dispute": DisputeRow,
    "intervention": InterventionRow,
    "call": CallRow,
}


def guard_case(
    db: Session,
    request: Request,
    identity: ServiceIdentity,
    *,
    case_id: str | None = None,
    **child: str,
) -> None:
    """Raise Forbidden unless the Relay caller's patient owns the case.

    Pass ``case_id`` or one child id (``claim=...``, ``dispute=...``). Unknown ids are
    left to the service, which answers its usual 404.
    """
    if identity.name != ServiceName.RELAY:
        return
    if case_id is None:
        [(kind, row_id)] = child.items()
        row = db.get(_PARENT[kind], row_id)
        if row is None:
            return
        case_id = row.case_id
    case = db.get(CaseRow, case_id)
    if case is None:
        return
    assert_case_access(case, resolve_session_user(db, request.headers.get(PATIENT_TOKEN_HEADER)))
