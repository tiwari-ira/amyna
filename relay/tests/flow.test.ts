/** Handlers end to end against a stub Core that mimics Core's notification flow. */
import { describe, expect, it } from "vitest";

import { onCoreNotify } from "../src/handlers/humanHandoff";
import { CASE, setup } from "./support";

describe("relay patient flow", () => {
  it("walks consent -> recording -> review -> question -> approval -> handoff -> report", async () => {
    const { core, relay, ctx, send, tap } = setup();
    await send({ text: "hi" });
    await send({ text: "my id is fn_pat_demo_001" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ kind: "button", buttonId: "RECORD_YES" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/lorazepam/);
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ text: "CLM-2026-0915-001" });
    const question = relay.outbox.at(-1)!.message;
    expect(question.text).toMatch(/anesthesia/);
    expect(question.buttons?.map((b) => b.label)).toEqual(["Yes", "No", "Not sure"]);

    await tap(":NO");
    expect(core.events[0]).toMatchObject({ intervention_id: "hin_1", choice: "NO", case_id: CASE });
    expect(relay.outbox.at(-1)?.message.buttons?.map((b) => b.label)).toEqual(["Review", "Approve", "Reject"]);

    await tap(":REVIEW");
    expect(relay.outbox.at(-1)?.message.text).toBe("Dear insurer...");
    await tap(":APPROVE");
    expect(core.calls).toHaveLength(1); // the service started the call after approval
    expect(relay.outbox.at(-1)?.message.text).toMatch(/SSN/);

    await tap(":DONE");
    expect(core.state).toBe("RESOLVED");
    expect(relay.outbox.some((m) => m.message.text.startsWith("Case summary (RESOLVED)"))).toBe(true);
    expect(ctx.store.get("patient").stage).toBe("done");
  });

  it("delivers each Core notification exactly once (push + pull)", async () => {
    const { core, relay, ctx, send } = setup();
    await send({ text: "hi" });
    await send({ text: "fn_pat_demo_001" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ text: "CLM-2026-0915-001" });
    const shown = relay.outbox.length;
    await onCoreNotify(ctx, { notification: core.notes[0]!, case: { case_id: CASE, channel: "relay", finchnode_patient_id: "x" } });
    expect(relay.outbox.length).toBe(shown);
  });

  it("accepts typed answers for the open card and ignores unknown cases on push", async () => {
    const { core, ctx, send } = setup();
    await send({ text: "hi" });
    await send({ text: "fn_pat_demo_001" });
    await send({ text: "yes" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ text: "review my bill" });
    await send({ text: "CLM-2026-0915-001" });
    await send({ text: "No" });
    expect(core.events[0]?.choice).toBe("NO");
    const result = await onCoreNotify(ctx, {
      notification: { ...core.notes[0]!, case_id: "case_other" },
      case: { case_id: "case_other", channel: "asi1", finchnode_patient_id: "x" },
    });
    expect(result.delivered).toBe(false);
  });
});
