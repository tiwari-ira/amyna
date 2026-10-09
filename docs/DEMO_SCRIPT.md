# Demo Script

All data is synthetic. Patient **Jordan Rivera** (`fn_pat_demo_001`) had a brain MRI
with contrast on **2026-09-15** at *Bayview Imaging Center* (synthetic). Before the
scan, the nurse gave **lorazepam 1 mg IV**. Nobody mentions anesthesia.

Claim `CLM-2026-0915-001` (synthetic) from *Acme Health Plan* bills:

| # | Code | Description | Amount | Expected |
|---|---|---|---|---|
| 1 | CPT 70553 | MRI brain without and with contrast | $2,400.00 | matched (transcript + FinchNode procedure) |
| 2 | HCPCS J2060 | Lorazepam injection | $35.00 | matched (transcript + FinchNode medication) |
| 3 | CPT 01922 | Anesthesia for non-invasive imaging | $780.00 | **unsupported** → ask patient |
| 4 | HCPCS G0463 | Facility fee | $250.00 | matched (FinchNode encounter) |
| 5 | HCPCS G0463 | Facility fee | $250.00 | **duplicate** → auto-confirmed |
| 6 | HCPCS J3490 | Unclassified drug | $40.00 | **unknown medication** → ask patient |

Expected outcome: 3 findings ($1,070.00 flagged). The patient answers the clean
question **"Did you receive anesthesia during your visit on September 15, 2026?"
(YES / NO / NOT SURE)** with NO, and NOT SURE for the unclassified drug. The patient approves the dispute. A mock call runs, the rep
asks for the SSN, the call pauses for a human, the patient verifies directly, and the
rep agrees to remove $780 + $250 and to request itemization for $40 →
**RESOLVED**.

---

## Demo 0 — 60-second offline proof (no keys)

```bash
make setup
make smoke        # drives the whole flow through the real Core HTTP API
```

The smoke run prints each step (case → transcript → evidence → claim → analysis → answers →
dispute → approval → call → SSN handoff → resolution) and finishes with `RESOLVED`.

## Demo 1 — ASI:One (Fetch track)

Prereqs: Agentverse setup done (`fetch/agentverse/README_AGENTVERSE.md`), `make dev-local`
running with `make seed`, and `make agents` running with `FETCH_MODE=agentverse`.

Offline rehearsal of the exact same conversation (no Agentverse): `make chat`.

| You type in ASI:One | Amyna replies (abridged) |
|---|---|
| "Hi Amyna, I think this hospital bill is wrong." | Ack + plain-English reply ("I can look into it..."), guidance to share the record code or claim number |
| "My patient ID is fn_pat_demo_001 and the claim is CLM-2026-0915-001." | Ack + "To review your bill I need your permission to read your FinchNode health records for this case. Reply YES to consent." |
| "Yes" | "Thanks — checking your visit evidence and claim…" → "Visit evidence: MRI brain with contrast; lorazepam 1 mg IV." → summary of **3 potential issues** ($1,070.00) → "**Did you receive anesthesia during your visit on 2026-09-15?** Reply YES, NO, or NOT SURE." |
| "No" | Next question: "Your bill includes a $40.00 charge for an unspecified drug. Other than lorazepam and gadolinium contrast, did you receive any other medication during your visit on September 15, 2026?" |
| "Not sure" | "Items to dispute: … Prepare a dispute package? (YES/NO)" |
| "Yes" | Dispute package summary with policy citations → "Reply **APPROVE** to approve the dispute and authorize Amyna to call Acme Health Plan for you, or REJECT." |
| "APPROVE" | "Calling Acme Health Plan…" → "The representative asked for the member's **Social Security number**. Amyna never shares that, so I paused the call. Please verify directly with the representative, then reply DONE." |
| "Done" | "✅ Resolved. The insurer will remove $780.00 (anesthesia) and $250.00 (duplicate facility fee) and request itemization for $40.00. Reference MOCK-REF-48213." |

Talking points: only the orchestrator is public; all inter-agent traffic is typed
(`VisitEvidenceRequest`, `ClaimAnalysisRequest`, `DisputeRequest`); agents never touch
the DB — they call Core tools; the call itself is owned by Core.

**First validate discoverability:** `make agents-hello` (HELLO_WORLD=1) and confirm ASI:One
replies "Hello from Amyna".

## Demo 2 — Relay patient experience (Relay track)

Mock rehearsal: run `make dev-local` in one terminal and `make relay-demo` in another.
The demo script feeds patient events through the real Relay handlers (mock Relay
adapter) against Core and prints what the patient would see:

1. "Hi" → welcome + consent buttons (**I consent** / **No thanks**).
2. Recording consent → appointment recording → audio ingested → transcript posted to Core.
3. **Review my bill** → "Which bill? Send the claim number" → `CLM-2026-0915-001` → claim pulled from FinchNode → analysis.
4. Question card: "Did you receive anesthesia…?" **[YES] [NO] [NOT SURE]** (one at a time).
5. Approval card: total, items, letter preview → **[Review] [Approve] [Reject]**.
6. During the call: handoff card "The insurer is asking for identity verification. Amyna never shares your SSN…" **[Done] [Cancel call]**.
7. Final status + case summary with the reference number.

Questions, the approval card and the handoff card are Core notifications rendered by the Relay
service; button taps travel back to Core as signed events on `POST /relay/webhook`.

Live: set `RELAY_MODE=live` + keys, `NOTIFY_MODE=relay_service`, tunnel the service, and repeat from a phone.

## Demo 3 — Voice + SSN handoff (xAI track)

Mock (always works): `make smoke`, or in the API docs at `http://localhost:8080/docs`:
`POST /voice/start` → `GET /voice/{call_id}` shows the transcript up to the SSN request,
case `HUMAN_REQUIRED`, and an open `credential_request` intervention →
`POST /human-intervention/{id}/resolve {"choice":"DONE", ...}` → transcript continues → `RESOLVED`.

Live: `XAI_VOICE_MODE=live`, `TELEPHONY_MODE=live`, allowlisted test number only.
Have a teammate play the insurer rep and ask for the SSN to show the pause.

Talking points: the call needs approval **and** stored authorization (try `POST /voice/start`
before approval → 409); the SSN detector runs in code on every utterance, not only in the
prompt; the agent cannot state codes or facts that are not in the case.
