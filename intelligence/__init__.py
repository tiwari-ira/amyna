"""Amyna intelligence library (Member 4). Pure Python, no DB, no network
except the optional Ollama extractor.

- extraction/   transcript -> entities (rules | Qwen3 via Ollama)
- codes/        deterministic code resolver + hospital price-file ingest
- policy_rag/   versioned policy docs, BM25 index, filtered retrieval
- discrepancy/  claim lines vs records vs visit evidence -> typed findings
"""
