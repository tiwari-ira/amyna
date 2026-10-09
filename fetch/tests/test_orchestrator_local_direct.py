"""The ASI:One conversation end to end via LOCAL_DIRECT (no Agentverse)."""

from typing import Any

import pytest

from claimshield_contracts.agent_messages import (
    ClaimAnalysisRequest,
    VisitEvidenceRequest,
)
from claimshield_contracts.enums import AgentName
from claimshield_contracts.tool_api import CoreToolError, PatientDataRequest
from fetch.agents.core_client import CoreClient
from fetch.agents.local_direct import DEMO_CONVERSATION, LocalDirectRunner
from fetch.agents.logic.claim_logic import handle_claim_analysis_request
from fetch.agents.logic.visit_logic import handle_visit_evidence_request
from fetch.agents.session import Stage, save_session

from .conftest import agent_settings


@pytest.fixture
def runner(agent_env: None, core: dict[str, Any]) -> LocalDirectRunner:
    return LocalDirectRunner(settings=agent_settings(), http=core["client"])


def _case(core: dict[str, Any]) -> dict[str, Any]:
    return core["client"].get(f"/cases/{core['case_id']}", headers=core["relay"]).json()


def test_demo_conversation_resolves(runner: LocalDirectRunner, core: dict[str, Any]) -> None:
    transcript = [runner.send(text) for text in DEMO_CONVERSATION]
    assert "permission" in transcript[0][0]
    assert any("Did you receive anesthesia" in r for r in transcript[1])
    assert any("unspecified drug" in r for r in transcript[2])
    assert any("prepare a dispute package" in r for r in transcript[3])
    assert any("APPROVE" in r for r in transcript[4])
    assert any("Social Security number" in r and "paused" in r for r in transcript[5])
    assert "MOCK-REF-48213" in transcript[6][-1]
    case = _case(core)
    assert case["state"] == "RESOLVED"
    consent = [c for c in case["consents"] if c["consent_type"] == "health_data_access"][0]
    assert consent["channel"] == "asi1" and "Patient replied" in consent["consent_text"]
    approval = next(i for i in case["interventions"] if i["reason"] == "identity_verification")
    assert approval["resolution"]["channel"] == "asi1"
    agents = {run["agent"] for run in case["agent_runs"]}
    assert {"orchestrator", "visit_agent", "claim_agent", "dispute_agent"} <= agents


def test_consent_refusal_stops_without_reading_records(
    runner: LocalDirectRunner, core: dict[str, Any]
) -> None:
    runner.send(DEMO_CONVERSATION[0])
    replies = runner.send("No")
    assert "won't access" in replies[0]
    case = _case(core)
    assert case["claim"] is None and not any(
        c["consent_type"] == "health_data_access" for c in case["consents"]
    )


@pytest.mark.parametrize("text", ["yes", "I don't approve", "yes, I approve the letter only"])
def test_only_an_exact_approve_authorizes(
    runner: LocalDirectRunner, core: dict[str, Any], text: str
) -> None:
    for message in DEMO_CONVERSATION[:5]:
        runner.send(message)
    assert runner.session.stage == Stage.AWAITING_APPROVAL
    replies = runner.send(text)
    assert "reply exactly **APPROVE**" in replies[0]
    assert runner.session.stage == Stage.AWAITING_APPROVAL
    case = _case(core)
    assert case["state"] == "AWAITING_APPROVAL" and case["dispute"]["status"] == "awaiting_approval"


def test_a_new_chat_resumes_the_case_where_it_stands(
    runner: LocalDirectRunner, agent_env: None, core: dict[str, Any]
) -> None:
    for message in DEMO_CONVERSATION[:5]:
        runner.send(message)
    second = LocalDirectRunner(settings=agent_settings(), http=core["client"], sender="new-chat")
    replies = second.send(DEMO_CONVERSATION[0])
    assert second.session.case_id == runner.session.case_id
    assert second.session.stage == Stage.AWAITING_APPROVAL
    assert any("APPROVE" in reply for reply in replies)


