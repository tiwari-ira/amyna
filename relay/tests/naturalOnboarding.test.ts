/** Natural-language onboarding: Amyna understands plain words, never demands an ID first. */
import { describe, expect, it } from "vitest";

import { setup } from "./support";

describe("natural-language onboarding", () => {
  it("greets without demanding a patient ID and understands a plain complaint", async () => {
    const { relay, send } = setup();
    await send({ text: "hi" });
    const welcome = relay.outbox.at(-1)!.message.text;
    expect(welcome).toMatch(/Amyna/);
    expect(welcome).not.toMatch(/Please send your FinchNode patient ID/);

    await send({ text: "I got this bill and I don't think it's right" });
    const reply = relay.outbox.at(-1)!.message.text;
    expect(reply).toMatch(/photo of the bill|tell me what happened/); // Amyna's guidance
    expect(reply).toMatch(/fn_pat_/); // the optional ID path, explained in plain English
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
    await send({ text: "this bill looks wrong" });
    await send({ text: "my id is fn_pat_demo_001" });
    expect(relay.outbox.at(-1)!.message.text).toMatch(/permission/); // data consent card
    expect(core.notes.length).toBe(0); // nothing recorded without consent
  });
});
