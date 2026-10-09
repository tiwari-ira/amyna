"""Deterministic keyword/regex extractor (EXTRACTION_MODE=rules, and the Ollama fallback).

Handles: imaging procedures with body part + contrast, medications with dose and
route, anesthesia/sedation services, negation ("no anesthesia today"), questions
(low confidence), and speaker weighting (clinician/staff statements count most).
"""

import re

from claimshield_contracts.enums import EvidenceKind, Speaker
from claimshield_contracts.models import Transcript, TranscriptSegment

from .extractor import ExtractedEntity, ExtractionResult

MEDICATIONS: dict[str, str] = {
    "lorazepam": "lorazepam",
    "ativan": "lorazepam",
    "midazolam": "midazolam",
    "versed": "midazolam",
    "propofol": "propofol",
    "fentanyl": "fentanyl",
    "ondansetron": "ondansetron",
    "gadolinium": "gadolinium contrast",
    "contrast dye": "gadolinium contrast",
}
SERVICES: dict[str, str] = {
    "general anesthesia": "anesthesia",
    "anesthesia": "anesthesia",
    "anesthesiologist": "anesthesia",
    "put you under": "anesthesia",
    "sedation": "sedation",
}
BODY_PARTS = ("brain", "head", "knee", "spine", "shoulder", "abdomen", "chest", "hip")
SPEAKER_CONFIDENCE = {
    Speaker.CLINICIAN: 0.9,
    Speaker.STAFF: 0.9,
    Speaker.UNKNOWN: 0.7,
    Speaker.PATIENT: 0.6,
}
QUESTION_CONFIDENCE = 0.4

_SENTENCE_SPLIT = re.compile(r"(?<!\bDr)(?<!\bMr)(?<!\bMs)(?<!\bMrs)(?<=[.!?])\s+")
_NEGATION = re.compile(
    r"\b(no|not|without|never|won't|will not|don't|do not|didn't|did not|no need for|"
    r"denies|declined|refused)\b(?:\W+\w+){0,5}\W*$",
    re.IGNORECASE,
)
_DOSE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|milligrams?|mcg|micrograms?|ml|milliliters?)\b", re.I)
_UNITS = {"milligram": "mg", "milligrams": "mg", "microgram": "mcg", "micrograms": "mcg"}
_UNITS.update({"milliliter": "ml", "milliliters": "ml"})
_ROUTES = (
    (re.compile(r"\b(through (your|the) iv|iv|intravenous(ly)?)\b", re.I), "IV"),
    (re.compile(r"\b(by mouth|oral(ly)?|pill|tablet)\b", re.I), "oral"),
    (re.compile(r"\b(injection|inject|shot)\b", re.I), "injection"),
)
_MRI = re.compile(r"\bmri\b", re.IGNORECASE)


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _negated(sentence: str, start: int) -> bool:
    return bool(_NEGATION.search(sentence[:start]))


def _dose(sentence: str) -> str | None:
    match = _DOSE.search(sentence)
    if not match:
        return None
    unit = match.group(2).lower()
    return f"{match.group(1)} {_UNITS.get(unit, unit)}"


def _route(sentence: str) -> str | None:
    return next((route for pattern, route in _ROUTES if pattern.search(sentence)), None)


def _contrast(sentence: str) -> str | None:
    lowered = sentence.lower()
    if re.search(r"with(out)? and with(out)? contrast", lowered):
        return "with and without contrast"
    if "with contrast" in lowered:
        return "with contrast"
    if "without contrast" in lowered:
        return "without contrast"
    return None


class RuleBasedExtractor:
    name = "rules"

    def extract(self, transcript: Transcript) -> ExtractionResult:
        entities: list[ExtractedEntity] = []
        for segment in transcript.segments:
            base = SPEAKER_CONFIDENCE.get(segment.speaker, 0.7)
            for sentence in split_sentences(segment.text.replace("\u2019", "'")):
                confidence = QUESTION_CONFIDENCE if sentence.rstrip().endswith("?") else base
                entities.extend(self._imaging(sentence, segment, confidence))
                entities.extend(
                    self._terms(sentence, segment, confidence, MEDICATIONS, EvidenceKind.MEDICATION)
                )
                entities.extend(
                    self._terms(sentence, segment, confidence, SERVICES, EvidenceKind.SERVICE)
                )
        return ExtractionResult(extractor=self.name, entities=tuple(entities))

    @staticmethod
    def _imaging(
        sentence: str, segment: TranscriptSegment, confidence: float
    ) -> list[ExtractedEntity]:
        match = _MRI.search(sentence)
        if not match:
            return []
        lowered = sentence.lower()
        body = next((part for part in BODY_PARTS if re.search(rf"\b{part}\b", lowered)), None)
        contrast = _contrast(sentence)
        mention = " ".join(p for p in ("MRI", body, contrast) if p)
        attributes = {"modality": "MRI"}
        attributes.update({k: v for k, v in (("body_part", body), ("contrast", contrast)) if v})
        return [
            ExtractedEntity(
                kind=EvidenceKind.PROCEDURE,
                mention=mention,
                segment_id=segment.segment_id,
                attributes=attributes,
                negated=_negated(sentence, match.start()),
                confidence=confidence,
            )
        ]

    @staticmethod
    def _terms(
        sentence: str,
        segment: TranscriptSegment,
        confidence: float,
        lexicon: dict[str, str],
        kind: EvidenceKind,
    ) -> list[ExtractedEntity]:
        found: list[ExtractedEntity] = []
        seen: set[str] = set()
        for term, canonical in lexicon.items():
            match = re.search(rf"\b{re.escape(term)}\b", sentence, re.IGNORECASE)
            if not match or canonical in seen:
                continue
            seen.add(canonical)
            attributes: dict[str, str] = {}
            if kind == EvidenceKind.MEDICATION:
                attributes = {
                    k: v for k, v in (("dose", _dose(sentence)), ("route", _route(sentence))) if v
                }
            found.append(
                ExtractedEntity(
                    kind=kind,
                    mention=canonical,
                    segment_id=segment.segment_id,
                    attributes=attributes,
                    negated=_negated(sentence, match.start()),
                    confidence=confidence,
                )
            )
        return found
