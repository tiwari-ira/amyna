"""FinchNode normalized-records snapshots: the shape every FinchNode HTTP API returns.

``GET /users/{subject}/records`` is the one records route FinchNode serves in the
public demo, the sandbox and production alike (finchnode.com/docs/records/record-model).
A snapshot has ``data`` grouped into sections, the ``categories`` the patient
shared, and ``meta`` saying what is complete (finchnode.com/docs/records/freshness).
Shape verified against the public demo on 2026-10-09 (schemaVersion 2).

The functions here turn one snapshot into the shared contract models. Codes stay
verbatim (ARCHITECTURE rule 5) and nothing is invented for missing fields: a
record without a usable date keeps ``occurred_on=None``, and a bill with no
procedure code comes back with ``lines=[]``. A procedure code becomes one line
for the claim total, because FinchNode does not split that total.
"""

import re
from dataclasses import dataclass
from typing import Any

from claimshield_contracts.enums import ClaimSource, CodeSystem, RecordType
from claimshield_contracts.models import Claim, ClaimLine, CodeRef, HealthRecord, PatientRef

from .normalize import code_system_for, safe_date, to_cents, us_state

# Snapshot statuses that mean the read is complete ("simulated" is the public demo).
COMPLETE_SYNC_STATUSES = frozenset({"complete", "simulated"})
# Warnings that describe the data set itself, not a gap in this read.
INFORMATIONAL_WARNINGS = frozenset({"synthetic_data"})

