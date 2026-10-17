"""Audit trail and PHI-safe structured logging.

- :func:`redact` / :func:`redact_text` mask PHI-like values (SSNs, phones,
  emails, member ids, DOB phrases) and drop known PHI keys.
- :class:`JsonFormatter` + :class:`PhiRedactingFilter` apply redaction to every
  log record, so a stray f-string cannot leak a transcript line into logs.
- :func:`record_audit` writes an append-only ``audit_events`` row.

Audit details should contain ids, types and counts -- never free text.
"""

import json
import logging
import re
import traceback
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.ids import new_id

from .models_db import AuditRow

REDACTED = "[REDACTED]"

PHI_KEYS = frozenset(
    {
        "address",
        "authorization_text",
        "consent_text",
        "display_name",
        "dob",
        "letter_text",
        "member_ref",
        "name",
        "notes",
        "patient_display_name",
        "payer_phone",
        "phone",
        "prompt",
        "quote",
        "segments",
        "ssn",
        "text",
        "transcript",
    }
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\w)\d{3}-\d{2}-\d{4}(?!\w)"), "[REDACTED-SSN]"),
    (re.compile(r"(?<!\w)\d{9}(?!\w)"), "[REDACTED-ID]"),
    (
        re.compile(r"(?<![\w+])(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"),
        "[REDACTED-PHONE]",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED-EMAIL]"),
    (re.compile(r"\b(?:SYN-)?MBR-\d+\b"), "[REDACTED-MEMBER]"),
    (re.compile(r"(?i)\b(dob|date of birth)\b\s*[:=]?\s*[\w/.-]+"), r"\1 [REDACTED]"),
)


def redact_text(value: str) -> str:
    """Mask PHI-like substrings in free text."""
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact(value: Any) -> Any:
    """Recursively redact a JSON-like structure (dict keys in PHI_KEYS are dropped)."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in PHI_KEYS else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def mask_phone(number: str | None) -> str | None:
    """``+15555550100`` -> ``+1•••••••0100``.

    [2.9] Numbers no longer than the visible prefix+tail would leak entirely
    (found by the hypothesis property test); they are fully masked instead.
    """
    if not number:
        return None
    if len(number) <= 6:
        return "•" * len(number)
    return f"{number[:2]}{'•' * (len(number) - 6)}{number[-4:]}"


def mask_member_ref(member_ref: str | None) -> str | None:
    if not member_ref:
        return None
    # [2.9] A ref no longer than the visible tail would leak entirely:
    # short values are fully masked (found by the hypothesis property test).
    tail = member_ref[-3:] if len(member_ref) > 3 else ""
    return f"•••{tail}" if tail else "•••"


class PhiRedactingFilter(logging.Filter):
    """Redacts the message, structured ``fields`` and traceback text of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            # uvicorn's AccessFormatter unpacks these five args itself; redact each one
            # and keep the shape (clearing them broke every access-log line).
            record.args = tuple(
                redact_text(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        else:
            record.msg = redact_text(record.getMessage())
            record.args = ()
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            record.fields = redact(fields)
        if record.exc_info and not record.exc_text:
            # Formatters reuse exc_text, so the traceback is redacted exactly once here.
            record.exc_text = redact_text("".join(traceback.format_exception(*record.exc_info)))
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, plus redacted ``fields``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


# Loggers that ship their own handlers (uvicorn configures these before the app factory runs).
THIRD_PARTY_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy", "httpx")


def configure_logging(level: str = "INFO") -> None:
    """Install JSON + redaction on the root logger and redaction on third-party
    handlers (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(getattr(h, "_Amyna", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler.addFilter(PhiRedactingFilter())
        handler._Amyna = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    for name in THIRD_PARTY_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, PhiRedactingFilter) for f in handler.filters):
                handler.addFilter(PhiRedactingFilter())


_log = logging.getLogger("claimshield.audit")


def record_audit(
    db: Session,
    *,
    actor: str,
    action: str,
    case_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditRow:
    """Append a redacted audit event in the caller's transaction.

    [3.2] The event chains to the previous event of its case: ``entry_hash``
    covers the whole record (including ``prev_hash``), so any later edit or
    deletion is detectable via :func:`verify_audit_chain`.
    """
    from .db import utcnow

    redacted = redact(details or {})
    at = utcnow()
    prev_hash = db.scalar(
        select(AuditRow.entry_hash)
        .where(AuditRow.case_id == case_id if case_id else AuditRow.case_id.is_(None))
        .order_by(AuditRow.at.desc(), AuditRow.id.desc())
        .limit(1)
    )
    entry_hash = _chain_hash(
        prev_hash=prev_hash,
        at=at,
        actor=actor,
        action=action,
        case_id=case_id,
        resource_id=resource_id,
        details=redacted,
    )
    row = AuditRow(
        id=new_id("audit"),
        at=at,
        actor=actor,
        action=action,
        case_id=case_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details=redacted,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
    )
    db.add(row)
    _log.info(
        action,
        extra={"fields": {"actor": actor, "case_id": case_id, "resource_id": resource_id}},
    )
    return row


def _chain_hash(
    *,
    prev_hash: str | None,
    at: datetime,
    actor: str,
    action: str,
    case_id: str | None,
    resource_id: str | None,
    details: dict[str, Any],
) -> str:
    import hashlib

    # SQLite round-trips naive datetimes: normalize so a verified chain does not
    # depend on the driver's timezone handling.
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    payload = "\n".join(
        (
            prev_hash or "",
            at.isoformat(),
            actor,
            action,
            case_id or "",
            resource_id or "",
            json.dumps(details, sort_keys=True, separators=(",", ":")),
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def verify_audit_chain(db: Session, case_id: str | None = None) -> bool:
    """True when the case's audit chain is intact end to end.

    Recomputes every entry hash and checks the links; a single edited or
    deleted row makes this False. Run it from an operator loop or a
    compliance spot check.
    """
    rows = list(
        db.scalars(
            select(AuditRow)
            .where(AuditRow.case_id == case_id if case_id else AuditRow.case_id.is_(None))
            .order_by(AuditRow.at, AuditRow.id)
        )
    )
    prev: str | None = None
    for row in rows:
        expected = _chain_hash(
            prev_hash=row.prev_hash,
            at=row.at,
            actor=row.actor,
            action=row.action,
            case_id=row.case_id,
            resource_id=row.resource_id,
            details=row.details,
        )
        if row.prev_hash != prev or row.entry_hash != expected:
            return False
        prev = row.entry_hash
    return True
