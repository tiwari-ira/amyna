"""[2.9] Property-based tests for the hand-rolled parsers and redaction regexes.

Hypothesis hunts for adversarial inputs the example-based tests never thought
of, and its per-example deadline doubles as a ReDoS check: any pathological
regex backtracking fails the run. Safety properties first: nothing that is not
the *whole message* command can ever approve, reject, finish or cancel.
"""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.audit import mask_member_ref, mask_phone
from app.services.security_policy import contains_sensitive_data, detect_sensitive_request

text = st.text(st.characters(), max_size=120)


@given(text)
@settings(max_examples=300)
def test_hedged_approvals_never_authorize(anything: str) -> None:
    """No prefix/suffix can turn 'I don't approve' into an approval."""
    from fetch.agents.intent import IntentKind, parse_intent

    for hedge in ("i dont approve", "do not approve", "not done yet", "never cancel"):
        assert parse_intent(f"{hedge} {anything}").kind not in (
            IntentKind.APPROVE,
            IntentKind.REJECT,
            IntentKind.DONE,
            IntentKind.CANCEL,
        )


@given(text)
@settings(max_examples=200)
def test_normalize_is_idempotent(anything: str) -> None:
    from fetch.agents.intent import normalize

    once = normalize(anything)
    assert normalize(once) == once


@given(st.text(min_size=1, max_size=20))
@settings(max_examples=300)
def test_masked_phone_numbers_never_leak_the_original(number: str) -> None:
    masked = mask_phone(number)
    assert masked is not None
    assert number not in masked


@given(st.text(min_size=1))
@settings(max_examples=200)
def test_masked_member_refs_never_leak_the_original(ref: str) -> None:
    assert ref not in mask_member_ref(ref)


@given(
    d3=st.integers(min_value=0, max_value=999),
    d2=st.integers(min_value=0, max_value=99),
    d4=st.integers(min_value=0, max_value=9999),
)
@settings(max_examples=200)
def test_every_ssn_shaped_string_is_flagged(d3: int, d2: int, d4: int) -> None:
    ssn = f"{d3:03d}-{d2:02d}-{d4:04d}"
    assert contains_sensitive_data(f"my ssn is {ssn}")


@given(text)
@settings(max_examples=200)
def test_sensitive_detection_never_crashes(anything: str) -> None:
    """A parser must never raise on untrusted input (and must stay fast)."""
    contains_sensitive_data(anything)
    detect_sensitive_request(anything)


@st.composite
def money_strings(draw):  # type: ignore[no-untyped-def]
    whole = draw(st.integers(min_value=0, max_value=10_000_000))
    cents = draw(st.integers(min_value=0, max_value=99))
    return f"{whole}.{cents:02d}"


@given(money_strings())
@settings(max_examples=300)
def test_to_cents_is_exact_decimal_money(amount: str) -> None:
    from finchnode.normalize import to_cents

    assert to_cents(amount) == int(Decimal(amount) * 100)
