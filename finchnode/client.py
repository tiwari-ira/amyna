"""FinchNode clients: MOCK (local fixtures) | DEMO | SANDBOX | PRODUCTION (one HTTP API).

Only Core calls these (through core/app/adapters/finchnode). Agents never do.

Verified 2026-10-09 against finchnode.com/docs and the live public demo:

    demo        https://api.finchnode.com/demo/v1   no key (fictional patients)
    sandbox     https://api.finchnode.com/api/v1    Bearer ck_test_... (synthetic patients)
    production  https://api.finchnode.com/api/v1    Bearer ck_live_... (real, consented)

Every environment serves ``GET /users/{subject}/records``: one snapshot of the
patient's shared categories with completeness metadata (see ``records.py``).
Sandbox and production have no FHIR endpoints, so the HTTP client reads only
that snapshot -- once per patient per ``cache_ttl_s`` -- instead of one call per
record type. Patients link through hosted Connect sessions (``/connect/sessions``);
sandbox patients come from FinchNode's named scenarios via ``simulate``, and
``/sandbox/subjects/{subject}/events`` drives them (new records, revocation...).

CLI (prints normalized records):
    python -m finchnode.client --patient fn_pat_demo_001 [--mode mock|demo|sandbox]
"""

import argparse
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

from .records import Snapshot

log = logging.getLogger("claimshield.finchnode")

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RESOURCE_FILES = {
    "Coverage": "coverage.json",
    "Encounter": "encounters.json",
    "Procedure": "procedures.json",
    "MedicationAdministration": "medications.json",
    "Claim": "claims.json",
}
# Public fake demo. No account, no API key.
DEMO_BASE_URL = "https://api.finchnode.com/demo/v1"
# Sandbox and production share one API; the key decides which data it reads.
API_BASE_URL = "https://api.finchnode.com/api/v1"
PRODUCTION_BASE_URL = API_BASE_URL
SANDBOX_KEY_PREFIX = "ck_test_"
# A Retry-After longer than this fails the request instead of stalling Core.
MAX_RETRY_AFTER_S = 5.0


class FinchNodeError(RuntimeError):
    """FinchNode request failed after retries (or returned something unusable)."""

    code = "finchnode_unavailable"
    retryable = True


class FinchNodeConsentInactive(FinchNodeError):
    """410 consent_inactive: the patient's sharing ended (revoked or expired)."""

    code = "finchnode_consent_inactive"
    retryable = False


class FinchNodeAuthError(FinchNodeError):
    """401/403: the key is missing, wrong for this environment, or lacks the scope."""

    code = "finchnode_not_authorized"
    retryable = False


class FinchNodeRequestError(FinchNodeError):
    """Another 4xx: FinchNode rejected the request itself (bad id, bad body...)."""

    code = "finchnode_request_rejected"
    retryable = False


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def raise_for_finchnode(response: httpx.Response, what: str) -> None:
    """Map a non-2xx FinchNode response to a typed error. Messages never include
    the URL (it carries the patient's subject)."""
    if response.is_success:
        return
    status, code = response.status_code, _error_code(response)
    detail = f"{what}: HTTP {status}{f' {code}' if code else ''}"
    if status == 410:
        raise FinchNodeConsentInactive(detail)
    if status in (401, 403):
        raise FinchNodeAuthError(detail)
    if status == 429 or status >= 500:
        raise FinchNodeError(detail)
    raise FinchNodeRequestError(detail)


class FinchNodeClient(Protocol):
    """Minimal read API over FHIR-style resources (the local fixtures)."""

    def get_patient(self, patient_id: str) -> dict[str, Any] | None: ...

    def search(self, resource_type: str, patient_id: str) -> list[dict[str, Any]]: ...


def _bundle_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry["resource"] for entry in bundle.get("entry", []) if "resource" in entry]


