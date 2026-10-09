"""Patient identity endpoints (tasks 5/19).

POST /auth/session creates a demo Amyna session for a subject. It exists only
outside production (synthetic/test identities); in production it is disabled
and the identity provider must issue the token. The record is the *binding*:
an authenticated user <-> an authorized FinchNode patient identity. A typed
``fn_pat_...`` remains an identifier, never a credential.
"""

from fastapi import APIRouter, Depends, status

from claimshield_contracts.api import AuthSessionCreate, AuthSessionResult

from ..container import Container
from ..deps import get_container
from ..services.identity_service import create_demo_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/session", response_model=AuthSessionResult, status_code=status.HTTP_201_CREATED)
def start_session(
    body: AuthSessionCreate,
    container: Container = Depends(get_container),
) -> AuthSessionResult:
    """Issue an opaque session token (demo environments; disabled in prod)."""
    with container.session_scope() as db:
        user, token = create_demo_session(
            db,
            app_env=container.settings.app_env,
            subject=body.subject,
            display_name=body.display_name,
        )
        return AuthSessionResult(user_id=user.id, patient_token=token)
