"""Local Qwen3 (0.6B / 1.7B) extraction via Ollama with strict JSON-schema output.

EXTRACTION_MODE=ollama. The model only extracts mentions; codes come from the
deterministic resolver. On any failure (Ollama down, invalid JSON, unknown
segment ids) it falls back to the rules extractor and records a warning.

# VERIFY against current docs: Ollama ``/api/chat`` fields -- ``format`` accepting
# a JSON schema (structured outputs), ``think: false`` for Qwen3 reasoning
# models, ``options.temperature`` -- and the model tag you pulled.

Demo:  python -m intelligence.extraction.qwen_ollama --demo
"""

import argparse
import json
import logging
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

from claimshield_contracts.enums import EvidenceKind
from claimshield_contracts.models import Transcript

from .extractor import ExtractedEntity, ExtractionResult, Extractor

log = logging.getLogger("claimshield.extraction")

SYSTEM_PROMPT = """\
You extract billing-relevant facts from a medical visit transcript.
Return ONLY entities that the transcript says were performed or given during
this visit: procedures, medications, services (e.g. anesthesia), supplies.
- mention: the short phrase as spoken (e.g. "MRI of the brain with contrast", "lorazepam").
- segment_id: the id of the segment where it is mentioned (copy it exactly).
- negated: true if the transcript says it was NOT done or not needed.
- dose / route: only if stated (e.g. "1 mg", "IV").
Never invent entities. Never output billing codes. Output JSON matching the schema.
"""
MODEL_CONFIDENCE = 0.75


class _LLMEntity(BaseModel):
    kind: Literal["procedure", "medication", "service", "supply"]
    mention: str
    segment_id: str
    negated: bool = False
    dose: str | None = None
    route: str | None = None


class _LLMOutput(BaseModel):
    entities: list[_LLMEntity]


def render_transcript(transcript: Transcript) -> str:
    return "\n".join(f"[{s.segment_id}] {s.speaker.value}: {s.text}" for s in transcript.segments)


class OllamaQwenExtractor:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        fallback: Extractor | None = None,
        http: httpx.Client | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.name = f"ollama:{model}"
        self._url = base_url.rstrip("/") + "/api/chat"
        self._model = model
        self._fallback = fallback
        self._http = http or httpx.Client(timeout=timeout_s)

    def extract(self, transcript: Transcript) -> ExtractionResult:
        try:
            return ExtractionResult(extractor=self.name, entities=tuple(self._call(transcript)))
        except (httpx.HTTPError, ValidationError, KeyError, ValueError) as exc:
            if self._fallback is None:
                raise
            log.warning(
                "ollama extraction failed; using rules fallback",
                extra={"fields": {"error": type(exc).__name__}},
            )
            result = self._fallback.extract(transcript)
            warning = f"{self.name} failed ({type(exc).__name__}); used {result.extractor}"
            return ExtractionResult(result.extractor, result.entities, (*result.warnings, warning))

    def _call(self, transcript: Transcript) -> list[ExtractedEntity]:
        payload = {
            "model": self._model,
            "stream": False,
            "think": False,
            "format": _LLMOutput.model_json_schema(),
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": render_transcript(transcript)},
            ],
        }
        response = self._http.post(self._url, json=payload)
        response.raise_for_status()
        output = _LLMOutput.model_validate_json(response.json()["message"]["content"])
        segment_ids = {s.segment_id for s in transcript.segments}
        unknown = [e.segment_id for e in output.entities if e.segment_id not in segment_ids]
        if unknown:
            raise ValueError(f"model cited unknown segment ids: {unknown[:3]}")
        return [
            ExtractedEntity(
                kind=EvidenceKind(entity.kind),
                mention=entity.mention,
                segment_id=entity.segment_id,
                attributes={k: v for k, v in (("dose", entity.dose), ("route", entity.route)) if v},
                negated=entity.negated,
                confidence=MODEL_CONFIDENCE,
            )
            for entity in output.entities
        ]


def _demo() -> None:
    import os

    from claimshield_contracts.demo import demo_visit_complete

    from .rule_based import RuleBasedExtractor

    body = demo_visit_complete()
    transcript = Transcript(
        transcript_id="demo",
        visit_id="demo",
        source=body.source,
        segments=[
            {"segment_id": f"seg_{i}", **s.model_dump()} for i, s in enumerate(body.segments)
        ],
    )
    extractor = OllamaQwenExtractor(
        base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        model=os.environ.get("OLLAMA_MODEL", "qwen3:1.7b"),
        fallback=RuleBasedExtractor(),
    )
    result = extractor.extract(transcript)
    print(json.dumps({"extractor": result.extractor, "warnings": result.warnings}, indent=2))
    for entity in result.entities:
        print(
            f"- {entity.kind.value}: {entity.mention} {entity.attributes} negated={entity.negated}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qwen3/Ollama extraction demo")
    parser.add_argument("--demo", action="store_true", help="run on the demo transcript")
    if parser.parse_args().demo:
        _demo()
