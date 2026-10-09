"""FinchNode clients: MOCK (local fixtures) | DEMO (public fake API, no key).

Only Core calls these (through core/app/adapters/finchnode). Agents never do.

The access this team was given is the public synthetic demo. Verified 2026-10-04:

    https://api.finchnode.com/demo/v1/fhir/Patient/patient-demo-001
    https://api.finchnode.com/demo/v1/fhir/{resourceType}?patient=patient-demo-001
    https://api.finchnode.com/demo/v1/users/patient-demo-001/records

Those URLs return fictional records and do not accept an API key. Sandbox mode
remains in this file for a later FinchNode application; it is not the current
access, and demo mode refuses a key instead of sending one.

CLI (prints normalized records):
    python -m finchnode.client --patient fn_pat_demo_001 [--mode mock|demo|sandbox]
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

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
# Patient-authorized records API. Production keys are issued once the FinchNode
# account's application is complete and enabled by its owner (finchnode.com/docs).
PRODUCTION_BASE_URL = "https://api.finchnode.com/api/v1"
SANDBOX_KEY_PREFIX = "ck_test_"


class FinchNodeError(RuntimeError):
    """FinchNode request failed after retries (or returned something unusable)."""


class FinchNodeClient(Protocol):
    """Minimal read API over FHIR-style resources."""

    def get_patient(self, patient_id: str) -> dict[str, Any] | None: ...

    def search(self, resource_type: str, patient_id: str) -> list[dict[str, Any]]: ...


def _bundle_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry["resource"] for entry in bundle.get("entry", []) if "resource" in entry]


def _concept(item: dict[str, Any]) -> dict[str, Any]:
    """Records-API item -> FHIR CodeableConcept. Source system/code/display stay as sent."""
    label = item.get("name") or item.get("reason") or item.get("type")
    coding = [
        {"system": code.get("system"), "code": str(code["code"]), "display": code.get("display")}
        for code in item.get("codes") or []
        if isinstance(code, dict) and code.get("code")
    ]
    return {"text": label if isinstance(label, str) else "", "coding": coding}


def _as_procedure(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "resourceType": "Procedure",
        "id": item.get("id"),
        "status": item.get("status"),
        "code": _concept(item),
        "performedDateTime": item.get("effectiveDate") or item.get("date") or item.get("issuedDate"),
    }


def _as_medication(item: dict[str, Any], resource_type: str) -> dict[str, Any]:
    dosage = item.get("dosage")
    return {
        "resourceType": resource_type,
        "id": item.get("id"),
        "status": item.get("status"),
        "medicationCodeableConcept": _concept(item),
        "authoredOn": item.get("startDate"),
        "effectiveDateTime": item.get("startDate"),
        "dosage": dosage if isinstance(dosage, dict) else {},
    }


def _as_encounter(item: dict[str, Any]) -> dict[str, Any]:
    location = item.get("location")
    return {
        "resourceType": "Encounter",
        "id": item.get("id"),
        "status": item.get("status"),
        "type": [_concept(item)],
        "period": {"start": item.get("startDate")},
        "serviceProvider": {"display": location} if isinstance(location, str) else {},
    }


def _as_patient(data: dict[str, Any], patient_id: str) -> dict[str, Any]:
    demo = data.get("demographics") if isinstance(data.get("demographics"), dict) else {}
    raw_name = demo.get("name")
    if isinstance(raw_name, str):
        parts = raw_name.split()
        fhir_name = (
            {"given": parts[:-1], "family": parts[-1]} if len(parts) > 1 else {"text": raw_name}
        )
    elif isinstance(raw_name, dict):
        fhir_name = raw_name
    else:
        fhir_name = {}
    return {"resourceType": "Patient", "id": demo.get("id") or patient_id, "name": [fhir_name]}


# Records-API lists that correspond to a FHIR search the rest of the app already does.
_RECORD_LISTS: dict[str, tuple[str, ...]] = {
    "Encounter": ("encounters",),
    "Procedure": ("diagnosticReports", "labs"),
    "MedicationRequest": ("medications",),
    "MedicationAdministration": ("medicationAdministrations",),
    "Observation": ("labs",),
    "DiagnosticReport": ("diagnosticReports",),
}


def resources_from_records(data: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    """Map a `/users/{id}/records` payload into the FHIR resources `search` returns."""
    if resource_type == "Claim":
        claims = data.get("claims")
        if isinstance(claims, dict):
            return [
                item
                for key in ("claims", "explanationsOfBenefit")
                for item in claims.get(key) or []
                if isinstance(item, dict)
            ]
        return [item for item in claims or [] if isinstance(item, dict)]
    resources: list[dict[str, Any]] = []
    for key in _RECORD_LISTS.get(resource_type, ()):
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            if resource_type in ("Procedure", "Observation", "DiagnosticReport"):
                resources.append(_as_procedure(item))
            elif resource_type == "Encounter":
                resources.append(_as_encounter(item))
            else:
                resources.append(_as_medication(item, resource_type))
    return resources


class FixtureClient:
    """MOCK mode: synthetic JSON fixtures, one folder per patient."""

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


class HttpFinchNodeClient:
    """DEMO (keyless fake API) or SANDBOX over HTTPS, with retries."""

    PATIENT_PATH = "/fhir/Patient/{patient_id}"
    SEARCH_PATH = "/fhir/{resource_type}"
    RECORDS_PATH = "/users/{patient_id}/records"

    def __init__(
        self,
        *,
        mode: str,
        base_url: str,
        api_key: str | None = None,
        http: httpx.Client | None = None,
        retries: int = 2,
        backoff_s: float = 0.5,
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
        elif mode == "production":
            if not api_key:
                raise ValueError("FINCHNODE_MODE=production requires FINCHNODE_API_KEY")
            if api_key.startswith(SANDBOX_KEY_PREFIX):
                raise ValueError(
                    "A sandbox key cannot be used for production records; "
                    "use the production key from the FinchNode account."
                )
            base_url = base_url or PRODUCTION_BASE_URL
        elif not base_url:
            raise ValueError(f"FINCHNODE_MODE={mode} requires FINCHNODE_BASE_URL")
        if mode == "sandbox":
            if not api_key:
                raise ValueError("FINCHNODE_MODE=sandbox requires FINCHNODE_API_KEY")
            if not api_key.startswith(SANDBOX_KEY_PREFIX):
                raise ValueError(
                    f"Sandbox keys start with {SANDBOX_KEY_PREFIX!r}; refusing a non-sandbox key."
                )
        headers = {"Accept": "application/fhir+json, application/json"}
        if mode in ("sandbox", "production") and api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._mode = mode
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0)
        self._http.headers.update(headers)
        self._retries = retries
        self._backoff_s = backoff_s

    def _get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._http.get(path, params=params)
                if response.status_code < 500:
                    return response
                last_error = FinchNodeError(f"HTTP {response.status_code}")
            except httpx.TransportError as exc:
                last_error = exc
            if attempt < self._retries:
                time.sleep(self._backoff_s * (2**attempt))
        raise FinchNodeError(f"FinchNode request failed: {path}") from last_error

    def _records(self, patient_id: str) -> dict[str, Any] | None:
        """Patient-authorized records bundle. None when this account has no such user."""
        response = self._get(self.RECORDS_PATH.format(patient_id=patient_id))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        return data if isinstance(data, dict) else None

    def get_patient(self, patient_id: str) -> dict[str, Any] | None:
        response = self._get(self.PATIENT_PATH.format(patient_id=patient_id))
        if response.status_code == 404:
            if self._mode not in ("sandbox", "production"):
                return None
            data = self._records(patient_id)
            return _as_patient(data, patient_id) if data else None
        response.raise_for_status()
        return response.json()

    def search(self, resource_type: str, patient_id: str) -> list[dict[str, Any]]:
        try:
            return self._search(resource_type, patient_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            if resource_type == "Claim":
                try:
                    return self._search("ExplanationOfBenefit", patient_id)
                except httpx.HTTPStatusError as eob:
                    if eob.response.status_code != 404:
                        raise
            if self._mode not in ("sandbox", "production"):
                raise
            data = self._records(patient_id)
            return resources_from_records(data, resource_type) if data else []

    def _search(self, resource_type: str, patient_id: str) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        path: str | None = self.SEARCH_PATH.format(resource_type=resource_type)
        params: dict[str, str] | None = {"patient": patient_id}
        while path:
            response = self._get(path, params)
            response.raise_for_status()
            bundle = response.json()
            resources.extend(_bundle_resources(bundle))
            # FHIR paging: follow the "next" link if present. VERIFY.
            path = next(
                (link["url"] for link in bundle.get("link", []) if link.get("relation") == "next"),
                None,
            )
            params = None
        return resources


def build_client(mode: str, base_url: str = "", api_key: str | None = None) -> FinchNodeClient:
    """Client for FINCHNODE_MODE (mock | demo | sandbox)."""
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
        print(f"claim {claim.external_claim_id}: {len(claim.lines)} lines, ${total:,.2f}")


if __name__ == "__main__":
    _main()
