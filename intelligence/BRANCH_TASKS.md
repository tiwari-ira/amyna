# branch-2-fetch — Member 4 (intelligence)

You own `intelligence/`: a pure, deterministic library (no DB; network only for the
optional Ollama extractor). It is used by the Fetch agent logic and, through it, by Core's
local_direct bridge. **No fine-tuning and no bulk "train on all codes/laws"**: the pipeline is
small-model extraction → deterministic code resolver → versioned policy RAG → engine.

## Already working

- [x] `extraction/rule_based.py`: imaging + body part + contrast, medications with dose/route, anesthesia/sedation, negation ("no anesthesia today"), questions (low confidence), speaker weighting
- [x] `extraction/qwen_ollama.py`: Qwen3 via Ollama with JSON-schema output; validates segment ids; falls back to rules with a warning
- [x] `codes/resolver.py`: exact → alias → RapidFuzz (`WRatio`) with ranked confidence; prefers source-record codes upstream
- [x] `codes/hospital_ingest.py`: CMS v2 MRF CSV ("tall") + JSON → per-hospital gross/cash/negotiated charges (synthetic sample)
- [x] `policy_rag/`: 5 synthetic, versioned docs with metadata (source, jurisdiction, payer, effective_date, retrieved_at, doc_type, topic, url) + BM25 + filters (state / payer / issue / claim type / effective date) + `cite_findings`
- [x] `discrepancy/engine.py`: duplicate, unsupported, unknown medication, code mismatch, price-above-published; patient question only when the patient can answer and confidence < threshold
- [x] Seed scenario → exactly 3 findings, the clean anesthesia question, $1,070 flagged (tests pin it)

## To implement / improve

1. **Evidence pipeline (step 5)**: run `EXTRACTION_MODE=ollama` with `qwen3:1.7b` (or 0.6b) on real-ish transcripts; tune the prompt and schema; keep rules as the fallback.
2. **Claim comparison (step 6)**: extend rules (quantity/units mismatch, modifier checks, bundling e.g. IV push inside the procedure) with a unit test per rule; keep the seed test green.
3. Code data: grow `codes/data/sample_codes.csv` with codes you actually see in FinchNode records. Paraphrase CPT text (AMA license); HCPCS/ICD-10-CM text is public.
4. Hospital files: point `hospital_ingest.build_directory` at real (public) MRFs for demo hospitals; verify the current CMS template.
5. Policy RAG: add real public documents only with full provenance metadata; keep `effective_date` filters; never present output as legal advice.

## Env vars

`EXTRACTION_MODE` (`rules|ollama`), `OLLAMA_URL`, `OLLAMA_MODEL`, `CONFIRMATION_THRESHOLD`.

## Commands

```bash
.venv/bin/python -m pytest intelligence/tests
ollama pull qwen3:1.7b && python -m intelligence.extraction.qwen_ollama --demo
make smoke        # the full flow must still resolve the seed scenario
```

## Do not change without a PR to main

`shared/**` (`EvidenceItem`, `DisputeCandidate`, `LineAssessment`, `PolicyCitation`, enums
such as `IssueType`), the logic entry points in `fetch/agents/logic/` (shared with Member 3),
and `shared/python/claimshield_contracts/fixtures/demo_scenario.json` (the seed contract).

## Definition of done

- [ ] Ollama extraction matches or beats rules on the demo transcript; rules fallback tested
- [ ] Every finding carries evidence refs + at least one relevant, filtered policy citation
- [ ] Engine stays deterministic; each new rule has a test; seed test unchanged
- [ ] Items for Ollama/CMS/data licensing ticked in `docs/VERIFY_LIST.md`

## Team build order (yours in **bold**)

1. Fetch hello-world (M3) · 2. Orchestrator ↔ Claim Agent (M3) · 3. **FinchNode demo → sandbox** ·
4. Relay thin UX (M2) · 5. **Evidence pipeline** · 6. **Claim comparison** · 7. Human approval (M2) ·
8. Dispute Agent (M3) · 9. Grok Voice + SIP (M1) · 10. Human takeover (M1 + M2).
Optional, low priority, **do not build now**: Grok Imagine avatar.
