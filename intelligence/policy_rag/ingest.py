"""Load versioned policy documents (markdown + front matter) and chunk them.

Required metadata per document: doc_id, title, source, jurisdiction,
effective_date, retrieved_at, doc_type, topic, url. Optional: payer (default
ANY), issue_types, claim_types (comma-separated), version.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

SAMPLE_DOCS_DIR = Path(__file__).parent / "sample_docs"
REQUIRED_METADATA = (
    "doc_id",
    "title",
    "source",
    "jurisdiction",
    "effective_date",
    "retrieved_at",
    "doc_type",
    "topic",
    "url",
)


@dataclass(frozen=True)
class PolicyDocument:
    doc_id: str
    title: str
    source: str
    jurisdiction: str
    payer: str
    doc_type: str
    topic: str
    issue_types: tuple[str, ...]
    claim_types: tuple[str, ...]
    effective_date: date
    retrieved_at: date
    url: str
    version: str | None
    body: str


@dataclass(frozen=True)
class PolicyChunk:
    chunk_id: str
    document: PolicyDocument
    section: str | None
    text: str


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_document(text: str) -> PolicyDocument:
    """Parse ``---`` front matter (``key: value`` lines) followed by a markdown body."""
    if not text.startswith("---"):
        raise ValueError("policy document must start with front matter")
    _, header, body = text.split("---", 2)
    meta = {
        key.strip(): value.strip()
        for key, value in (
            line.split(":", 1) for line in header.strip().splitlines() if ":" in line
        )
    }
    missing = [key for key in REQUIRED_METADATA if not meta.get(key)]
    if missing:
        raise ValueError(f"policy document missing metadata: {missing}")
    return PolicyDocument(
        doc_id=meta["doc_id"],
        title=meta["title"],
        source=meta["source"],
        jurisdiction=meta["jurisdiction"].upper(),
        payer=meta.get("payer", "ANY"),
        doc_type=meta["doc_type"],
        topic=meta["topic"],
        issue_types=_split_list(meta.get("issue_types", "")),
        claim_types=_split_list(meta.get("claim_types", "")),
        effective_date=date.fromisoformat(meta["effective_date"]),
        retrieved_at=date.fromisoformat(meta["retrieved_at"]),
        url=meta["url"],
        version=meta.get("version"),
        body=body.strip(),
    )


def load_documents(directory: Path = SAMPLE_DOCS_DIR) -> list[PolicyDocument]:
    return [
        parse_document(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.md"))
    ]


def chunk_document(document: PolicyDocument) -> list[PolicyChunk]:
    """One chunk per ``## `` section (the whole body if there are no sections)."""
    chunks: list[PolicyChunk] = []
    section: str | None = None
    lines: list[str] = []

    def flush() -> None:
        text = " ".join(line.strip() for line in lines if line.strip())
        if text:
            chunks.append(PolicyChunk(f"{document.doc_id}#{len(chunks)}", document, section, text))

    for line in document.body.splitlines():
        if line.startswith("## "):
            flush()
            section, lines = line[3:].strip(), []
        else:
            lines.append(line)
    flush()
    return chunks
