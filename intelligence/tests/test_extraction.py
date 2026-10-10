"""Rules extractor on the demo transcript, negation, questions, and the Ollama path."""

import json

import httpx
import pytest

from claimshield_contracts.enums import EvidenceKind, Speaker
from claimshield_contracts.models import Transcript
from intelligence.codes.resolver import CodeResolver
from intelligence.extraction.extractor import build_extractor, to_evidence_items
from intelligence.extraction.qwen_ollama import OllamaQwenExtractor
from intelligence.extraction.rule_based import RuleBasedExtractor


def _evidence(transcript: Transcript, resolver: CodeResolver, extractor=None):  # type: ignore[no-untyped-def]
    result = (extractor or RuleBasedExtractor()).extract(transcript)
    return to_evidence_items(
        result, transcript, case_id="case_demo", visit_id="vis_demo", resolver=resolver
    )


def _one_line(text: str, speaker: Speaker = Speaker.CLINICIAN) -> Transcript:
    return Transcript(
        transcript_id="t",
        visit_id="v",
        source="test",
        segments=[{"segment_id": "s1", "speaker": speaker, "text": text}],
    )


def test_demo_transcript_evidence(transcript: Transcript, resolver: CodeResolver) -> None:
    evidence = {item.concept: item for item in _evidence(transcript, resolver)}
    assert set(evidence) == {"mri_brain_contrast", "lorazepam", "gadolinium_contrast"}
    mri = evidence["mri_brain_contrast"]
    assert mri.kind == EvidenceKind.PROCEDURE and mri.codes[0].code == "70553"
    lorazepam = evidence["lorazepam"]
    assert lorazepam.attributes == {"dose": "1 mg", "route": "IV"}
    assert len(lorazepam.segment_ids) >= 3  # mentioned several times, merged
    assert "anesthesia_imaging" not in evidence


def test_negation_is_kept_as_negated_evidence(resolver: CodeResolver) -> None:
    [item] = _evidence(_one_line("You won't need anesthesia for this scan."), resolver)
    assert item.concept == "anesthesia_imaging" and item.negated


def test_questions_are_low_confidence(resolver: CodeResolver) -> None:
    [item] = _evidence(_one_line("Will I need anesthesia?", Speaker.PATIENT), resolver)
    assert item.confidence == 0.4


def test_doctor_abbreviation_does_not_split_sentences(resolver: CodeResolver) -> None:
    items = _evidence(_one_line("Dr. Kim ordered an MRI of the brain with contrast."), resolver)
    assert items[0].attributes["body_part"] == "brain"


def _ollama(handler) -> OllamaQwenExtractor:  # type: ignore[no-untyped-def]
    return OllamaQwenExtractor(
        base_url="http://ollama.test",
        model="qwen3:1.7b",
        fallback=RuleBasedExtractor(),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_ollama_structured_output(transcript: Transcript) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        content = {
            "entities": [
                {
                    "kind": "medication",
                    "mention": "lorazepam",
                    "segment_id": "seg_2",
                    "dose": "1 mg",
                }
            ]
        }
        return httpx.Response(200, json={"message": {"content": json.dumps(content)}})

    result = _ollama(handler).extract(transcript)
    assert result.extractor == "ollama:qwen3:1.7b" and not result.warnings
    assert result.entities[0].attributes == {"dose": "1 mg"}
    assert seen["think"] is False and seen["format"]["type"] == "object"


def test_ollama_invalid_output_falls_back_to_rules(transcript: Transcript) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json"}})

    result = _ollama(handler).extract(transcript)
    assert result.extractor == "rules" and "failed" in result.warnings[0]


def test_ollama_unknown_segment_falls_back(transcript: Transcript) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        content = {"entities": [{"kind": "service", "mention": "x", "segment_id": "nope"}]}
        return httpx.Response(200, json={"message": {"content": json.dumps(content)}})

    assert _ollama(handler).extract(transcript).extractor == "rules"


def test_build_extractor_modes() -> None:
    assert build_extractor("rules").name == "rules"
    assert build_extractor("ollama").name.startswith("ollama:")


@pytest.mark.parametrize(
    ("sentence", "term", "negated"),
    [
        ("Not to worry, the anesthesiologist will be with you.", "anesthesiologist", False),
        ("Don't worry, we'll give you lorazepam.", "lorazepam", False),
        ("No anesthesia, just lorazepam.", "lorazepam", False),
        ("We didn't do the MRI but we gave lorazepam.", "lorazepam", False),
        ("You won't need anesthesia for this scan.", "anesthesia", True),
        ("We will not need Dr. Lee's anesthesia team.", "anesthesia", True),
        ("No IV, no contrast.", "contrast", True),
    ],
)
def test_negation_stays_inside_its_clause(sentence: str, term: str, negated: bool) -> None:
    """Reassurances and earlier clauses never negate a later term."""
    from intelligence.extraction.rule_based import _negated

    assert _negated(sentence, sentence.index(term)) is negated


def test_abbreviations_do_not_split_sentences() -> None:
    from intelligence.extraction.rule_based import split_sentences

    assert split_sentences("Dr. Patel gave lorazepam. Then we left.") == [
        "Dr. Patel gave lorazepam.",
        "Then we left.",
    ]
