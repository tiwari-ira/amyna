"""Token registry and HMAC webhook signatures (unit level)."""

import pytest

from app.auth import (
    SCOPES,
    SignatureError,
    TokenRegistry,
    sign_payload,
    tool_scope,
    verify_signature,
)
from claimshield_contracts.enums import ServiceName
from claimshield_contracts.tool_api import ToolName

SECRET = "test-secret"
BODY = b'{"event_id":"evt_123456"}'


def test_registry_identifies_each_service() -> None:
    registry = TokenRegistry(
        {ServiceName.RELAY: "r", ServiceName.FETCH: "f", ServiceName.VOICE: "v"}
    )
    assert registry.identify("r").name == ServiceName.RELAY
    assert registry.identify("f").name == ServiceName.FETCH
    assert registry.identify("nope") is None
    assert registry.identify(None) is None


def test_scopes_are_least_privilege() -> None:
    relay, fetch, voice = (
        SCOPES[s] for s in (ServiceName.RELAY, ServiceName.FETCH, ServiceName.VOICE)
    )
    assert not any(scope.startswith("tools:") for scope in relay)
    assert tool_scope(ToolName.RECORD_INSURER_RESPONSE) not in fetch
    assert tool_scope(ToolName.RECORD_INSURER_RESPONSE) in voice
    assert tool_scope(ToolName.START_INSURANCE_CALL) not in voice
    assert "disputes:decide" not in fetch and "disputes:decide" not in voice


def test_signature_round_trip() -> None:
    signature = sign_payload(SECRET, "1000", BODY)
    assert signature.startswith("v1=")
    verify_signature(SECRET, BODY, "1000", signature, now=1100)


@pytest.mark.parametrize(
    ("secret", "timestamp", "signature_ts", "now", "message"),
    [
        ("wrong", "1000", "1000", 1000, "mismatch"),
        (SECRET, "1000", "1000", 2000, "tolerance"),
        (SECRET, "abc", "abc", 1000, "invalid timestamp"),
        (SECRET, "1001", "1000", 1000, "mismatch"),
    ],
)
def test_signature_failures(
    secret: str, timestamp: str, signature_ts: str, now: int, message: str
) -> None:
    signature = sign_payload(SECRET, signature_ts, BODY)
    with pytest.raises(SignatureError, match=message):
        verify_signature(secret, BODY, timestamp, signature, now=now)


def test_missing_headers_and_unconfigured_secret() -> None:
    with pytest.raises(SignatureError, match="missing"):
        verify_signature(SECRET, BODY, None, None)
    with pytest.raises(SignatureError, match="not configured"):
        verify_signature("", BODY, "1", "v1=x")


def test_tampered_body_fails() -> None:
    signature = sign_payload(SECRET, "1000", BODY)
    with pytest.raises(SignatureError):
        verify_signature(SECRET, BODY + b" ", "1000", signature, now=1000)
