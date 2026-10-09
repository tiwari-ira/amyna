"""Deterministic code resolver: exact code -> alias -> RapidFuzz fuzzy match.

Returns ranked ``CodeCandidate`` lists with a confidence and the match method.
Codes already present in source records are handled upstream (the discrepancy
engine looks them up with :meth:`CodeResolver.entry_for` and never re-derives).
"""

import csv
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from rapidfuzz import fuzz, process

from claimshield_contracts.enums import CodeSystem, MatchMethod
from claimshield_contracts.models import CodeCandidate, CodeRef

DEFAULT_TABLE = Path(__file__).parent / "data" / "sample_codes.csv"
_CODE_MENTION = re.compile(r"(?<![\w-])(\d{5}|[A-V]\d{4})(?![\w-])", re.IGNORECASE)
_METHOD_RANK = {
    MatchMethod.SOURCE_RECORD: 0,
    MatchMethod.EXACT: 1,
    MatchMethod.ALIAS: 2,
    MatchMethod.FUZZY: 3,
}


def normalize_text(text: str) -> str:
    """Lowercase, drop punctuation (keep hyphens inside words), collapse spaces."""
    text = re.sub(r"[^\w\s-]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class CodeEntry:
    system: CodeSystem
    code: str
    display: str
    concept: str
    family: str
    category: str
    plain_label: str
    aliases: tuple[str, ...]
    ingredient: str | None = None

    def candidate(self, confidence: float, method: MatchMethod) -> CodeCandidate:
        return CodeCandidate(
            system=self.system,
            code=self.code,
            display=self.display,
            concept=self.concept,
            confidence=round(confidence, 3),
            match_method=method,
        )


class CodeTable:
    """In-memory code table indexed by (system, code) and by concept."""

    def __init__(self, entries: Iterable[CodeEntry]) -> None:
        self._entries = list(entries)
        self._by_code = {(e.system, e.code.upper()): e for e in self._entries}
        self._by_concept: dict[str, list[CodeEntry]] = {}
        for entry in self._entries:
            self._by_concept.setdefault(entry.concept, []).append(entry)

    @classmethod
    def load(cls, path: Path = DEFAULT_TABLE) -> "CodeTable":
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return cls(
            CodeEntry(
                system=CodeSystem(row["system"]),
                code=row["code"],
                display=row["display"],
                concept=row["concept"],
                family=row["family"],
                category=row["category"],
                plain_label=row["plain_label"],
                aliases=tuple(a.strip().lower() for a in row["aliases"].split("|") if a.strip()),
                ingredient=row["ingredient"] or None,
            )
            for row in rows
        )

    def get(self, system: CodeSystem, code: str) -> CodeEntry | None:
        return self._by_code.get((system, code.upper()))

    def find_code(self, code: str) -> list[CodeEntry]:
        """Entries with this code in any system."""
        return [e for (_, c), e in self._by_code.items() if c == code.upper()]

    def by_concept(self, concept: str) -> list[CodeEntry]:
        return list(self._by_concept.get(concept, []))

    def __iter__(self) -> Iterator[CodeEntry]:
        return iter(self._entries)


class CodeResolver:
    """Ranks code candidates for a code or a free-text mention."""

    def __init__(
        self, table: CodeTable, *, fuzzy_threshold: float = 75.0, max_fuzzy_confidence: float = 0.8
    ) -> None:
        self.table = table
        self._fuzzy_threshold = fuzzy_threshold
        self._max_fuzzy = max_fuzzy_confidence
        self._choices: list[tuple[str, CodeEntry]] = [
            (normalize_text(text), entry)
            for entry in table
            for text in (*entry.aliases, entry.display)
        ]

    def entry_for(self, code: CodeRef) -> CodeEntry | None:
        """Table entry for a code exactly as it appears in a record or claim line."""
        return self.table.get(code.system, code.code)

    def resolve_code(self, code: str, system: CodeSystem | None = None) -> list[CodeCandidate]:
        entries = [self.table.get(system, code)] if system else self.table.find_code(code)
        return [e.candidate(1.0, MatchMethod.EXACT) for e in entries if e is not None]

    def resolve_text(
        self, text: str, *, k: int = 5, categories: frozenset[str] | None = None
    ) -> list[CodeCandidate]:
        """Exact code mentions, then aliases, then fuzzy matches; best first."""
        normalized = normalize_text(text)
        best: dict[tuple[CodeSystem, str], CodeCandidate] = {}

        def offer(entry: CodeEntry, confidence: float, method: MatchMethod) -> None:
            if categories and entry.category not in categories:
                return
            key = (entry.system, entry.code)
            current = best.get(key)
            if current is None or confidence > current.confidence:
                best[key] = entry.candidate(confidence, method)

        for match in _CODE_MENTION.finditer(text):
            for entry in self.table.find_code(match.group(1)):
                offer(entry, 0.98, MatchMethod.EXACT)
        for alias, entry in self._choices:
            if alias == normalized:
                offer(entry, 0.95, MatchMethod.ALIAS)
            elif re.search(rf"\b{re.escape(alias)}\b", normalized):
                offer(entry, 0.8 + min(0.1, len(alias) / 200), MatchMethod.ALIAS)
        for _, score, index in process.extract(
            normalized,
            [choice for choice, _ in self._choices],
            scorer=fuzz.WRatio,
            limit=k * 4,
        ):
            if score >= self._fuzzy_threshold:
                offer(self._choices[index][1], score / 100 * self._max_fuzzy, MatchMethod.FUZZY)

        ranked = sorted(
            best.values(), key=lambda c: (-c.confidence, _METHOD_RANK[c.match_method], c.code)
        )
        return ranked[:k]


@cache
def default_resolver() -> CodeResolver:
    """Resolver over the bundled sample table (cached per process)."""
    return CodeResolver(CodeTable.load())
