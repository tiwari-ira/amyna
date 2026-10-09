# Code data (demo only)

- `sample_codes.csv` — a **tiny** hand-made table for the synthetic demo scenario.
  CPT descriptions are **paraphrased** (CPT descriptors are AMA-licensed; do not
  bulk-download or paste official CPT text). HCPCS Level II and ICD-10-CM short
  descriptions are public CMS/NCHS data. RxNorm ids are for demo; verify before use.
- `sample_hospital_mrf.csv` / `.json` — synthetic hospital price-transparency
  files in the CMS v2 layout, for `hospital_ingest.py`.

Columns of `sample_codes.csv`:
`system, code, display, concept, family, category, plain_label, aliases (|-separated), ingredient`.

- `concept` groups codes that mean the same service for matching (70552/70553 → `mri_brain_contrast`).
- `family` groups related concepts (used to detect code mismatches).
- `category` drives discrepancy rules: `procedure | anesthesia | service | facility | drug | diagnosis`.
- `plain_label` is patient-facing wording used in confirmation questions.

Extending: add rows, keep codes you already see in FinchNode source records
(the engine prefers source codes), and add a test in `intelligence/tests/`.