def test_a_different_claim_gets_its_own_case(
    runner: LocalDirectRunner, agent_env: None, core: dict[str, Any]
) -> None:
    runner.send(DEMO_CONVERSATION[0])
    second = LocalDirectRunner(settings=agent_settings(), http=core["client"], sender="other")
    second.send("Please review fn_pat_demo_001 claim CLM-2026-0999-999")
    assert second.session.case_id not in (None, runner.session.case_id)


def test_a_call_running_in_the_background_reports_from_core(runner: LocalDirectRunner) -> None:
    """Live voice runs the call on a background thread: CALLING re-reads Core on any message."""
    for message in DEMO_CONVERSATION[:6]:
        runner.send(message)
    session = runner.session
    session.stage = Stage.CALLING  # as if the call request returned before the SSN pause
    save_session(runner.store, session)
    replies = runner.send("any update?")
    assert any("Social Security number" in reply for reply in replies)
    assert "MOCK-REF-48213" in runner.send("Done")[-1]


def test_rejecting_the_dispute(runner: LocalDirectRunner, core: dict[str, Any]) -> None:
    for text in DEMO_CONVERSATION[:5]:
        runner.send(text)
    assert "not sent" in runner.send("REJECT")[0]
    assert _case(core)["state"] == "REJECTED"


def test_status_and_restart(runner: LocalDirectRunner) -> None:
    assert "No bill under review" in runner.send("status")[0]
    runner.send(DEMO_CONVERSATION[0])
    assert "AWAITING" in runner.send("status")[0] or "EVIDENCE" in runner.send("status")[0]
    assert "Amyna" in runner.send("restart")[0]
    assert runner.session.case_id is None


def test_timeouts_produce_an_error_reply(runner: LocalDirectRunner) -> None:
    runner.send(DEMO_CONVERSATION[0])
    session = runner.session
    session.pending = []
    runner.orchestrator.clock = lambda: 0.0
    from fetch.agents.logic.orchestrate import Step

    step = Step()
    runner.orchestrator._send(  # noqa: SLF001 - simulate a request that never got a reply
        session,
        step,
        AgentName.CLAIM_AGENT,
        ClaimAnalysisRequest(request_id="req_late", case_id=session.case_id or ""),
    )
    runner.orchestrator.clock = lambda: 10_000.0
    timed_out = runner.orchestrator.on_timeouts(session)
    assert "didn't answer in time" in timed_out.replies[0]
    assert session.stage == Stage.ERROR and session.pending == []


def test_specialist_logic_is_idempotent(agent_env: None, core: dict[str, Any]) -> None:
    runner = LocalDirectRunner(settings=agent_settings(), http=core["client"])
    for text in DEMO_CONVERSATION[:2]:
        runner.send(text)
    tools = CoreClient(
        "", agent_settings().fetch_token, agent=AgentName.VISIT_AGENT, http=core["client"]
    )
    visit = handle_visit_evidence_request(
        tools, VisitEvidenceRequest(request_id="req_again", case_id=core["case_id"])
    )
    assert visit.ok and {e.concept for e in visit.evidence} >= {"lorazepam"}
    claim_tools = CoreClient(
        "", agent_settings().fetch_token, agent=AgentName.CLAIM_AGENT, http=core["client"]
    )
    first = handle_claim_analysis_request(
        claim_tools, ClaimAnalysisRequest(request_id="req_a", case_id=core["case_id"])
    )
    second = handle_claim_analysis_request(
        claim_tools, ClaimAnalysisRequest(request_id="req_b", case_id=core["case_id"])
    )
    assert [f.finding_id for f in first.findings] == [f.finding_id for f in second.findings]


def test_agents_cannot_use_tools_outside_their_allowlist(
    agent_env: None, core: dict[str, Any]
) -> None:
    token = agent_settings().fetch_token
    visit_tools = CoreClient("", token, agent=AgentName.VISIT_AGENT, http=core["client"])
    with pytest.raises(CoreToolError) as excinfo:  # FinchNode data is Claim-Agent-only
        visit_tools.get_patient_procedures(PatientDataRequest(case_id=core["case_id"]))
    assert excinfo.value.status_code == 403 and excinfo.value.code == "forbidden"
