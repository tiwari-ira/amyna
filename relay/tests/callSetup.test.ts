/** Before the insurer call: the spoken authorization memo and the patient's callback number. */
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { copy } from "../src/ux/messages";
import { CASE, setup } from "./support";

const MEMO = {
  attachmentId: "att_1",
  url: "https://media.relayapp.im/signed/att_1?token=secret",
  mimeType: "audio/mp4",
  sizeBytes: 3,
};

const texts = (relay: ReturnType<typeof setup>["relay"]) => relay.outbox.map((m) => m.message.text);

function withMemos() {
  const env = setup({ voiceMemos: true, dataDir: mkdtempSync(join(tmpdir(), "amyna-memo-")) });
  env.relay.fetchRecording = async () => new Uint8Array([1, 2, 3]);
  return env;
}

describe("the authorization memo", () => {
  it("is kept when it arrives and sent to Core once the case exists", async () => {
    const { core, relay, ctx, send } = withMemos();
    await send({ text: "hi" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ audio: MEMO });
    expect(texts(relay).at(-2)).toMatch(/play your recording to your insurance company/);
    expect(core.recordings).toEqual([]); // no case yet
    const kept = ctx.store.get("patient").authorizationMemo!;
    expect(existsSync(kept.path)).toBe(true);

    await send({ text: "patient-demo-001" });
    expect(core.recordings).toEqual([{ caseId: CASE, mimeType: "audio/mp4", bytes: 3 }]);
    expect(ctx.store.get("patient").authorizationMemo?.uploaded).toBe(true);
    expect(existsSync(kept.path)).toBe(false); // PHI removed once Core has it
    // The signed platform URL is never persisted.
    expect(JSON.stringify(ctx.store.get("patient"))).not.toContain("token=secret");
  });

  it("says so plainly when the audio can't be downloaded", async () => {
    const { core, relay, send } = withMemos();
    relay.fetchRecording = async () => {
      throw new Error("expired");
    };
    await send({ text: "hi" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ audio: MEMO });
    expect(texts(relay).at(-2)).toMatch(/couldn't save the audio/);
    await send({ text: "patient-demo-001" });
    expect(core.recordings).toEqual([]);
  });
});

async function toCallQuestion(env: ReturnType<typeof setup>) {
  const { send, tap } = env;
  await send({ text: "hi" });
  await send({ kind: "button", buttonId: "CONSENT_YES" });
  await send({ text: "patient-demo-001" });
  await send({ kind: "button", buttonId: "REVIEW_BILL" });
  await send({ text: "CLM-2026-0915-001" });
  await tap(":NO");
  await tap(":APPROVE");
}

describe("the patient's callback number", () => {
  it("is asked after they say call, before anything is dialed", async () => {
    const env = setup();
    (env.ctx.config as { askCallbackPhone: boolean }).askCallbackPhone = true;
    await toCallQuestion(env);
    await env.tap("CALL_INSURER");
    expect(texts(env.relay).at(-1)).toMatch(/What number should I call\?/);
    expect(texts(env.relay).at(-1)).toMatch(/Never send your SSN here/);
    expect(env.core.events.some((event) => event.choice === "APPROVE")).toBe(false);
    expect(env.core.calls).toHaveLength(0);

    await env.send({ text: "555-555-0123" });
    expect(env.core.phone).toBe("555-555-0123");
    expect(env.core.events.at(-1)).toMatchObject({ dispute_id: "dsp_1", choice: "APPROVE" });
    expect(texts(env.relay)).toContainEqual(expect.stringMatching(/I'll call \+1••••••0123 and add you/));
  });

  it("still calls the insurer when they skip it", async () => {
    const env = setup();
    (env.ctx.config as { askCallbackPhone: boolean }).askCallbackPhone = true;
    await toCallQuestion(env);
    await env.tap("CALL_INSURER");
    await env.tap("SKIP_PHONE");
    expect(env.core.phone).toBeNull();
    expect(env.core.events.at(-1)).toMatchObject({ dispute_id: "dsp_1", choice: "APPROVE" });
  });

  it("is not asked again when a number is already on file", async () => {
    const env = setup();
    (env.ctx.config as { askCallbackPhone: boolean }).askCallbackPhone = true;
    env.core.phone = "555-555-0123";
    await toCallQuestion(env);
    await env.tap("CALL_INSURER");
    expect(env.core.events.at(-1)).toMatchObject({ dispute_id: "dsp_1", choice: "APPROVE" });
  });

  it("explains a stale Done tap or an ended call plainly", () => {
    expect(copy.coreProblem("already_resolved")).toBe("That's already taken care of. I'll keep you posted here.");
    expect(copy.coreProblem("call_not_active")).toMatch(/already ended/);
  });
});
