"""Narrow extraction: transcript -> typed entities -> EvidenceItems.

Extractors only find *what was said* (mentions + attributes + negation). Codes
come from the deterministic resolver afterwards, never from the model.
"""

from dataclasses import dataclass, field
from typing import Protocol

from claimshield_contracts.enums import EvidenceKind, EvidenceSource
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import EvidenceItem, Transcript

from ..codes.resolver import CodeResolver, normalize_text

# Resolver categories each entity kind may resolve into.
KIND_CATEGORIES: dict[EvidenceKind, frozenset[str]] = {
    EvidenceKind.PROCEDURE: frozenset({"procedure", "anesthesia"}),
    EvidenceKind.MEDICATION: frozenset({"drug"}),
    EvidenceKind.SERVICE: frozenset({"service", "anesthesia", "facility"}),
    EvidenceKind.SUPPLY: frozenset({"drug"}),
}
MIN_CONCEPT_CONFIDENCE = 0.6
QUOTE_LIMIT = 240


@dataclass(frozen=True)
class ExtractedEntity:
    kind: EvidenceKind
    mention: str
    segment_id: str
    attributes: dict[str, str] = field(default_factory=dict)
    negated: bool = False
    confidence: float = 0.8


@dataclass(frozen=True)
class ExtractionResult:
    extractor: str
    entities: tuple[ExtractedEntity, ...]
    warnings: tuple[str, ...] = ()


class Extractor(Protocol):
    name: str

    def extract(self, transcript: Transcript) -> ExtractionResult: ...


def build_extractor(
    mode: str, *, ollama_url: str = "http://localhost:11434", ollama_model: str = "qwen3:1.7b"
) -> Extractor:
    """Extractor for EXTRACTION_MODE (rules | ollama). Ollama falls back to rules."""
    from .rule_based import RuleBasedExtractor

    if mode == "ollama":
        from .qwen_ollama import OllamaQwenExtractor

        return OllamaQwenExtractor(
            base_url=ollama_url, model=ollama_model, fallback=RuleBasedExtractor()
        )
    return RuleBasedExtractor()


def to_evidence_items(
    result: ExtractionResult,
    transcript: Transcript,
    *,
    case_id: str,
    visit_id: str,
    resolver: CodeResolver,
) -> list[EvidenceItem]:
    """Resolve codes for each entity and merge repeated mentions of one concept."""
    segments = {s.segment_id: s for s in transcript.segments}
    merged: dict[tuple[str, bool], EvidenceItem] = {}
    for entity in result.entities:
        candidates = resolver.resolve_text(
            entity.mention, k=3, categories=KIND_CATEGORIES[entity.kind]
        )
        top = candidates[0] if candidates else None
        concept = top.concept if top and top.confidence >= MIN_CONCEPT_CONFIDENCE else None
        key = (concept or normalize_text(entity.mention), entity.negated)
        existing = merged.get(key)
        if existing is not None:
            merged[key] = existing.model_copy(
                update={
                    "segment_ids": [*existing.segment_ids, entity.segment_id],
                    "attributes": {**entity.attributes, **existing.attributes},
                    "confidence": max(existing.confidence, entity.confidence),
                }
            )
            continue
        segment = segments.get(entity.segment_id)
        merged[key] = EvidenceItem(
            evidence_id=new_id("evidence"),
            case_id=case_id,
            visit_id=visit_id,
            source=EvidenceSource.VISIT_TRANSCRIPT,
            kind=entity.kind,
            concept=concept,
            description=entity.mention,
            codes=[c for c in candidates if c.concept == concept] if concept else candidates,
            attributes=dict(entity.attributes),
            segment_ids=[entity.segment_id],
            quote=segment.text[:QUOTE_LIMIT] if segment else None,
            negated=entity.negated,
            confidence=entity.confidence,
            extractor=result.extractor,
        )
    return [_describe(item) for item in merged.values()]


def _describe(item: EvidenceItem) -> EvidenceItem:
    """Human-readable description including key attributes (dose, route, contrast)."""
    details = ", ".join(f"{k} {v}" for k, v in item.attributes.items() if k in ("dose", "route"))
    prefix = "NOT " if item.negated else ""
    text = f"{prefix}{item.description}" + (f" ({details})" if details else "")
    return item.model_copy(update={"description": text})
