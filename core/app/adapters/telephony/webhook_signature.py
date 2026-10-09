"""Telnyx webhook signature verification (Ed25519; PyNaCl, no Telnyx SDK).

Every Telnyx webhook carries ``Telnyx-Timestamp`` (unix seconds) and
``Telnyx-Signature-Ed25519`` (base64) headers; the signed message is
``timestamp + raw request body`` and verifies against the account's public
key (Mission Control -> API keys -> Public key, env var TELNYX_PUBLIC_KEY).
Never parse and re-serialize the JSON body before verifying.

# VERIFY against current docs: the exact signed-payload format against the
# telnyx-python SDK's webhook verification on the first live delivery.
"""

import base64
import time

import nacl.signing
from nacl.exceptions import BadSignatureError

SIGNATURE_HEADER = "telnyx-signature-ed25519"
TIMESTAMP_HEADER = "telnyx-timestamp"
TOLERANCE_S = 300  # mirrors core/app/auth.py SIGNATURE_TOLERANCE_S


class TelnyxSignatureError(Exception):
    """Webhook signature missing, stale or wrong."""


def verify_telnyx_signature(
    public_key_b64: str,
    body: bytes,
    timestamp: str | None,
    signature_b64: str | None,
    *,
    now: float | None = None,
) -> None:
    """Raise TelnyxSignatureError unless the signature is valid and fresh."""
    if not public_key_b64:
        raise TelnyxSignatureError("TELNYX_PUBLIC_KEY is not configured")
    if not timestamp or not signature_b64:
        raise TelnyxSignatureError("missing signature headers")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise TelnyxSignatureError("invalid timestamp") from exc
    current = int(time.time() if now is None else now)
    if abs(current - sent_at) > TOLERANCE_S:
        raise TelnyxSignatureError("timestamp outside tolerance (possible replay)")
    try:
        verify_key = nacl.signing.VerifyKey(base64.b64decode(public_key_b64.encode()))
        signature = base64.b64decode(signature_b64.encode())
        verify_key.verify(timestamp.encode() + body, signature)
    except (BadSignatureError, ValueError, TypeError) as exc:
        raise TelnyxSignatureError("signature mismatch") from exc
