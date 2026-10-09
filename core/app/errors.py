"""Domain errors. Each maps to an HTTP status and a stable machine-readable code.

Error bodies always look like ``{"detail": {"code": "...", "message": "...", ...}}``.
"""

from typing import Any


class DomainError(Exception):
    """Base class for expected, client-visible failures."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.extra = extra

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class ConsentRequired(Conflict):
    code = "consent_required"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class InvalidInput(DomainError):
    status_code = 422
    code = "invalid_input"


class SensitiveDataRejected(DomainError):
    status_code = 422
    code = "sensitive_data_rejected"


class SafetyBlocked(DomainError):
    status_code = 409
    code = "safety_blocked"


class UpstreamError(DomainError):
    status_code = 502
    code = "upstream_error"
