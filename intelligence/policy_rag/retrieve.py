"""Filtered policy retrieval (state / payer / claim type / issue / effective date).

Citations carry their provenance (source, jurisdiction, effective date, url) so a
dispute can show *why* a charge is questionable. All sample docs are synthetic.
"""

from dataclasses import dataclass
from datetime import date
from functools import cache

from claimshield_contracts.models import DisputeCandidate, PolicyCitation

from .index import BM25Index
from .ingest import PolicyChunk, chunk_document, load_documents

SNIPPET_LIMIT = 280


@dataclass(frozen=True)
class PolicyFilter:
    state: str | None = None
    payer: str | None = None
    issue_type: str | None = None
    claim_type: str | None = None
    as_of: date | None = None

    def matches(self, chunk: PolicyChunk) -> bool:
        doc = chunk.document
        if doc.jurisdiction not in ("US", (self.state or "").upper()):
            return False
        if doc.payer != "ANY" and (self.payer or "").lower() != doc.payer.lower():
            return False
        if self.issue_type and doc.issue_types and self.issue_type not in doc.issue_types:
            return False
        if self.claim_type and doc.claim_types and self.claim_type not in doc.claim_types:
            return False
        return not (self.as_of and doc.effective_date > self.as_of)


class PolicyRetriever:
    def __init__(self, index: BM25Index) -> None:
        self._index = index

    def retrieve(
        self, query: str, flt: PolicyFilter | None = None, *, k: int = 3
    ) -> list[PolicyCitation]:
        predicate = flt.matches if flt else None
        hits = self._index.search(query, k=k * 3, predicate=predicate)
        citations: list[PolicyCitation] = []
        seen_docs: set[str] = set()
        for chunk, score in hits:
            if chunk.document.doc_id in seen_docs:
                continue
            seen_docs.add(chunk.document.doc_id)
            citations.append(_citation(chunk, score))
            if len(citations) == k:
                break
        return citations


def _citation(chunk: PolicyChunk, score: float) -> PolicyCitation:
    doc = chunk.document
    snippet = chunk.text if len(chunk.text) <= SNIPPET_LIMIT else chunk.text[:SNIPPET_LIMIT] + "…"
    return PolicyCitation(
        doc_id=doc.doc_id,
        title=doc.title,
        section=chunk.section,
        source=doc.source,
        jurisdiction=doc.jurisdiction,
        doc_type=doc.doc_type,
        effective_date=doc.effective_date,
        url=doc.url,
        snippet=snippet,
        score=round(score, 3),
    )


@cache
def default_retriever() -> PolicyRetriever:
    """Retriever over the bundled synthetic sample docs (cached per process)."""
    chunks = [chunk for doc in load_documents() for chunk in chunk_document(doc)]
    return PolicyRetriever(BM25Index(chunks))


def cite_findings(
    findings: list[DisputeCandidate],
    retriever: PolicyRetriever,
    *,
    state: str | None,
    payer: str | None,
    as_of: date | None = None,
    k: int = 2,
) -> list[DisputeCandidate]:
    """Attach the top policy citations to each finding (filtered by its issue type)."""
    cited = []
    for finding in findings:
        flt = PolicyFilter(
            state=state, payer=payer, issue_type=finding.issue_type.value, as_of=as_of
        )
        query = (
            f"{finding.issue_type.value.replace('_', ' ')} {finding.title} {finding.explanation}"
        )
        cited.append(
            finding.model_copy(update={"policy_citations": retriever.retrieve(query, flt, k=k)})
        )
    return cited
