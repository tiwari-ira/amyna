"""Intent rules and chat session persistence."""

import pytest

from fetch.agents.intent import IntentKind, parse_intent
from fetch.agents.session import (
    ChatSession,
    MemoryStore,
    PendingRequest,
    Stage,
    load_session,
    owner_of_request,
    pending_senders,
    save_session,
)


@pytest.mark.parametrize(
    ("text", "kind", "answer"),
    [
        ("yes", IntentKind.ANSWER, "YES"),
        ("Yes, I consent", IntentKind.ANSWER, "YES"),
        ("No", IntentKind.ANSWER, "NO"),
        ("no, I did not get that", IntentKind.ANSWER, "NO"),
        ("Not sure", IntentKind.ANSWER, "NOT_SURE"),
        ("I'm not sure", IntentKind.ANSWER, "NOT_SURE"),
        ("APPROVE", IntentKind.APPROVE, None),
        ("I approve", IntentKind.APPROVE, None),
        ("reject", IntentKind.REJECT, None),
        ("done", IntentKind.DONE, None),
        ("cancel the call", IntentKind.CANCEL, None),
        ("status", IntentKind.STATUS, None),
        ("restart", IntentKind.RESTART, None),
        ("hello there", IntentKind.GREETING, None),
        ("can you review my bill?", IntentKind.REVIEW_BILL, None),
    ],
)
def test_intents(text: str, kind: IntentKind, answer: str | None) -> None:
    intent = parse_intent(text)
    assert intent.kind == kind and intent.answer == answer


@pytest.mark.parametrize(
    "text",
    [
        "I don't approve",
        "I did not approve that",
        "approve the call",
        "yes, I approve",
        "not done yet",
        "please don't cancel",
        "I'm not finished",
    ],
)
def test_safety_commands_need_the_exact_word(text: str) -> None:
    assert parse_intent(text).kind == IntentKind.UNKNOWN


def test_hedged_answers_are_not_taken_at_face_value() -> None:
    assert parse_intent("yes, but I'm not sure").answer == "NOT_SURE"
    assert parse_intent("yes but only once").kind == IntentKind.UNKNOWN


def test_ids_are_extracted() -> None:
    intent = parse_intent("Patient fn_pat_demo_001, claim clm-2026-0915-001 please")
    assert intent.patient_id == "fn_pat_demo_001"
    assert intent.claim_id == "CLM-2026-0915-001"


def test_session_round_trip_and_request_index() -> None:
    store = MemoryStore()
    session = ChatSession(sender="agent1abc", stage=Stage.ANALYZING, case_id="case_1")
    session.pending.append(PendingRequest("req_1", "claim_agent", "ClaimAnalysisRequest", 1.0))
    save_session(store, session)
    loaded = load_session(store, "agent1abc")
    assert loaded == session
    assert owner_of_request(store, "req_1") == "agent1abc"
    assert pending_senders(store) == ["agent1abc"]
    loaded.pending.clear()
    save_session(store, loaded)
    assert pending_senders(store) == []


def test_reset_keeps_sender() -> None:
    session = ChatSession(sender="s", stage=Stage.DONE, case_id="case_1")
    session.reset()
    assert session == ChatSession(sender="s")
