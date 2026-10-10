# Production-like demo on the FinchNode sandbox

The demo runs Amyna exactly as production would -- hosted Connect, signed FinchNode
webhooks, "new records arrived" pushes -- with a **synthetic** patient, because no real
records are linked.

## What comes from where

| In production | In this demo |
|---|---|
| Patient taps a Connect link, signs into their hospital, approves sharing | Same link and screens on FinchNode's **sandbox**: pick "Northstar Health System (Synthetic)" (no hospital login), or complete it with `simulate` |
| The hospital uploads the visit's records; FinchNode sends `records.updated` | `python scripts/finchnode_sandbox.py advance --latest` |
| Patient revokes sharing / the source fails | `... revoke --latest` / `... fail --latest` |
| The patient's **itemized bill** | Not in FinchNode, ever (its claims are summaries). Made up by you: `scripts/examples/baseline_adult_bill.json`, attached with `scripts/upload_bill.py` |
| The insurer's phone number | Not in FinchNode. `PAYER_PHONES` maps the payer to your test line |
| Visit recording | A voice memo in Relay (live mode) |

FinchNode does not accept uploaded records: sandbox patients come only from its named
scenarios. Use **`baseline-adult`** (Morgan Rivera, synthetic): a wellness visit on
2026-07-18 at Northstar, HbA1c / lipid / glucose / creatinine / potassium / hemoglobin
labs, metformin, coverage "Example Health Plan (Synthetic)". The example bill matches
that visit and plants two errors (a duplicate HbA1c line and a brain MRI that never
happened); Amyna matches the real lines against her FinchNode records and flags those two.

## One-time setup

1. **FinchNode** (free account at finchnode.com): create an application with a purpose,
   categories `demographics, encounters, labs, medications, claims, documents`, sync mode
   **continuous**, and a sandbox key (`ck_test_…`, shown once). Set the webhook URL to
   `https://<public Core host>/finchnode/webhook` and copy the `whsec_…` secret.
2. **`.env`** (never commit it):

   ```
   APP_ENV=dev
   FINCHNODE_MODE=sandbox
   FINCHNODE_API_KEY=ck_test_...
   FINCHNODE_WEBHOOK_SECRET=whsec_...
   FINCHNODE_SYNC_MODE=continuous
   PAYER_PHONES=Example Health Plan (Synthetic)=+1<your Telnyx test line>
   TELEPHONY_ALLOWED_NUMBERS=+1<your Telnyx test line>
   TELEPHONY_ALLOW_PATIENT_PHONES=true      # the handoff calls the tester's own phone
   NOTIFY_MODE=relay_service                # pushes ("records arrived", handoffs)
   RELAY_SERVICE_URL=http://localhost:3000
   ```

   Keep `APP_ENV=dev`: `staging` additionally demands Postgres, live voice/telephony and
   the agent network.
3. **Public HTTPS for Core** so FinchNode (and Telnyx) can reach it -- a host, or a tunnel
   such as `cloudflared tunnel --url http://localhost:8080`. Without one, linking still
   works: the patient taps "I'm done connecting" and Core reads the session back.
4. Check: `curl <core>/health` shows `"FINCHNODE_MODE": "sandbox"`.

## Running the demo

1. The tester texts Amyna on Relay. Amyna sends **Connect my health records**.
2. The tester opens the link (in the phone's browser), picks
   *Northstar Health System (Synthetic)* and approves -- or the operator runs
   `python scripts/finchnode_sandbox.py simulate --latest --scenario baseline-adult`.
   Amyna: *"Your health records are connected."*
3. The tester "goes to the appointment" (sharing a location at a hospital prompts a
   recording) and optionally records a voice memo.
4. Operator: `python scripts/finchnode_sandbox.py advance --latest` -- FinchNode imports
   the scenario's next records and sends `records.updated`; Amyna tells the tester new
   records arrived.
5. The bill arrives: `python scripts/upload_bill.py --handle <tester's Relay handle> --latest --bill scripts/examples/baseline_adult_bill.json`.
6. The tester taps **Review my bill**: Amyna checks it against the records and the memo,
   asks about the MRI, drafts the dispute, asks for the tester's callback number, and calls
   the insurer line after approval.

Sandbox patients are purged 7 days after creation (25 active max): connect within a week
of the demo. Everything stays synthetic -- Amyna is not HIPAA-ready.