class FixtureClient:
    """MOCK mode: synthetic FHIR JSON fixtures, one folder per patient."""

    def __init__(self, root: Path = FIXTURES_DIR) -> None:
        self._root = root

    def _read(self, patient_id: str, filename: str) -> dict[str, Any] | None:
        path = self._root / patient_id / filename
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_patient(self, patient_id: str) -> dict[str, Any] | None:
        return self._read(patient_id, "patient.json")

    def search(self, resource_type: str, patient_id: str) -> list[dict[str, Any]]:
        filename = RESOURCE_FILES.get(resource_type)
        bundle = self._read(patient_id, filename) if filename else None
        return _bundle_resources(bundle) if bundle else []


def _connect_session(body: Any) -> dict[str, Any]:
    """One Connect session shape for both APIs (the demo uses different field names)."""
    body = body if isinstance(body, dict) else {}
    status = str(body.get("status") or "pending")
    return {
        "id": body.get("id"),
        "url": body.get("url") or body.get("connect_url"),
        "status": {"complete": "completed", "cancelled": "canceled"}.get(status, status),
        "subject": body.get("subject") or body.get("patient_id"),
        "external_id": body.get("externalId") or body.get("external_user_id"),
        "environment": body.get("environment"),
        "expires_at": body.get("expiresAt") or body.get("expires_at"),
        "sync_status": (body.get("sync") or {}).get("status")
        if isinstance(body.get("sync"), dict)
        else None,
        "simulation_state": (body.get("simulation") or {}).get("state")
        if isinstance(body.get("simulation"), dict)
        else None,
    }


