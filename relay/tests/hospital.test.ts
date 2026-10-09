/** A shared location at a hospital asks the patient to record the visit. */
import { afterEach, describe, expect, it } from "vitest";

import { stopLiveWatch } from "../src/handlers/hospitalCheckin";
import { metersBetween } from "../src/places/hospitals";
import { setup } from "./support";

const SFGH = { latitude: 37.7558, longitude: -122.4048 };

afterEach(() => {
  stopLiveWatch("patient");
});

describe("hospital check-in", () => {
  it("measures distance between two points", () => {
    const meters = metersBetween(SFGH, { latitude: 37.7561, longitude: -122.4049 });
    expect(meters).toBeGreaterThan(20);
    expect(meters).toBeLessThan(80);
  });

  it("asks them to record when a pin is at a hospital", async () => {
    const { relay, send } = setup({
      findHospital: async () => ({ name: "Zuckerberg San Francisco General", meters: 40 }),
    });
    await send({ text: "hi", chatId: "chat_1" });
    await send({ text: "u_f03417db39ffc1ae" });
    expect(relay.locationRequests).toEqual(["chat_1"]);
    expect(relay.outbox.some((m) => /Share your location/.test(m.message.text))).toBe(true);
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ place: { ...SFGH, name: "Zuckerberg San Francisco General" } });
    const prompt = relay.outbox.at(-1)!.message;
    expect(prompt.text).toMatch(/Zuckerberg San Francisco General/);
    expect(prompt.buttons?.map((b) => b.id)).toEqual(["RECORD_YES", "RECORD_SKIP"]);
  });

  it("stays quiet when the pin is not a hospital", async () => {
    const { relay, send } = setup({ findHospital: async () => undefined });
    await send({ text: "hi" });
    await send({ text: "fn_pat_demo_001" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ place: { latitude: 37.77, longitude: -122.42, name: "Ferry Building" } });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/doesn't look like a hospital/);
  });

  it("asks for the patient id before recording", async () => {
    const { relay, send } = setup();
    await send({ text: "I'm at the hospital" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/Send your FinchNode patient ID first/);

    const later = setup();
    await later.send({ text: "hi" });
    await later.send({ text: "u_f03417db39ffc1ae" });
    await later.send({ text: "yes" });
    await later.send({ kind: "button", buttonId: "RECORD_SKIP" });
    await later.send({ text: "I'm at the hospital" });
    expect(later.relay.outbox.at(-1)?.message.text).toMatch(/Looks like you're at a hospital/);
  });
});
