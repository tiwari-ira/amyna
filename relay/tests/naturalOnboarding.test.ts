/** Natural-language onboarding: Amyna understands plain words, never demands an ID first. */
import { describe, expect, it } from "vitest";

import { setup } from "./support";

describe("natural-language onboarding", () => {
  it("greets without demanding a patient ID and understands a plain complaint", async () => {
    const { relay, send } = setup();
    await send({ text: "hi" });
    expect(relay.outbox[0]!.message.text).toMatch(/Amyna/);
    const consent = relay.outbox.at(-1)!.message.text;
    expect(consent).toMatch(/permission/);
    expect(consent).not.toMatch(/Please send your FinchNode patient ID/);

    await send({ text: "I got this bill and I don't think it's right" });
    const reply = relay.outbox.at(-1)!.message.text;
    expect(reply).toMatch(/photo of the bill|tell me what happened/);
    expect(reply).toMatch(/permission/);
  });

  it("explains insurance jargon immediately when asked", async () => {
    const { relay, send } = setup();
    await send({ text: "hi" });
    await send({ text: "what is a claim number?" });
    const reply = relay.outbox.at(-1)!.message.text;
    expect(reply).toMatch(/ID your insurance company assigns/);
  });

  it("still starts the case the moment an ID arrives", async () => {
    const { core, relay, send } = setup();
    await send({ text: "hi" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ text: "this bill looks wrong" });
    await send({ text: "my id is fn_pat_demo_001" });
    expect(relay.outbox.at(-1)!.message.text).toMatch(/Share your location/);
    expect(core.notes.length).toBe(0);
  });
});
