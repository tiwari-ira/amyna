"""Service-token authentication with per-caller scopes, plus HMAC webhook signatures.

Every caller (Relay service, Fetch agents, voice/telephony callbacks) has its own
bearer token and an explicit scope set. Tool endpoints require ``tools:<name>``.
"""

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from claimshield_contracts.enums import ServiceName
from claimshield_contracts.tool_api import ToolName


def tool_scope(name: ToolName) -> str:
    return f"tools:{name.value}"


VOICE_TOOLS: frozenset[ToolName] = frozenset(
    {
        ToolName.GET_DISPUTE_CASE,
        ToolName.GET_CLAIM_LINE,
        ToolName.GET_SUPPORTING_EVIDENCE,
        ToolName.RECORD_INSURER_RESPONSE,
        ToolName.REQUEST_HUMAN,
        ToolName.GET_PATIENT_CONTEXT,
    }
)
# Agents may do everything except record what the insurer said on a call.
FETCH_TOOLS: frozenset[ToolName] = frozenset(ToolName) - {ToolName.RECORD_INSURER_RESPONSE}

SCOPES: dict[ServiceName, frozenset[str]] = {
    ServiceName.RELAY: frozenset(
        {
            "sessions:issue",
            "assistant:use",
            "cases:read",
            "cases:write",
            "consents:write",
            "visits:write",
            "claims:write",
            "claims:analyze",
            "disputes:read",
            "disputes:write",
            "disputes:decide",
            "human:read",
            "human:write",
            "voice:start",
            "voice:read",
            "voice:handoff",
        }
    ),
    ServiceName.FETCH: frozenset(
        {
            "cases:read",
            "jobs:run",
            "metrics:read",
            *(tool_scope(t) for t in FETCH_TOOLS),
        }
    ),
    ServiceName.VOICE: frozenset(
        {"voice:read", "voice:events", "voice:handoff", *(tool_scope(t) for t in VOICE_TOOLS)}
    ),
}


@dataclass(frozen=True)
class ServiceIdentity:
    name: ServiceName
    scopes: frozenset[str]

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def actor(self) -> str:
        """Audit-log actor string, e.g. ``service:relay``."""
        return f"service:{self.name.value}"


def identity_for(name: ServiceName) -> ServiceIdentity:
    """Identity for in-process callers (e.g. the local_direct fetch bridge)."""
    return ServiceIdentity(name=name, scopes=SCOPES[name])


class TokenRegistry:
    """Maps bearer tokens to identities using constant-time comparison."""

    def __init__(self, tokens: Mapping[ServiceName, str]) -> None:
        self._tokens = {name: token for name, token in tokens.items() if token}

    def identify(self, token: str | None) -> ServiceIdentity | None:
        if not token:
            return None
        match: ServiceName | None = None
        for name, expected in self._tokens.items():
            if hmac.compare_digest(token.encode(), expected.encode()):
                match = name
        return identity_for(match) if match else None


_bearer = HTTPBearer(
    auto_error=False, description="Per-service token: RELAY_/FETCH_/VOICE_SERVICE_TOKEN"
)


def require_scopes(*scopes: str) -> Callable[..., ServiceIdentity]:
    """FastAPI dependency: authenticate the caller and require every scope."""

    def dependency(
        request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
    ) -> ServiceIdentity:
        registry: TokenRegistry = request.app.state.container.tokens
        identity = registry.identify(credentials.credentials if credentials else None)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "unauthorized", "message": "Missing or invalid service token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        missing = [scope for scope in scopes if not identity.has(scope)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "insufficient_scope",
                    "message": f"{identity.name.value} token lacks scope(s): {', '.join(missing)}",
                },
            )
        return identity

    return dependency


# --------------------------------------------------------------------------- webhooks

SIGNATURE_HEADER = "X-Amyna-Signature"
TIMESTAMP_HEADER = "X-Amyna-Timestamp"
SIGNATURE_TOLERANCE_S = 300


class SignatureError(Exception):
    """Webhook signature missing, stale or wrong."""


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    """``v1=<hex HMAC-SHA256(secret, "{timestamp}." + body)>``.

    Mirrored in relay/src/webhook.ts -- keep both in sync.
    """
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"v1={mac.hexdigest()}"


def verify_signature(
    secret: str,
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    now: float | None = None,
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
) -> None:
    """Raise SignatureError unless ``signature`` is valid and fresh."""
    if not secret:
        raise SignatureError("webhook secret is not configured")
    if not timestamp or not signature:
        raise SignatureError("missing signature headers")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise SignatureError("invalid timestamp") from exc
    current = int(time.time() if now is None else now)
    if abs(current - sent_at) > tolerance_s:
        raise SignatureError("timestamp outside tolerance (possible replay)")
    if not hmac.compare_digest(sign_payload(secret, timestamp, body), signature.strip()):
        raise SignatureError("signature mismatch")
