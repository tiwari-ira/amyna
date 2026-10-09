"""Contract-level tests: messages, converters, registries, demo fixture."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from claimshield_contracts import agent_messages as am
from claimshield_contracts.api import ClaimIngest
from claimshield_contracts.demo import demo_patient, demo_visit_complete, load_demo_scenario
from claimshield_contracts.enums import (
    ClaimSource,
    DisputeAction,
    EvidenceKind,
    EvidenceSource,
    FindingStatus,
    IssueType,
    PatientAnswer,
)
from claimshield_contracts.models import (
    ConfirmationQuestion,
    DisputeCandidate,
    EvidenceItem,
    EvidenceRef,
)
from claimshield_contracts.tool_api import TOOL_SPECS, CoreTools, ToolName


def _finding() -> DisputeCandidate:
    return DisputeCandidate(
        finding_id="fnd_1",
        case_id="case_1",
        claim_id="clm_1",
        claim_line_id="lin_3",
        line_number=3,
        issue_type=IssueType.UNSUPPORTED_CHARGE,
        title="Anesthesia not supported",
        explanation="No anesthesia in visit evidence or records.",
        amount_cents=78000,
        confidence=0.85,
        human_confirmation_required=True,
        confirmation=ConfirmationQuestion(
            question="Did you receive anesthesia?",
            effects={
                PatientAnswer.YES: FindingStatus.DISMISSED,
                PatientAnswer.NO: FindingStatus.CONFIRMED,
                PatientAnswer.NOT_SURE: FindingStatus.NEEDS_CLARIFICATION,
            },
        ),
        status=FindingStatus.OPEN,
        recommended_action=DisputeAction.REMOVE_CHARGE,
        evidence_refs=[
            EvidenceRef(
                source=EvidenceSource.VISIT_TRANSCRIPT, ref_id="trn_1", description="no mention"
            )
        ],
    )


def test_agent_message_json_round_trip() -> None:
    req = am.ClaimAnalysisRequest(request_id="req_1", case_id="case_1", claim_id="clm_1")
    again = am.ClaimAnalysisRequest.parse_raw(req.json())
    assert again == req
    assert again.schema_version == am.AGENT_PROTOCOL_VERSION


def test_agent_messages_are_pydantic_v1_models() -> None:
    # uagents' Model is pydantic.v1-based; our messages must be too (decision D1).
    from pydantic.v1 import BaseModel as V1BaseModel

    for message in am.ALL_MESSAGES:
        assert issubclass(message, V1BaseModel), message.__name__


def test_schema_digests_are_unique_and_stable() -> None:
    first = am.schema_digests()
    second = am.schema_digests()
    assert first == second
    assert len(set(first.values())) == len(first)
    assert all(d.startswith("model:") for d in first.values())


def test_finding_converter_keeps_typed_fields() -> None:
    msg = am.finding_to_msg(_finding())
    assert msg.issue_type == "unsupported_charge"
    assert msg.confirmation_question == "Did you receive anesthesia?"
    assert msg.evidence_refs[0].source == "visit_transcript"
    assert am.FindingMsg.parse_raw(msg.json()) == msg


def test_evidence_converter() -> None:
    item = EvidenceItem(
        evidence_id="evd_1",
        case_id="case_1",
        kind=EvidenceKind.MEDICATION,
        concept="lorazepam",
        description="lorazepam 1 mg IV",
        attributes={"dose": "1 mg", "route": "IV"},
        confidence=0.9,
        extractor="rules",
    )
    msg = am.evidence_to_msg(item)
    assert msg.kind == "medication" and msg.attributes["route"] == "IV"


def test_claim_ingest_requires_matching_payload() -> None:
    ClaimIngest(case_id="case_1", source=ClaimSource.FINCHNODE, finchnode_claim_id="CLM-1")
    with pytest.raises(ValidationError):
        ClaimIngest(case_id="case_1", source=ClaimSource.FINCHNODE)
    with pytest.raises(ValidationError):
        ClaimIngest(case_id="case_1", source=ClaimSource.UPLOAD)


def test_input_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ClaimIngest(case_id="c", source=ClaimSource.FINCHNODE, finchnode_claim_id="x", oops=1)


def test_tool_registry_matches_protocol() -> None:
    assert set(TOOL_SPECS) == set(ToolName)
    for name in ToolName:
        assert callable(getattr(CoreTools, name.value, None)), f"CoreTools missing {name}"


def test_demo_scenario_is_consistent() -> None:
    scenario = load_demo_scenario()
    patient = demo_patient()
    assert patient.finchnode_patient_id == "fn_pat_demo_001"
    body = demo_visit_complete()
    text = " ".join(s.text.lower() for s in body.segments)
    assert "lorazepam" in text and "contrast" in text
    assert "anesthesia" not in text  # the scenario hinges on this
    expected = scenario["expected"]
    assert expected["finding_count"] == len(expected["findings"])
    assert expected["total_flagged_cents"] == sum(f["amount_cents"] for f in expected["findings"])


def test_datetimes_serialize_with_timezone() -> None:
    finding = _finding().model_copy(update={"created_at": datetime(2026, 9, 15, tzinfo=UTC)})
    assert finding.model_dump(mode="json")["created_at"].endswith("Z")


def test_schema_digests_match_lock_file() -> None:
    """Any message change must be intentional: bump the version and rewrite the lock."""
    import json

    lock = json.loads(am.DIGEST_LOCK_PATH.read_text("utf-8"))
    assert lock["agent_protocol_version"] == am.AGENT_PROTOCOL_VERSION, (
        "AGENT_PROTOCOL_VERSION changed: run "
        "`python -c 'from claimshield_contracts.agent_messages import write_digest_lock; "
        "write_digest_lock()'`"
    )
    assert lock["digests"] == am.schema_digests(), (
        "uAgents message schemas changed. Bump AGENT_PROTOCOL_VERSION in version.py, then run "
        "`python -c 'from claimshield_contracts.agent_messages import write_digest_lock; "
        "write_digest_lock()'` and restart all agents together."
    )