class HttpFinchNodeClient:
    """DEMO (keyless), SANDBOX (ck_test_) or PRODUCTION (ck_live_) over HTTPS."""

    RECORDS_PATH = "/users/{subject}/records"

    def __init__(
        self,
        *,
        mode: str,
        base_url: str,
        api_key: str | None = None,
        http: httpx.Client | None = None,
        retries: int = 2,
        backoff_s: float = 0.5,
        cache_ttl_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if mode not in ("demo", "sandbox", "production"):
            raise ValueError(f"HttpFinchNodeClient supports demo|sandbox|production, not {mode!r}")
        if mode == "demo":
            if api_key:
                raise ValueError(
                    "The FinchNode demo is public fake data and has no API key. "
                    "Unset FINCHNODE_API_KEY."
                )
            base_url = base_url or DEMO_BASE_URL
        else:
            if not api_key:
                raise ValueError(f"FINCHNODE_MODE={mode} requires FINCHNODE_API_KEY")
            if mode == "sandbox" and not api_key.startswith(SANDBOX_KEY_PREFIX):
                raise ValueError(
                    f"Sandbox keys start with {SANDBOX_KEY_PREFIX!r}; refusing a non-sandbox key."
                )
            if mode == "production" and api_key.startswith(SANDBOX_KEY_PREFIX):
                raise ValueError(
                    "A sandbox key cannot be used for production records; "
                    "use the production key from the FinchNode account."
                )
            base_url = base_url or API_BASE_URL
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.mode = mode
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0)
        self._http.headers.update(headers)
        self._retries = retries
        self._backoff_s = backoff_s
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[str, tuple[float, Snapshot | None]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ transport

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Retry transport errors, 5xx and 429 (honoring a short Retry-After)."""
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            delay = self._backoff_s * (2**attempt)
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "")
                    wait = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 1
                    if wait > MAX_RETRY_AFTER_S or attempt == self._retries:
                        return response
                    delay = wait
                elif response.status_code < 500:
                    return response
                last_error = FinchNodeError(f"HTTP {response.status_code}")
            if attempt < self._retries:
                self._sleep(delay)
        raise FinchNodeError(f"FinchNode request failed: {method} {path.split('/')[1]}") from (
            last_error
        )

    # ------------------------------------------------------------------ records

    def snapshot(self, subject: str) -> Snapshot | None:
        """The patient's records snapshot (cached), or None when FinchNode has no such
        patient for this application."""
        now = self._clock()
        with self._lock:
            cached = self._cache.get(subject)
            if cached and now - cached[0] < self._cache_ttl_s:
                return cached[1]
        response = self._request("GET", self.RECORDS_PATH.format(subject=subject))
        if response.status_code == 404:
            snapshot = None
        else:
            raise_for_finchnode(response, "records")
            try:
                snapshot = Snapshot.from_body(subject, response.json())
            except ValueError as exc:
                raise FinchNodeError("records: response was not JSON") from exc
        with self._lock:
            self._cache[subject] = (now, snapshot)
        return snapshot

    def invalidate(self, subject: str | None = None) -> None:
        """Forget cached records (all, or one patient's) -- e.g. on records.updated."""
        with self._lock:
            if subject is None:
                self._cache.clear()
            else:
                self._cache.pop(subject, None)

    # ------------------------------------------------------------------ connect + sandbox

    def create_connect_session(
        self,
        *,
        external_id: str,
        categories: list[str] | None = None,
        sync_mode: str | None = None,
        return_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"externalId": external_id}
        if categories:
            body["categories"] = categories
        if sync_mode:
            body["syncMode"] = sync_mode
        if return_url:
            body["returnUrl"] = return_url
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        response = self._request("POST", "/connect/sessions", json=body, headers=headers)
        raise_for_finchnode(response, "connect session")
        return _connect_session(response.json())

    def get_connect_session(self, session_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/connect/sessions/{session_id}")
        raise_for_finchnode(response, "connect session")
        return _connect_session(response.json())

    def simulate(self, session_id: str, scenario: str) -> dict[str, Any]:
        """Sandbox only: complete a session with a synthetic scenario patient."""
        response = self._request(
            "POST", f"/connect/sessions/{session_id}/simulate", json={"scenario": scenario}
        )
        raise_for_finchnode(response, "simulate")
        return _connect_session(response.json())

    def sandbox_event(self, subject: str, event_type: str, source: str | None = None) -> Any:
        """Sandbox only: records.advance | consent.revoke | consent.expire | source.fail."""
        body: dict[str, Any] = {"type": event_type}
        if source:
            body["source"] = source
        response = self._request("POST", f"/sandbox/subjects/{subject}/events", json=body)
        raise_for_finchnode(response, "sandbox event")
        self.invalidate(subject)
        return response.json() if response.content else {}


def build_client(
    mode: str, base_url: str = "", api_key: str | None = None
) -> FixtureClient | HttpFinchNodeClient:
    """Client for FINCHNODE_MODE (mock | demo | sandbox | production)."""
    if mode == "mock":
        return FixtureClient()
    return HttpFinchNodeClient(mode=mode, base_url=base_url, api_key=api_key)


def _main() -> None:
    from . import tools

    parser = argparse.ArgumentParser(description="Print normalized FinchNode data for a patient")
    parser.add_argument("--patient", default="fn_pat_demo_001")
    parser.add_argument("--mode", default=os.environ.get("FINCHNODE_MODE", "mock"))
    args = parser.parse_args()
    client = build_client(
        args.mode, os.environ.get("FINCHNODE_BASE_URL", ""), os.environ.get("FINCHNODE_API_KEY")
    )
    print("profile:", tools.get_patient_profile(client, args.patient))
    for name in ("encounters", "procedures", "medications"):
        records = getattr(tools, f"get_patient_{name}")(client, args.patient)
        print(f"{name}:")
        for record in records:
            codes = ", ".join(f"{c.system.value} {c.code}" for c in record.codes) or "uncoded"
            print(f"  {record.occurred_on}  {record.display}  [{codes}]")
    for claim in tools.get_patient_claims(client, args.patient):
        total = claim.total_billed_cents / 100
        itemized = f"{len(claim.lines)} lines" if claim.lines else "not itemized"
        print(f"claim {claim.external_claim_id}: {itemized}, ${total:,.2f}")
    for record_type, gap in tools.record_gaps(client, args.patient).items():
        print(f"incomplete {record_type.value}: {gap}")


if __name__ == "__main__":
    _main()
