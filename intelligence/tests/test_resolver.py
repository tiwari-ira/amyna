"""Code resolver ranking: exact -> alias -> fuzzy."""

from claimshield_contracts.enums import CodeSystem, MatchMethod
from claimshield_contracts.models import CodeRef
from intelligence.codes.resolver import CodeResolver


def test_exact_code(resolver: CodeResolver) -> None:
    [candidate] = resolver.resolve_code("70553", CodeSystem.CPT)
    assert candidate.match_method == MatchMethod.EXACT and candidate.confidence == 1.0
    assert candidate.concept == "mri_brain_contrast"


def test_code_mention_in_text_ranks_first(resolver: CodeResolver) -> None:
    ranked = resolver.resolve_text("billed 01922 for the scan")
    assert ranked[0].code == "01922" and ranked[0].match_method == MatchMethod.EXACT


def test_alias_beats_fuzzy(resolver: CodeResolver) -> None:
    ranked = resolver.resolve_text("the MRI of your brain with contrast", k=5)
    assert ranked[0].code == "70553"
    assert ranked[0].match_method == MatchMethod.ALIAS
    confidences = [c.confidence for c in ranked]
    assert confidences == sorted(confidences, reverse=True)


def test_fuzzy_handles_typos_with_lower_confidence(resolver: CodeResolver) -> None:
    ranked = resolver.resolve_text("lorazapam injectoin", k=3)
    assert ranked and ranked[0].concept == "lorazepam"
    assert ranked[0].match_method == MatchMethod.FUZZY
    assert ranked[0].confidence <= 0.8


def test_category_filter(resolver: CodeResolver) -> None:
    ranked = resolver.resolve_text("lorazepam", categories=frozenset({"procedure"}))
    assert all(c.concept != "lorazepam" for c in ranked)


def test_unknown_text_returns_nothing(resolver: CodeResolver) -> None:
    assert resolver.resolve_text("quantum entanglement therapy") == []


def test_entry_for_source_code(resolver: CodeResolver) -> None:
    entry = resolver.entry_for(CodeRef(system=CodeSystem.RXNORM, code="6470"))
    assert entry is not None and entry.concept == "lorazepam" and entry.category == "drug"
