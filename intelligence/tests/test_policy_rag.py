"""Policy ingestion metadata, filtered retrieval, citation attachment."""

from datetime import date

import pytest

from intelligence.policy_rag.ingest import chunk_document, load_documents, parse_document
from intelligence.policy_rag.retrieve import PolicyFilter, default_retriever


def test_sample_docs_have_required_metadata() -> None:
    docs = load_documents()
    assert len(docs) == 5
    for doc in docs:
        assert doc.source and doc.url and doc.effective_date <= doc.retrieved_at
        assert chunk_document(doc)


def test_missing_metadata_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing metadata"):
        parse_document("---\ndoc_id: x\ntitle: y\n---\nbody")


def test_retrieval_respects_jurisdiction_and_payer() -> None:
    retriever = default_retriever()
    in_ca = retriever.retrieve("itemized bill dispute charge", PolicyFilter(state="CA"), k=5)
    assert any(c.jurisdiction == "CA" for c in in_ca)
    in_tx = retriever.retrieve("itemized bill dispute charge", PolicyFilter(state="TX"), k=5)
    assert all(c.jurisdiction == "US" for c in in_tx)
    other_payer = retriever.retrieve("anesthesia record", PolicyFilter(payer="Other Plan"), k=5)
    assert all(not c.doc_id.startswith("acme-") for c in other_payer)


def test_retrieval_filters_by_issue_and_effective_date() -> None:
    retriever = default_retriever()
    hits = retriever.retrieve(
        "duplicate facility fee", PolicyFilter(issue_type="duplicate_charge"), k=1
    )
    assert hits[0].doc_id == "synthetic-duplicate-lines-2026"
    early = retriever.retrieve(
        "anesthesia record", PolicyFilter(payer="Acme Health Plan", as_of=date(2025, 1, 1)), k=5
    )
    assert all(c.doc_id != "acme-anesthesia-2026" for c in early)
