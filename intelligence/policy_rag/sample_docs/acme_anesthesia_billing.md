---
doc_id: acme-anesthesia-2026
title: Acme Health Plan (synthetic) - Anesthesia Services Billing Policy
source: SYNTHETIC demo document (not a real payer policy)
jurisdiction: US
payer: Acme Health Plan
doc_type: payer_policy
topic: anesthesia
issue_types: unsupported_charge, code_mismatch
claim_types: professional, institutional
effective_date: 2026-01-01
retrieved_at: 2026-09-01
version: 3
url: https://example.org/synthetic/acme/anesthesia-billing
---
## Documentation requirements
Anesthesia services are reimbursable only when the medical record contains an anesthesia record that identifies the anesthesia provider, the type of anesthesia, and start and stop times. Claims billed with anesthesia codes but without an anesthesia record are not supported and are subject to removal on review.

## Minimal sedation and anxiolysis
An anxiolytic medication (for example, an oral or intravenous benzodiazepine) given by the ordering or imaging team to relax a patient before a scan is not anesthesia care. It must not be billed as an anesthesia service; the medication itself may be billed with the appropriate drug code.

## Member disputes
Members may dispute an anesthesia charge they did not receive. Acme will reprocess the claim when the provider cannot produce a matching anesthesia record.
