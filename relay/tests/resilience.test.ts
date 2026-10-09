/** Failure handling: retry-safe steps, Core 4xx vs 5xx, exactly-once delivery, per-patient order. */
import type { AddressInfo } from "node:net";

import { afterEach, describe, expect, it } from "vitest";

import { createApp } from "../src/app";
import { CoreError } from "../src/coreClient";
import { handleIncoming } from "../src/handlers/incomingMessage";
import { onCoreNotify } from "../src/handlers/humanHandoff";
import { EventLedger } from "../src/webhook";
import { CASE, setup } from "./support";

async function toClaimPrompt(send: ReturnType<typeof setup>["send"]) {
  await send({ text: "hi" });
  await send({ text: "fn_pat_demo_001" });
  await send({ kind: "button", buttonId: "CONSENT_YES" });
  await send({ kind: "button", buttonId: "RECORD_SKIP" });
  await send({ kind: "button", buttonId: "REVIEW_BILL" });
}

const texts = (relay: ReturnType<typeof setup>["relay"]) => relay.outbox.map((m) => m.message.text);

describe("Core errors", () => {
  it("turns a 4xx into a patient message instead of a platform retry", async () => {
    const { core, relay, send } = setup();
    await toClaimPrompt(send);
    core.ingestClaim = async () => {
      throw new CoreError(409, "consent_required", "Health-data consent is required.");
    };
    await expect(send({ text: "CLM-2026-0915-001" })).resolves.toBeUndefined();
    expect(texts(relay).at(-1)).toMatch(/couldn't do that step \(consent_required\)/);
    expect(texts(relay).at(-1)).not.toMatch(/Health-data/); // Core's internal wording stays internal
  });

  it("propagates a 5xx and the platform retry neither re-ingests nor skips the step", async () => {
    const { core, relay, ctx, send } = setup();
    await toClaimPrompt(send);
    const analyze = core.analyzeClaim.bind(core);
    let failures = 1;
    core.analyzeClaim = async () => {
      if (failures-- > 0) throw new CoreError(502, "orchestrator_unreachable", "down");
      return analyze();
    };
    const event = { id: "evt_claim", kind: "message" as const, from: "patient", text: "CLM-2026-0915-001" };
    await expect(handleIncoming(ctx, event)).rejects.toThrow(/502/);
    expect(ctx.store.get("patient").stage).toBe("awaiting_claim_id");

    await handleIncoming(ctx, event); // the platform's retry of the same event
    expect(core.ingested).toBe(1);
    expect(texts(relay).at(-1)).toMatch(/anesthesia/);
  });
});

describe("notification delivery", () => {
  it("re-delivers a notification whose send failed, and never twice", async () => {
    const { core, relay, ctx, send } = setup();
    await toClaimPrompt(send);
    const sendMessage = relay.sendMessage.bind(relay);
    let failures = 1;
    relay.sendMessage = async (userId, message) => {
      if (/anesthesia/.test(message.text) && failures-- > 0) throw new Error("platform timeout");
      return sendMessage(userId, message);
    };
    const event = { id: "evt_claim", kind: "message" as const, from: "patient", text: "CLM-2026-0915-001" };
    await expect(handleIncoming(ctx, event)).rejects.toThrow(/platform timeout/);
    expect(ctx.store.get("patient").delivered).toEqual({});

    await handleIncoming(ctx, event); // retry: the queued question is pulled and shown
    expect(texts(relay).filter((t) => /anesthesia/.test(t))).toHaveLength(1);

    await onCoreNotify(ctx, { notification: core.notes[0]!, case: { case_id: CASE, channel: "relay", finchnode_patient_id: "x" } });
    expect(texts(relay).filter((t) => /anesthesia/.test(t))).toHaveLength(1);
  });

  it("does not double-deliver when a push and a pull race", async () => {
    const { core, relay, ctx, send } = setup();
    await toClaimPrompt(send);
    await send({ text: "CLM-2026-0915-001" });
    core.note("status_update", "Still working on it.");
    const pushed = core.notes.at(-1)!;
    await Promise.all([
      onCoreNotify(ctx, { notification: pushed, case: { case_id: CASE, channel: "relay", finchnode_patient_id: "x" } }),
      send({ text: "anything new?" }),
    ]);
    expect(texts(relay).filter((t) => t === "Still working on it.")).toHaveLength(1);
  });
});

describe("per-patient ordering", () => {
  it("handles one patient's events strictly one at a time", async () => {
    const { core, send, tap } = setup();
    await toClaimPrompt(send);
    await send({ text: "CLM-2026-0915-001" });
    const order: string[] = [];
    let open!: () => void;
    const gate = new Promise<void>((resolve) => (open = resolve));
    const forward = core.sendSignedEvent.bind(core);
    core.sendSignedEvent = async (event) => {
      order.push(`start:${event.event_id}`);
      await gate;
      order.push(`end:${event.event_id}`);
      return forward(event);
    };
    const first = tap(":NO");
    const second = tap(":NO"); // a double tap
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(order).toHaveLength(1);
    open();
    await Promise.all([first, second]);
    expect(order.map((o) => o.split(":")[0])).toEqual(["start", "end", "start", "end"]);
  });
});

describe("HTTP webhook", () => {
  const servers: { close: () => void }[] = [];
  afterEach(() => servers.splice(0).forEach((s) => s.close()));

  async function serve(ctx: ReturnType<typeof setup>["ctx"]) {
    const server = createApp(ctx, new EventLedger());
    servers.push(server);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address() as AddressInfo;
    return (body: string) =>
      fetch(`http://127.0.0.1:${port}/webhook/relay`, { method: "POST", body }).then(async (r) => ({
        status: r.status,
        json: (await r.json()) as { status?: string },
      }));
  }

  it("rejects malformed events with 400 (no retry storm)", async () => {
    const post = await serve(setup().ctx);
    expect((await post("{not json")).status).toBe(400);
    expect((await post(JSON.stringify({ type: "message" }))).status).toBe(400);
  });

  it("releases a failed event for the platform retry and dedupes after success", async () => {
    const { core, ctx } = setup();
    const post = await serve(ctx);
    const createCase = core.createCase.bind(core);
    let failures = 1;
    core.createCase = async () => {
      if (failures-- > 0) throw new CoreError(503, "unavailable", "db down");
      return createCase();
    };
    const hi = JSON.stringify({ id: "evt_hi", type: "message", from: "u1", text: "hi" });
    const id = JSON.stringify({ id: "evt_id", type: "message", from: "u1", text: "fn_pat_demo_001" });
    expect((await post(hi)).json.status).toBe("processed");
    expect((await post(id)).status).toBe(500);
    expect((await post(id)).json.status).toBe("processed");
    expect((await post(id)).json.status).toBe("duplicate");
  });
});
