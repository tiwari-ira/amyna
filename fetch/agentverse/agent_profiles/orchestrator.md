# Amyna

Amyna helps patients review healthcare bills by comparing claimed procedures against patient-authorized health records, appointment evidence, and billing information. It identifies potential discrepancies, asks the patient for confirmation when evidence is uncertain, and prepares evidence-backed dispute packages.

## What to send

Just tell Amyna what's going on in your own words:

> This hospital bill looks wrong.

If you have them handy, your record code (fn_pat_...) and the claim number from the
bill (CLM-...) speed things up, for example:

> Please review my bill. My patient ID is fn_pat_demo_001 and the claim is CLM-2026-0915-001.

Don't know either code? That's fine -- describe the bill and Amyna will guide you.

## How it works

1. You consent to Amyna reading your FinchNode health records for this bill.
2. Amyna compares each billed line with your visit recording and records.
3. It lists potential issues and asks you to confirm anything uncertain (YES / NO / NOT SURE).
4. It prepares a dispute package with the evidence and policy references.
5. Only if you reply **APPROVE**, it can call your insurer for you. It never shares your SSN, PINs or passwords — if those are requested, the call pauses and you verify directly.

## Notes

- Demo data is synthetic. This is a hackathon prototype, not a HIPAA-ready service.
- Commands: STATUS, RESTART, HELP.
