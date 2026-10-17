"""Discrepancy engine: billed claim lines vs FinchNode records vs visit evidence.

Pure and deterministic. Produces typed ``DisputeCandidate`` findings (with
evidence refs, confidence, and a patient question when the patient can settle
it) plus a ``LineAssessment`` for every line.

Rules per line, in order:
1. ``duplicate_charge``  same code / date / units / amount / modifiers as an earlier line
2. facility lines        supported by a FinchNode encounter (or the reviewed visit) that day
3. drug lines            unclassified codes -> ``unknown_medication`` (ask the patient);
                         otherwise must match a documented medication concept
4. procedures, services, anesthesia
                         source-record codes first, then concepts; same family but a
                         different concept -> ``code_mismatch``; nothing -> ``unsupported_charge``
5. ``price_above_published``  billed above the hospital's own published gross charge
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from claimshield_contracts.enums import (
    CodeSystem,
    DisputeAction,
    EvidenceKind,
    EvidenceSource,
    FindingStatus,
    IssueType,
    LineMatchStatus,
    PatientAnswer,
    RecordType,
)
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import (
    Claim,
    ClaimLine,
    ConfirmationQuestion,
    DisputeCandidate,
    EvidenceItem,
    EvidenceRef,
    HealthRecord,
    LineAssessment,
)

from ..codes.hospital_ingest import HospitalPriceIndex
from ..codes.resolver import CodeEntry, CodeResolver

PATIENT_CONFIRMABLE = frozenset(
    {IssueType.UNSUPPORTED_CHARGE, IssueType.UNKNOWN_MEDICATION, IssueType.CODE_MISMATCH}
)
UNCLASSIFIED_DRUG_CONCEPTS = frozenset({"unclassified_drug"})
DEFAULT_EFFECTS = {
    PatientAnswer.YES: FindingStatus.DISMISSED,
    PatientAnswer.NO: FindingStatus.CONFIRMED,
    PatientAnswer.NOT_SURE: FindingStatus.NEEDS_CLARIFICATION,
}
RECORD_TEXT_MIN_CONFIDENCE = 0.85


@dataclass(frozen=True)
class EngineConfig:
    confirmation_threshold: float = 0.9
    min_support_confidence: float = 0.6
    duplicate_confidence: float = 0.97
    unknown_medication_confidence: float = 0.55
    undocumented_drug_confidence: float = 0.7
    code_mismatch_confidence: float = 0.75
    price_confidence: float = 0.8


@dataclass(frozen=True)
class AnalysisResult:
    findings: list[DisputeCandidate]
    line_assessments: list[LineAssessment]

    @property
    def total_flagged_cents(self) -> int:
        return sum(f.amount_cents for f in self.findings)


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def spoken_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


# --------------------------------------------------------------------------- support index

Dated = tuple[EvidenceRef, date | None]


@dataclass
class _Support:
    """What the records and the visit say happened, indexed for matching."""

    visit_ref: EvidenceRef | None = None
    by_code: dict[tuple[CodeSystem, str], list[Dated]] = field(
        default_factory=lambda: defaultdict(list)
    )
    by_concept: dict[str, list[Dated]] = field(default_factory=lambda: defaultdict(list))
    by_family: dict[str, list[tuple[str, Dated]]] = field(default_factory=lambda: defaultdict(list))
    medications: dict[str, list[Dated]] = field(default_factory=lambda: defaultdict(list))
    encounters: dict[date, list[EvidenceRef]] = field(default_factory=lambda: defaultdict(list))
    record_dates: set[date] = field(default_factory=set)
    negated: set[str] = field(default_factory=set)
    #: False when FinchNode could not provide every record type completely: then the
    #: absence of a record proves nothing and is never cited as evidence.
    records_complete: bool = True

    def records_checked(self, day: date) -> bool:
        """FinchNode records can testify about ``day`` (complete, and something that day)."""
        return self.records_complete and day in self.record_dates

    def add_entry(self, entry: CodeEntry, dated: Dated, *, medication: bool) -> None:
        self.by_concept[entry.concept].append(dated)
        self.by_family[entry.family].append((entry.concept, dated))
        if medication:
            self.medications[entry.concept].append(dated)

    @staticmethod
    def on(items: list[Dated], day: date) -> list[EvidenceRef]:
        return [ref for ref, when in items if when is None or when == day]

    def code_refs(self, line: ClaimLine) -> list[EvidenceRef]:
        return self.on(
            self.by_code.get((line.code.system, line.code.code.upper()), []), line.date_of_service
        )

    def concept_refs(self, concept: str, day: date) -> list[EvidenceRef]:
        return self.on(self.by_concept.get(concept, []), day)

    def family_alternatives(self, entry: CodeEntry, day: date) -> list[tuple[str, EvidenceRef]]:
        return [
            (concept, ref)
            for concept, (ref, when) in self.by_family.get(entry.family, [])
            if concept != entry.concept and (when is None or when == day)
        ]


def _build_support(
    records: list[HealthRecord],
    evidence: list[EvidenceItem],
    resolver: CodeResolver,
    visit_id: str | None,
    config: EngineConfig,
    records_complete: bool = True,
) -> _Support:
    support = _Support(records_complete=records_complete)
    if visit_id:
        support.visit_ref = EvidenceRef(
            source=EvidenceSource.VISIT_TRANSCRIPT,
            ref_id=visit_id,
            description="Recorded and transcribed visit",
        )
    for record in records:
        if record.occurred_on:
            support.record_dates.add(record.occurred_on)
        ref = EvidenceRef(
            source=EvidenceSource.FINCHNODE_RECORD,
            ref_id=record.record_id,
            description=(
                f"FinchNode {record.record_type.value}: {record.display} ({record.occurred_on})"
            ),
        )
        dated: Dated = (ref, record.occurred_on)
        if record.record_type == RecordType.ENCOUNTER:
            if record.occurred_on:
                support.encounters[record.occurred_on].append(ref)
            # A coded visit type (e.g. a wellness visit) supports the visit's own E/M line.
            for code in record.codes:
                support.by_code[(code.system, code.code.upper())].append(dated)
                if entry := resolver.entry_for(code):
                    support.add_entry(entry, dated, medication=False)
            continue
        for code in record.codes:  # prefer codes already present in source records
            support.by_code[(code.system, code.code.upper())].append(dated)
        entries = [entry for code in record.codes if (entry := resolver.entry_for(code))]
        if not entries:
            top = next(iter(resolver.resolve_text(record.display, k=1)), None)
            if top and top.confidence >= RECORD_TEXT_MIN_CONFIDENCE:
                entries = [e for e in [resolver.table.get(top.system, top.code)] if e]
        for entry in entries:
            support.add_entry(entry, dated, medication=record.record_type == RecordType.MEDICATION)
    for item in evidence:
        if item.confidence < config.min_support_confidence or not item.concept:
            continue
        if item.negated:
            support.negated.add(item.concept)
            continue
        ref = EvidenceRef(
            source=item.source,
            ref_id=item.evidence_id,
            description=f"Visit evidence: {item.description}",
            quote=item.quote,
        )
        medication = item.kind in (EvidenceKind.MEDICATION, EvidenceKind.SUPPLY)
        for entry in resolver.table.by_concept(item.concept)[:1]:
            support.add_entry(entry, (ref, None), medication=medication)
    return support


# --------------------------------------------------------------------------- engine


class _Analysis:
    def __init__(
        self,
        claim: Claim,
        support: _Support,
        resolver: CodeResolver,
        price_index: HospitalPriceIndex | None,
        config: EngineConfig,
    ) -> None:
        self.claim = claim
        self.support = support
        self.resolver = resolver
        self.price_index = price_index
        self.config = config
        self.findings: list[DisputeCandidate] = []
        self.assessments: list[LineAssessment] = []
        self.billed_concepts = {
            entry.concept for line in claim.lines if (entry := resolver.entry_for(line.code))
        }

    # -- helpers ------------------------------------------------------------------

    def plain_label(self, concept: str) -> str:
        entries = self.resolver.table.by_concept(concept)
        return entries[0].plain_label if entries else concept.replace("_", " ")

    def assess(
        self,
        line: ClaimLine,
        status: LineMatchStatus,
        entry: CodeEntry | None,
        refs: list[EvidenceRef],
        note: str | None = None,
    ) -> None:
        self.assessments.append(
            LineAssessment(
                claim_line_id=line.line_id,
                line_number=line.line_number,
                status=status,
                concept=entry.concept if entry else None,
                evidence_refs=refs,
                note=note,
            )
        )

    def flag(
        self,
        line: ClaimLine,
        *,
        issue: IssueType,
        title: str,
        explanation: str,
        confidence: float,
        action: DisputeAction,
        refs: list[EvidenceRef],
        question: str | None = None,
    ) -> None:
        needs_patient = (
            issue in PATIENT_CONFIRMABLE
            and question is not None
            and confidence < self.config.confirmation_threshold
        )
        self.findings.append(
            DisputeCandidate(
                finding_id=new_id("finding"),
                case_id=self.claim.case_id or "",
                claim_id=self.claim.claim_id,
                claim_line_id=line.line_id,
                line_number=line.line_number,
                issue_type=issue,
                title=title,
                explanation=explanation,
                amount_cents=line.billed_cents,
                confidence=round(confidence, 2),
                human_confirmation_required=needs_patient,
                confirmation=(
                    ConfirmationQuestion(question=question, effects=DEFAULT_EFFECTS)
                    if needs_patient and question
                    else None
                ),
                status=FindingStatus.OPEN if needs_patient else FindingStatus.CONFIRMED,
                recommended_action=action,
                evidence_refs=refs,
                # task 12: patient-facing wording is derived from the structured
                # finding -- never invented by the language layer later.
                patient_facing_explanation=f"{title}. {explanation}",
            )
        )

    def billed_ref(self, line: ClaimLine) -> EvidenceRef:
        return EvidenceRef(
            source=EvidenceSource.CLAIM,
            ref_id=line.line_id,
            description=(
                f"Billed line {line.line_number}: {line.code.system.value} {line.code.code} "
                f"{line.description} ({money(line.billed_cents)})"
            ),
        )

    def absence_confidence(self, line: ClaimLine, entry: CodeEntry) -> float:
        if entry.concept in self.support.negated:
            return 0.95
        sources = int(self.support.visit_ref is not None) + int(
            self.support.records_checked(line.date_of_service)
        )
        return {2: 0.85, 1: 0.7}.get(sources, 0.5)

    def sources_checked(self, day: date) -> str:
        """Plain-language list of what was actually searched for ``day``."""
        checked = []
        if self.support.visit_ref:
            checked.append("the visit recording")
        if self.support.records_checked(day):
            checked.append(f"your FinchNode records for {day}")
        if not checked:
            return "the records Amyna could load"
        return " or ".join(checked)

    def not_mentioned(self, day: date, label: str) -> str:
        """ "neither X nor Y mention <label>" built from what was actually checked."""
        visit = self.support.visit_ref is not None
        records = self.support.records_checked(day)
        if visit and records:
            return (
                f"neither the visit recording nor your FinchNode records for {day} mention {label}"
            )
        if visit:
            return f"the visit recording doesn't mention {label}"
        if records:
            return f"your FinchNode records for {day} don't mention {label}"
        return f"nothing Amyna could check mentions {label}"

    def incomplete_note(self) -> str:
        return (
            ""
            if self.support.records_complete
            else " Some of your FinchNode records could not be loaded, so they were not checked."
        )

    def checked_refs(self, line: ClaimLine, label: str) -> list[EvidenceRef]:
        """Evidence that was searched and did not mention the billed item."""
        refs = [self.billed_ref(line)]
        if self.support.visit_ref:
            refs.append(
                self.support.visit_ref.model_copy(
                    update={"description": f"Visit transcript reviewed: no mention of {label}"}
                )
            )
        if self.support.records_checked(line.date_of_service):
            refs.append(
                EvidenceRef(
                    source=EvidenceSource.FINCHNODE_RECORD,
                    ref_id=f"finchnode:{line.date_of_service.isoformat()}",
                    description=f"FinchNode records for {line.date_of_service}: no {label}",
                )
            )
        return refs

    # -- rules --------------------------------------------------------------------

    def unknown(self, line: ClaimLine) -> None:
        """A billed code that is not in the reference table still gets a question.

        The wording comes from the line the payer sent. It is not assumed to be wrong.
        """
        self.assess(line, LineMatchStatus.UNKNOWN, None, [], note="code not in reference table")
        label = line.description.strip() or f"{line.code.system.value} {line.code.code}"
        self.flag(
            line,
            issue=IssueType.UNSUPPORTED_CHARGE,
            title=f"{label} is not in the code table",
            explanation=(
                f"Line {line.line_number} bills {line.code.system.value} {line.code.code} "
                f"({label}) for {money(line.billed_cents)}. That code is not in the reference "
                "table, so it was not matched to the visit."
            ),
            confidence=0.7,
            action=DisputeAction.REMOVE_CHARGE,
            refs=[self.billed_ref(line)],
            question=(
                f"Did you receive {label} during your visit on {spoken_date(line.date_of_service)}?"
            ),
        )

    def duplicate(self, line: ClaimLine, first: ClaimLine, entry: CodeEntry | None) -> None:
        self.assess(line, LineMatchStatus.DUPLICATE, entry, [self.billed_ref(first)])
        self.flag(
            line,
            issue=IssueType.DUPLICATE_CHARGE,
            title=f"Duplicate charge: {line.description}",
            explanation=(
                f"Line {line.line_number} repeats line {first.line_number} "
                f"({line.code.system.value} {line.code.code} on {line.date_of_service}, "
                f"{line.units} unit(s), {money(line.billed_cents)}) with no modifier that "
                "explains a second, separate service."
            ),
            confidence=self.config.duplicate_confidence,
            action=DisputeAction.REMOVE_CHARGE,
            refs=[self.billed_ref(line), self.billed_ref(first)],
        )

    def facility(self, line: ClaimLine, entry: CodeEntry) -> None:
        refs = list(self.support.encounters.get(line.date_of_service, []))
        if self.support.visit_ref:
            refs.append(self.support.visit_ref)
        if refs:
            self.assess(line, LineMatchStatus.MATCHED, entry, refs)
            return
        place = self.claim.facility_name or "this facility"
        self.assess(line, LineMatchStatus.UNSUPPORTED, entry, [])
        self.flag(
            line,
            issue=IssueType.UNSUPPORTED_CHARGE,
            title=f"{line.description} without a documented visit",
            explanation=(
                f"No visit at {place} on {line.date_of_service} appears in "
                f"{self.sources_checked(line.date_of_service)}.{self.incomplete_note()}"
            ),
            confidence=self.absence_confidence(line, entry),
            action=DisputeAction.REMOVE_CHARGE,
            refs=self.checked_refs(line, "visit"),
            question=f"Did you have a visit at {place} on {spoken_date(line.date_of_service)}?",
        )

    def drug(self, line: ClaimLine, entry: CodeEntry) -> None:
        unclassified = entry.concept in UNCLASSIFIED_DRUG_CONCEPTS
        if not unclassified:
            refs = self.support.code_refs(line) or self.support.concept_refs(
                entry.concept, line.date_of_service
            )
            if refs:
                self.assess(line, LineMatchStatus.MATCHED, entry, refs)
                return
        documented = list(self.support.medications)  # record order (FinchNode first)
        labels = [self.plain_label(c) for c in documented]
        unbilled = [self.plain_label(c) for c in documented if c not in self.billed_concepts]
        when = spoken_date(line.date_of_service)
        billed = f"{line.code.system.value} {line.code.code} ({line.description})"
        if unclassified:
            what = "without naming the drug"
            question = (
                f"Your bill includes a {money(line.billed_cents)} charge for an unspecified drug. "
                f"Other than {' and '.join(labels)}, did you receive any other medication during "
                f"your visit on {when}?"
                if labels
                else f"Did you receive any medication during your visit on {when}?"
            )
            confidence = self.config.unknown_medication_confidence
        else:
            what = "but no matching medication is documented"
            question = f"Did you receive {entry.plain_label} during your visit on {when}?"
            confidence = self.config.undocumented_drug_confidence
        explanation = (
            f"Line {line.line_number} bills {money(line.billed_cents)} under {billed} {what}. "
            f"Documented medications: {', '.join(labels) if labels else 'none'}."
            + (f" Possibly {', '.join(unbilled)}, which has no separate line." if unbilled else "")
            + self.incomplete_note()
        )
        self.assess(line, LineMatchStatus.UNKNOWN, entry, [], note="drug not identified")
        self.flag(
            line,
            issue=IssueType.UNKNOWN_MEDICATION,
            title=f"Unidentified drug charge ({line.code.code})",
            explanation=explanation,
            confidence=confidence,
            action=DisputeAction.REQUEST_ITEMIZATION,
            refs=[self.billed_ref(line)],
            question=question,
        )

    def service(self, line: ClaimLine, entry: CodeEntry) -> None:
        refs = self.support.code_refs(line) or self.support.concept_refs(
            entry.concept, line.date_of_service
        )
        if refs:
            self.assess(line, LineMatchStatus.MATCHED, entry, refs)
            return
        alternatives = self.support.family_alternatives(entry, line.date_of_service)
        if alternatives:
            concept, ref = alternatives[0]
            documented = self.plain_label(concept)
            self.assess(line, LineMatchStatus.MISMATCH, entry, [ref])
            self.flag(
                line,
                issue=IssueType.CODE_MISMATCH,
                title=f"Billed {line.description}, records show {documented}",
                explanation=(
                    f"Line {line.line_number} bills {line.code.system.value} {line.code.code} "
                    f"({line.description}), but your records document {documented}."
                ),
                confidence=self.config.code_mismatch_confidence,
                action=DisputeAction.CORRECT_CODE,
                refs=[self.billed_ref(line), ref],
                question=(
                    f"Your bill lists {entry.plain_label}, but your records show {documented}. "
                    f"Did you receive {entry.plain_label} on {spoken_date(line.date_of_service)}?"
                ),
            )
            return
        self.assess(line, LineMatchStatus.UNSUPPORTED, entry, [])
        self.flag(
            line,
            issue=IssueType.UNSUPPORTED_CHARGE,
            title=f"{line.description} not supported by records",
            explanation=(
                f"Line {line.line_number} bills {line.code.system.value} {line.code.code} "
                f"({line.description}) for {money(line.billed_cents)}, but "
                f"{self.not_mentioned(line.date_of_service, entry.plain_label)}."
                f"{self.incomplete_note()}"
            ),
            confidence=self.absence_confidence(line, entry),
            action=DisputeAction.REMOVE_CHARGE,
            refs=self.checked_refs(line, entry.plain_label),
            question=(
                f"Did you receive {entry.plain_label} during your visit on "
                f"{spoken_date(line.date_of_service)}?"
            ),
        )

    def price(self, line: ClaimLine, entry: CodeEntry | None) -> None:
        if self.price_index is None or any(f.claim_line_id == line.line_id for f in self.findings):
            return
        gross = self.price_index.gross_charge_cents(line.code.system, line.code.code)
        if gross is None or line.billed_cents <= gross * line.units:
            return
        self.assessments = [
            a.model_copy(update={"status": LineMatchStatus.PRICE_OUTLIER})
            if a.claim_line_id == line.line_id
            else a
            for a in self.assessments
        ]
        self.flag(
            line,
            issue=IssueType.PRICE_ABOVE_PUBLISHED,
            title=f"Billed above published price: {line.description}",
            explanation=(
                f"Billed {money(line.billed_cents)}; {self.price_index.hospital_name} publishes a "
                f"gross charge of {money(gross)} per unit."
            ),
            confidence=self.config.price_confidence,
            action=DisputeAction.REVIEW_PRICE,
            refs=[self.billed_ref(line)],
        )

    def run(self) -> AnalysisResult:
        first_seen: dict[tuple, ClaimLine] = {}
        for line in sorted(self.claim.lines, key=lambda item: item.line_number):
            entry = self.resolver.entry_for(line.code)
            key = (
                line.code.system,
                line.code.code.upper(),
                line.date_of_service,
                line.units,
                line.billed_cents,
                tuple(sorted(line.modifiers)),
            )
            if key in first_seen:
                self.duplicate(line, first_seen[key], entry)
                continue
            first_seen[key] = line
            if entry is None:
                self.unknown(line)
                continue
            if entry.category == "facility":
                self.facility(line, entry)
            elif entry.category == "drug":
                self.drug(line, entry)
            else:
                self.service(line, entry)
            self.price(line, entry)
        return AnalysisResult(
            findings=sorted(self.findings, key=lambda f: f.line_number),
            line_assessments=sorted(self.assessments, key=lambda a: a.line_number),
        )


def analyze_claim(
    claim: Claim,
    records: list[HealthRecord],
    evidence: list[EvidenceItem],
    *,
    resolver: CodeResolver,
    visit_id: str | None = None,
    price_index: HospitalPriceIndex | None = None,
    config: EngineConfig | None = None,
    records_complete: bool = True,
) -> AnalysisResult:
    """Compare every claim line against records + visit evidence (deterministic).

    ``records_complete=False`` (FinchNode reported missing or partial categories)
    keeps the comparison but never cites the records' silence as evidence."""
    config = config or EngineConfig()
    support = _build_support(records, evidence, resolver, visit_id, config, records_complete)
    return _Analysis(claim, support, resolver, price_index, config).run()
