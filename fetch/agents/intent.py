"""Rule-based intent routing for chat messages (no LLM needed).

Safety-relevant commands (APPROVE / REJECT / DONE / CANCEL) are matched only when
the WHOLE message is the command, so "I don't approve" or "not done yet" can
never authorize a call or resume one. Anything else is UNKNOWN and re-prompted.
An LLM fallback can be added for UNKNOWN intents later; the rules cover the demo.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

# VERIFY against current docs: the real FinchNode patient-id format.
PATIENT_ID = re.compile(r"\bfn_pat_[A-Za-z0-9_]+\b")
CLAIM_ID = re.compile(r"\bCLM-[A-Za-z0-9-]+\b", re.IGNORECASE)


class IntentKind(StrEnum):
    GREETING = "greeting"
    REVIEW_BILL = "review_bill"
    ANSWER = "answer"
    APPROVE = "approve"
    REJECT = "reject"
    DONE = "done"
    CANCEL = "cancel"
    STATUS = "status"
    HELP = "help"
    RESTART = "restart"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    answer: str | None = None
    patient_id: str | None = None
    claim_id: str | None = None


EXACT_COMMANDS: dict[str, IntentKind] = {
    **dict.fromkeys(("restart", "start over", "reset"), IntentKind.RESTART),
    **dict.fromkeys(("help", "what can you do"), IntentKind.HELP),
    **dict.fromkeys(("status", "update", "where are we"), IntentKind.STATUS),
    **dict.fromkeys(("approve", "approved", "i approve"), IntentKind.APPROVE),
    **dict.fromkeys(("reject", "rejected", "i reject", "decline"), IntentKind.REJECT),
    **dict.fromkeys(
        ("done", "verified", "finished", "im done", "i am done", "all done"), IntentKind.DONE
    ),
    **dict.fromkeys(("cancel", "cancel call", "cancel the call", "hang up"), IntentKind.CANCEL),
}
ANSWERS: dict[str, str] = {
    **dict.fromkeys(("yes", "yeah", "yep", "y", "sure", "correct", "ok", "okay"), "YES"),
    **dict.fromkeys(("yes i consent", "i consent", "i agree", "yes please"), "YES"),
    **dict.fromkeys(("no", "nope", "n", "i did not", "i didnt", "never", "no i did not"), "NO"),
    **dict.fromkeys(
        ("not sure", "unsure", "im not sure", "i dont know", "dont know", "no idea"), "NOT_SURE"
    ),
}
_SAFETY_WORDS = re.compile(r"\b(approve|approved|reject|done|verified|finished|cancel)\b")


def normalize(text: str) -> str:
    """Lowercase, drop apostrophes/punctuation, collapse spaces."""
    lowered = text.lower().replace("’", "'").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", lowered)).strip()


def _has(text: str, *phrases: str) -> bool:
    return any(re.search(rf"\b{re.escape(p)}\b", text) for p in phrases)


def _answer(normalized: str) -> str | None:
    """YES / NO / NOT_SURE, or None. A leading yes/no counts only if the rest of the
    message does not hedge or contradict it ("yes, but ..." is re-prompted)."""
    if normalized in ANSWERS:
        return ANSWERS[normalized]
    if _has(normalized, "not sure", "unsure", "dont know", "no idea"):
        return "NOT_SURE"
    first, _, rest = normalized.partition(" ")
    lead = ANSWERS.get(first)
    if lead in ("YES", "NO") and not _has(rest, "but", "except", "unless", "yes", "no"):
        return lead
    return None


def parse_intent(text: str) -> Intent:
    """Map a chat message to an intent plus any ids it contains."""
    raw = text.strip()
    normalized = normalize(raw)
    patient = PATIENT_ID.search(raw)
    claim = CLAIM_ID.search(raw)
    ids = {
        "patient_id": patient.group(0) if patient else None,
        "claim_id": claim.group(0).upper() if claim else None,
    }
    if normalized in EXACT_COMMANDS:
        return Intent(EXACT_COMMANDS[normalized], **ids)
    answer = _answer(normalized)
    if answer and not _SAFETY_WORDS.search(normalized):
        return Intent(IntentKind.ANSWER, answer=answer, **ids)
    if ids["patient_id"] or ids["claim_id"] or _has(normalized, "review", "bill", "claim"):
        return Intent(IntentKind.REVIEW_BILL, **ids)
    if _SAFETY_WORDS.search(normalized):
        return Intent(IntentKind.UNKNOWN, **ids)  # e.g. "I don't approve" -> re-prompt
    if _has(normalized, "hi", "hello", "hey"):
        return Intent(IntentKind.GREETING)
    return Intent(IntentKind.UNKNOWN, **ids)
