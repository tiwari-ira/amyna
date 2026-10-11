/** Production-style flows: FinchNode Connect, picking a FinchNode bill, summary-only bills,
 * the patient's callback number, attachments, and Core/platform edge cases. */
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config";
import { CoreClient, CoreError } from "../src/coreClient";
import { onCoreNotify } from "../src/handlers/humanHandoff";
import { parsePlatformEvent } from "../src/webhook";
import { CASE, setup } from "./support";

const texts = (relay: ReturnType<typeof setup>["relay"]) => relay.outbox.map((m) => m.message.text);
const CASE_REF = { case_id: CASE, channel: "relay", finchnode_patient_id: "u_0123456789abcdef" };

describe("FinchNode Connect", () => {
  it("asks for consent before a patient id and does not open a new Connect link", async () => {
    const { core, relay, ctx, send } = setup();
    core.connectEnabled = true;
    await send({ text: "hi" });
    const card = relay.outbox.at(-1)!.message;
    expect(card.text).toMatch(/permission/);
    expect(card.buttons?.map((b) => b.id)).toEqual(["CONSENT_YES", "CONSENT_NO"]);
    expect(ctx.store.get("patient").stage).toBe("awaiting_data_consent");
    expect(core.connectCalls).toBe(0);

    await send({ kind: "button", buttonId: "CONSENT_YES" });
    expect(ctx.store.get("patient").stage).toBe("awaiting_account_memo");
    await send({ text: "patient-demo-001" });
    expect(ctx.store.get("patient")).toMatchObject({
      stage: "ready_for_bill",
      finchnodePatientId: "patient-demo-001",
      healthConsentGiven: true,
    });
  });
});

async function toBillReview(send: ReturnType<typeof setup>["send"]) {
  await send({ text: "hi" });
  await send({ kind: "button", buttonId: "CONSENT_YES" });
  await send({ text: "patient-demo-001" }); // FinchNode demo ids are accepted
}

describe("picking a FinchNode bill", () => {
  const bill = {
    external_claim_id: "claim-demo-001",
    provider_name: "Northstar Health System (Synthetic)",
    payer_name: "Example Health Plan (Synthetic)",
    date_of_service: "2026-07-18",
    total_billed_cents: 18000,
    itemized: false,
  };

  it("offers the patient's bills as buttons", async () => {
    const { core, relay, send } = setup();
    core.bills = [bill];
    await toBillReview(send);
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    const card = relay.outbox.at(-1)!.message;
    expect(card.buttons).toEqual([{ id: "bill:claim-demo-001", label: "Jul 18 · $180.00 · Northstar Health System" }]);
  });

  it("explains a summary-only bill instead of analyzing an empty one", async () => {
    const { core, relay, ctx, send } = setup();
    core.bills = [bill];
    core.ingestError = new CoreError(422, "claim_not_itemized", "summary only", {
      provider_name: bill.provider_name,
      date_of_service: bill.date_of_service,
      total_billed_cents: bill.total_billed_cents,
    });
    await toBillReview(send);
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ kind: "button", buttonId: "bill:claim-demo-001" });
    expect(texts(relay).at(-1)).toMatch(/only as a total: it doesn't list the individual charges/);
    expect(texts(relay).at(-1)).toMatch(/\$180\.00/);
    expect(ctx.store.get("patient").stage).toBe("ready_for_bill");
    expect(core.ingested).toBe(0);
  });

  it("reviews an itemized bill that was added to the case", async () => {
    const { core, ctx, send } = setup();
    await toBillReview(send);
    core.claim = { claim_id: "clm_uploaded" };
    core.state = "CLAIM_RECEIVED";
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    expect(ctx.store.get("patient")).toMatchObject({ claimId: "clm_uploaded", stage: "in_review" });
  });
});

describe("consent before the insurer call", () => {
  it("asks whether to call the insurer, then approves only on yes", async () => {
    const { core, relay, send, tap } = setup();
    await toBillReview(send);
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ text: "CLM-2026-0915-001" });
    await tap(":NO");
    await tap(":APPROVE");
    expect(texts(relay).at(-1)).toMatch(/call your insurance company/);
    expect(texts(relay).at(-1)).toMatch(/phone number already on file/);
    expect(core.state).toBe("AWAITING_APPROVAL");
    await tap("CALL_INSURER");
    expect(core.events.at(-1)).toMatchObject({ dispute_id: "dsp_1", choice: "APPROVE" });
  });

  it("does not approve or dial when the patient says not now", async () => {
    const { core, relay, ctx, send, tap } = setup();
    await toBillReview(send);
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ text: "CLM-2026-0915-001" });
    await tap(":NO");
    await tap(":APPROVE");
    await tap("CALL_LATER");
    expect(texts(relay).at(-1)).toMatch(/won't call your insurance company/);
    expect(core.events.some((event) => event.choice === "APPROVE")).toBe(false);
    expect(core.calls).toHaveLength(0);
    expect(ctx.store.get("patient").stage).toBe("done");
  });
});

describe("edges", () => {
  it("answers a bill photo honestly", async () => {
    const { relay, send } = setup();
    await send({ text: "hi" });
    await send({ text: "", attachment: { mimeType: "image/jpeg" } });
    expect(texts(relay).at(-1)).toMatch(/can't read bill photos or PDFs yet/);
  });

  it("finishes the conversation when Core pushes the final report", async () => {
    const { core, relay, ctx, send } = setup();
    await toBillReview(send);
    core.state = "RESOLVED";
    core.note("final_report", "Resolved. Reference MOCK-REF-1.");
    await onCoreNotify(ctx, { notification: core.notes.at(-1)!, case: CASE_REF });
    expect(ctx.store.get("patient")).toMatchObject({ stage: "done", reportSent: true });
    expect(texts(relay).at(-1)).toMatch(/^Case summary \(RESOLVED\)/); // once, like the pull path
  });

  it("reads Relay's current sender field and ignores other agents", () => {
    const envelope = (from: Record<string, unknown>) => ({
      event_type: "message.received",
      event_id: "evt_1",
      data: { direction: "inbound", from_handle: from, parts: [{ type: "text", value: "hi" }] },
    });
    expect(parsePlatformEvent(envelope({ handle: "ira", kind: "user" }))).toMatchObject({ kind: "message", from: "ira" });
    expect(parsePlatformEvent(envelope({ handle: "otherbot", kind: "agent" })).kind).toBe("ignored");
  });

  it("turns a proxy's HTML error page into a CoreError with the real status", async () => {
    const html = async () => new Response("<html>Bad gateway</html>", { status: 502 });
    const client = new CoreClient("http://core.test/", "t", "s", html as typeof fetch);
    await expect(client.getCase("case_1")).rejects.toMatchObject({ status: 502, code: "error" });
  });

  it("strips a trailing slash from CORE_BASE_URL", () => {
    expect(loadConfig({ CORE_BASE_URL: "http://core:8080/" }).coreBaseUrl).toBe("http://core:8080");
  });
});
