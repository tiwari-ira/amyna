"""Prefixed, URL-safe identifiers (``case_3f9a…``) used across all services."""

from uuid import uuid4

ID_PREFIXES = {
    "case": "case",
    "consent": "cns",
    "visit": "vis",
    "transcript": "trn",
    "segment": "seg",
    "evidence": "evd",
    "claim": "clm",
    "line": "lin",
    "finding": "fnd",
    "assessment": "las",
    "dispute": "dsp",
    "intervention": "hin",
    "decision": "hdc",
    "call": "call",
    "agent_run": "run",
    "notification": "ntf",
    "request": "req",
    "audit": "aud",
    "user": "user",
    "finch_link": "flink",
    "job": "job",
}


def new_id(kind: str) -> str:
    """Return a new id such as ``fnd_1a2b3c4d5e6f7a8b`` for the given kind."""
    prefix = ID_PREFIXES[kind]
    return f"{prefix}_{uuid4().hex[:16]}"
