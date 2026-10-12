"""Inbound SIP-call webhook from xAI (``realtime.call.incoming``).

Verified against the current xAI docs (speech-to-speech/sip.md): when a carrier
routes a call into ``sip:{number}@sip.voice.x.ai;transport=tls``, xAI POSTs a
signed webhook with headers ``webhook-id``, ``webhook-timestamp`` and
``webhook-signature`` (Standard Webhooks style: HMAC-SHA256 over
``"{webhook-id}.{timestamp}.{raw body}"``, base64-encoded, using the signing
secret returned when the phone number is registered).

One xAI signing secret per registered number; verify the signature before
trusting ``data.call_id``. The exact signature scheme is pinned as a VERIFY
item the first time the real endpoint delivers an event; keep the secret
required outside dev/test so this is fail-closed.
"""

import base64
import hashlib
import hmac
from typing import NamedTuple

from ...errors import InvalidInput

WEBHOOK_ID_HEADER = "webhook-id"
WEBHOOK_TIMESTAMP_HEADER = "webhook-timestamp"
WEBHOOK_SIGNATURE_HEADER = "webhook-signature"

WEBHOOK_TOLERANCE_S = 300


class XaiWebhookError(InvalidInput):
    """Webhook signature missing, stale or wrong."""


class IncomingSipCall(NamedTuple):
    call_id: str
    event_id: str
    sip_headers: dict[str, str]


def _hmac_keys(secret: str) -> list[bytes]:
    """Keys Standard Webhooks and a raw secret string can both verify.

    xAI returns ``whsec_`` plus base64. The HMAC key is those decoded bytes.
    A raw secret (used by older tests) is tried as well.
    """
    keys = [secret.encode()]
    raw = secret.removeprefix("whsec_").strip()
    padded = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(padded, validate=False)
    except ValueError:
        decoded = b""
    if decoded and decoded not in keys:
        keys.append(decoded)
    return keys


def _presented_signatures(header: str) -> list[str]:
    """Each ``v1,<base64>`` candidate. The header may carry several, space-separated."""
    found: list[str] = []
    for part in header.split():
        if part.startswith("v1,"):
            found.append(part.removeprefix("v1,"))
        elif "," in part:
            found.append(part.split(",", 1)[1])
    return found


def verify_xai_signature(
    secret: str,
    body: bytes,
    *,
    webhook_id: str | None,
    timestamp: str | None,
    signature: str | None,
    now: float | None = None,
) -> None:
    """Standard Webhooks HMAC-SHA256 verification (VERIFY against xAI's pinning)."""
    if not secret:
        raise XaiWebhookError(
            "xAI webhook signing secret is not configured", code="invalid_signature"
        )
    if not webhook_id or not timestamp or not signature:
        raise XaiWebhookError("missing webhook signature headers", code="invalid_signature")
    import time

    current = int(time.time() if now is None else now)
    try:
        sent = int(timestamp)
    except ValueError as exc:
        raise XaiWebhookError("invalid webhook timestamp", code="invalid_signature") from exc
    if abs(current - sent) > WEBHOOK_TOLERANCE_S:
        raise XaiWebhookError(
            "webhook timestamp outside the replay window", code="invalid_signature"
        )
    signed = webhook_id.encode() + b"." + timestamp.encode() + b"." + body
    candidates = _presented_signatures(signature)
    for key in _hmac_keys(secret):
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        if any(hmac.compare_digest(expected, candidate) for candidate in candidates):
            return
    raise XaiWebhookError("webhook signature mismatch", code="invalid_signature")


def parse_incoming_call(body: bytes) -> IncomingSipCall:
    """Extract the xAI call id and SIP headers from the webhook envelope."""
    import json

    try:
        event = json.loads(body)
    except ValueError as exc:
        raise XaiWebhookError("webhook body is not valid JSON", code="invalid_xai_event") from exc
    if event.get("type") != "realtime.call.incoming":
        raise XaiWebhookError("unsupported webhook type", code="invalid_xai_event")
    data = event.get("data") or {}
    headers = {h.get("name", ""): h.get("value", "") for h in data.get("sip_headers", [])}
    return IncomingSipCall(
        call_id=str(data.get("call_id") or ""),
        event_id=str(event.get("id") or ""),
        sip_headers=headers,
    )
