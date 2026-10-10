"""Patient identity endpoints (tasks 5/19).

POST /auth/session issues an Amyna session for a subject. Only an authenticated
service may ask (scope ``sessions:issue``, held by the Relay service):

* ``relay:<handle>`` subjects work in every environment -- the Relay service only
  forwards handles from signature-verified Relay platform events, so Relay is the
  identity provider for them;
* any other subject is a demo session, refused in production.

The record is the *binding*: an authenticated user <-> an authorized FinchNode
patient identity. A typed ``fn_pat_...`` remains an identifier, never a credential.
"""

from fastapi import APIRouter, Depends, status

from claimshield_contracts.api import AuthSessionCreate, AuthSessionResult
from claimshield_contracts.enums import ServiceName

from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..services.identity_service import (
    RELAY_SUBJECT_PREFIX,
    create_demo_session,
    create_relay_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/session", response_model=AuthSessionResult, status_code=status.HTTP_201_CREATED)
def start_session(
    body: AuthSessionCreate,
    identity: ServiceIdentity = Depends(require_scopes("sessions:issue")),
    container: Container = Depends(get_container),
) -> AuthSessionResult:
    """Issue an opaque patient session token (Relay handles: any environment)."""
    with container.session_scope() as db:
        if identity.name == ServiceName.RELAY and body.subject.startswith(RELAY_SUBJECT_PREFIX):
            user, token = create_relay_session(
                db, subject=body.subject, display_name=body.display_name
            )
        else:
            user, token = create_demo_session(
                db,
                app_env=container.settings.app_env,
                subject=body.subject,
                display_name=body.display_name,
            )
        return AuthSessionResult(user_id=user.id, patient_token=token)