# FinchNode categories behind each record type the bill review reads.
CATEGORY_FOR = {
    RecordType.ENCOUNTER: "encounters",
    RecordType.PROCEDURE: "labs",
    RecordType.MEDICATION: "medications",
}


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("display", "name", "text"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    return None


@dataclass(frozen=True)
class Snapshot:
    """One ``/users/{subject}/records`` response."""

    subject: str
    data: dict[str, Any]
    shared: frozenset[str]
    meta: dict[str, Any]

    @classmethod
    def from_body(cls, subject: str, body: Any) -> "Snapshot":
        body = body if isinstance(body, dict) else {}
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        categories = body.get("categories")
        shared = (
            frozenset(c for c in categories if isinstance(c, str))
            if isinstance(categories, list)
            else frozenset()
        )
        return cls(subject=subject, data=data, shared=shared, meta=meta)

    def gap(self, category: str) -> str | None:
        """Why ``category`` can't be read as complete, or None when it can.

        FinchNode's rule: an empty category means "none on file" only when the
        sync is complete, the category is available, and no warning names it.
        """
        if self.shared and category not in self.shared:
            return "not_shared"
        status = self.meta.get("syncStatus")
        if status not in COMPLETE_SYNC_STATUSES:
            return f"sync_{status}" if isinstance(status, str) and status else "sync_unknown"
        if category in (self.meta.get("missingCategories") or []):
            return "missing"
        for warning in _items(self.meta.get("warnings")):
            if warning.get("category") == category:
                return str(warning.get("code") or "warning")
        return None

    def warning_codes(self) -> list[str]:
        """Warning codes worth surfacing (no messages: they can name sources)."""
        return sorted(
            {
                str(w["code"])
                for w in _items(self.meta.get("warnings"))
                if w.get("code") and w["code"] not in INFORMATIONAL_WARNINGS
            }
        )

    def section(self, name: str) -> list[dict[str, Any]]:
        return _items(self.data.get(name))

    def claims_section(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(explanations of benefit, coverages). The section is a dict or one typed list."""
        raw = self.data.get("claims")
        if isinstance(raw, dict):
            return _items(raw.get("explanationsOfBenefit")), _items(raw.get("coverages"))
        items = _items(raw)
        coverages = [i for i in items if i.get("resourceType") == "Coverage"]
        return [i for i in items if i.get("resourceType") != "Coverage"], coverages


# --------------------------------------------------------------------------- records


def codes_of(record: dict[str, Any]) -> list[CodeRef]:
    """The source's codes, verbatim."""
    return [
        CodeRef(
            system=code_system_for(code.get("system")),
            code=str(code["code"]),
            display=code.get("display"),
            source_system_uri=code.get("system"),
        )
        for code in _items(record.get("codes"))
        if code.get("code")
    ]


def _record(
    item: dict[str, Any],
    patient_id: str,
    record_type: RecordType,
    *,
    display: str | None,
    occurred: Any,
    fallback_type: str,
    **fields: Any,
) -> HealthRecord:
    record_id = str(item.get("id") or item.get("sourceRecordId") or "unknown")
    return HealthRecord(
        record_id=f"fn:{record_id}",
        record_type=record_type,
        patient_id=patient_id,
        occurred_on=safe_date(occurred),
        display=display or fallback_type,
        codes=codes_of(item),
        status=_text(item.get("status")),
        source_resource_type=str(item.get("resourceType") or fallback_type),
        source_id=str(item.get("sourceRecordId") or record_id),
        **fields,
    )


def encounters(snapshot: Snapshot) -> list[HealthRecord]:
    """Visits that happened (appointments and care teams are not visits)."""
    return [
        _record(
            item,
            snapshot.subject,
            RecordType.ENCOUNTER,
            display=_text(item.get("type")) or _text(item.get("name")) or _text(item.get("reason")),
            occurred=item.get("startDate") or item.get("date"),
            fallback_type="Encounter",
            facility=_text(item.get("location")) or _text(item.get("sourceName")),
            performer=_text(item.get("provider")),
        )
        for item in snapshot.section("encounters")
    ]


def procedures(snapshot: Snapshot) -> list[HealthRecord]:
    """FinchNode has no procedure category: lab results and reports are the closest record
    of tests that were performed."""
    return [
        _record(
            item,
            snapshot.subject,
            RecordType.PROCEDURE,
            display=_text(item.get("name")) or _text(item.get("code")),
            occurred=item.get("date") or item.get("effectiveDate") or item.get("issuedDate"),
            fallback_type="Observation" if section == "labs" else "DiagnosticReport",
            performer=_text(item.get("performer")),
        )
        for section in ("labs", "diagnosticReports")
        for item in snapshot.section(section)
    ]


def medications(snapshot: Snapshot) -> list[HealthRecord]:
    """Medications given during care. Prescriptions and pharmacy dispenses say a drug
    was ordered, not that it was administered at the billed visit, so they don't count."""
    records = []
    for item in snapshot.section("medicationAdministrations"):
        attributes = {
            key: value
            for key, value in (
                ("dose", _text(item.get("dosage"))),
                ("route", _text(item.get("route"))),
            )
            if value
        }
        records.append(
            _record(
                item,
                snapshot.subject,
                RecordType.MEDICATION,
                display=_text(item.get("name")),
                occurred=item.get("date") or item.get("effectiveDate") or item.get("startDate"),
                fallback_type="MedicationAdministration",
                performer=_text(item.get("performer")),
                attributes=attributes,
            )
        )
    return records


# --------------------------------------------------------------------------- patient + claims


def _payer(coverage: dict[str, Any]) -> str | None:
    payor = coverage.get("payor")
    if isinstance(payor, list):
        payor = payor[0] if payor else None
    return _text(payor) or _text(coverage.get("payer")) or _text(coverage.get("insurer"))


def _active_coverage(coverages: list[dict[str, Any]]) -> dict[str, Any]:
    return next((c for c in coverages if c.get("status", "active") == "active"), {})


def _state_from_address(address: Any) -> str | None:
    if isinstance(address, dict):
        return us_state(address.get("state"))
    if isinstance(address, str):
        for part in reversed([p.strip() for p in address.split(",")]):
            if state := us_state(part):
                return state
    return None


def _display_name(value: Any) -> str | None:
    if isinstance(value, dict):
        given = value.get("given") or []
        parts = [*(given if isinstance(given, list) else [given]), value.get("family")]
        return " ".join(p for p in parts if isinstance(p, str) and p).strip() or _text(value)
    return _text(value)


def patient_profile(snapshot: Snapshot) -> PatientRef | None:
    """Demographics + active coverage. FinchNode coverage carries no payer phone."""
    demographics = snapshot.data.get("demographics")
    demographics = demographics if isinstance(demographics, dict) else {}
    _, coverages = snapshot.claims_section()
    coverage = _active_coverage(coverages)
    if not demographics and not coverage:
        return None
    payer = _payer(coverage)
    member = _text(coverage.get("subscriberId"))
    name = _display_name(demographics.get("name"))
    return PatientRef(
        finchnode_patient_id=snapshot.subject,
        display_name=name[:200] if name else None,
        member_ref=member[:80] if member else None,
        payer_name=payer[:200] if payer else None,
        payer_phone=None,
        state=_state_from_address(demographics.get("address")),
    )


def _external_claim_id(eob: dict[str, Any]) -> str:
    """The health system's claim id (``ExplanationOfBenefit/claim-1`` -> ``claim-1``)."""
    source_id = eob.get("sourceRecordId")
    if isinstance(source_id, str) and source_id:
        return source_id.rsplit("/", 1)[-1]
    return str(eob.get("id") or "unknown")


def _procedure_line(
    eob: dict[str, Any], claim_id: str, served: Any, billed_cents: int
) -> list[ClaimLine]:
    """One line for a real procedure code, billed the claim total.

    FinchNode explanations of benefit do not split the total across charges.
    Diagnosis-style codes stay off the line. The amount is the source total,
    not a guessed split.
    """
    chosen_code: str | None = None
    chosen_display: str | None = None
    for item in _items(eob.get("procedureCodes")):
        code = _text(item.get("code"))
        if not code:
            continue
        display = _text(item.get("display")) or code
        # ICD-10-PCS procedure codes are exactly 7 letters or digits.
        # Longer local codes such as "wellness-visit" are not a billed procedure.
        if re.fullmatch(r"[A-Za-z0-9]{7}", code) and chosen_code is None:
            chosen_code, chosen_display = code, display
    if not chosen_code or not chosen_display:
        return []
    return [
        ClaimLine(
            line_id=f"{claim_id}:1",
            claim_id=claim_id,
            line_number=1,
            code=CodeRef(system=CodeSystem.LOCAL, code=chosen_code, display=chosen_display),
            description=chosen_display[:500],
            date_of_service=served,
            units=1,
            billed_cents=billed_cents,
        )
    ]


def claims(snapshot: Snapshot) -> list[Claim]:
    """Claim summaries. A procedure code becomes one line at the claim total."""
    eobs, coverages = snapshot.claims_section()
    payer = _payer(_active_coverage(coverages))
    out = []
    for eob in eobs:
        period = eob.get("servicePeriod") or eob.get("billablePeriod") or {}
        served = safe_date(period.get("start") if isinstance(period, dict) else None)
        if served is None:
            continue  # nothing to place on a timeline
        billed = eob.get("providerBilled") or {}
        provider = _text(eob.get("providerName")) or _text(eob.get("sourceName"))
        claim_id = f"fn:{eob.get('id') or _external_claim_id(eob)}"
        billed_cents = to_cents(billed.get("amount") if isinstance(billed, dict) else None)
        out.append(
            Claim(
                claim_id=claim_id,
                external_claim_id=_external_claim_id(eob),
                payer_name=_text(eob.get("insurer")) or payer,
                provider_name=provider,
                facility_name=provider,
                date_of_service=served,
                total_billed_cents=billed_cents,
                source=ClaimSource.FINCHNODE,
                lines=_procedure_line(eob, claim_id, served, billed_cents),
            )
        )
    return out
