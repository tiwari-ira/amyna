import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  EventLedger,
  PLATFORM_ID_HEADER,
  PLATFORM_SIGNATURE_HEADER,
  PLATFORM_TIMESTAMP_HEADER,
  SignatureError,
  parsePlatformEvent,
  signCoreEvent,
  signStandardWebhook,
  verifyPlatformSignature,
} from "../src/webhook";

describe("signCoreEvent", () => {
  it("matches Core's Python sign_payload byte for byte", () => {
    // Vector from: app.auth.sign_payload("dev-core-webhook-secret", "1700000000", b'{"event_id":"evt_123456"}')
    expect(signCoreEvent("dev-core-webhook-secret", "1700000000", '{"event_id":"evt_123456"}')).toBe(
      "v1=4f5269273c2f93ecc20e88cc8384c4004dd3cff26e9969b17754e8d14412872d",
    );
  });
});

describe("verifyPlatformSignature", () => {
  const secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw";
  const body = '{"event_id":"e1"}';
  const headersFor = (ts: string, sig = signStandardWebhook(secret, "msg_1", ts, body)) => ({
    [PLATFORM_ID_HEADER]: "msg_1",
    [PLATFORM_TIMESTAMP_HEADER]: ts,
    [PLATFORM_SIGNATURE_HEADER]: sig,
  });

  it("matches the Standard Webhooks reference vector", () => {
    expect(
      signStandardWebhook(secret, "msg_p5jXN8AQM9LWM0D4loKWxJek", "1614265330", '{"test": 2432232314}'),
    ).toBe("v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=");
  });

  it("accepts a fresh valid signature, including one of several", () => {
    expect(() => verifyPlatformSignature(secret, body, headersFor("1000"), 1100)).not.toThrow();
    const rotated = headersFor("1000", `v1,b2xk ${signStandardWebhook(secret, "msg_1", "1000", body)}`);
    expect(() => verifyPlatformSignature(secret, body, rotated, 1000)).not.toThrow();
  });

  it("rejects tampering, staleness and missing headers", () => {
    expect(() => verifyPlatformSignature(secret, `${body} `, headersFor("1000"), 1000)).toThrow(SignatureError);
    expect(() => verifyPlatformSignature(secret, body, headersFor("1000"), 5000)).toThrow(/tolerance/);
    expect(() => verifyPlatformSignature(secret, body, {}, 1000)).toThrow(/missing/);
    expect(() => verifyPlatformSignature("", body, headersFor("1000"), 1000)).toThrow(/not configured/);
  });
});

describe("parsePlatformEvent", () => {
  it("normalizes messages, buttons and recordings", () => {
    expect(parsePlatformEvent({ id: "1", type: "message", from: { id: "u" }, text: "hi" })).toEqual({
      id: "1",
      kind: "message",
      from: "u",
      text: "hi",
    });
    expect(parsePlatformEvent({ id: "2", type: "button", from: "u", button: { id: "YES" } }).buttonId).toBe("YES");
    expect(
      parsePlatformEvent({ id: "3", type: "recording.completed", from: "u", recording: { url: "https://x" } })
        .recordingUrl,
    ).toBe("https://x");
    expect(() => parsePlatformEvent({ type: "message" })).toThrow();
  });

  it("reads Relay message.received envelopes and ignores other events", () => {
    const envelope = (eventType: string, direction = "inbound") => ({
      event_type: eventType,
      event_id: "evt_1",
      data: {
        direction,
        sender_handle: { handle: "ira" },
        parts: [{ type: "text", value: "Hi" }],
      },
    });
    expect(parsePlatformEvent(envelope("message.received"))).toEqual({
      id: "evt_1",
      kind: "message",
      from: "ira",
      text: "Hi",
    });
    expect(parsePlatformEvent(envelope("message.sent", "outbound")).kind).toBe("ignored");
    expect(parsePlatformEvent(envelope("chat.typing_indicator.started")).kind).toBe("ignored");
    expect(() => parsePlatformEvent({ event_type: "message.received" })).toThrow(/event_id/);
  });
});

describe("EventLedger", () => {
  it("claims each id once, persists across restarts, and can release", () => {
    const path = join(mkdtempSync(join(tmpdir(), "ledger-")), "events.json");
    const ledger = new EventLedger(path);
    expect(ledger.claim("evt_1")).toBe(true);
    expect(ledger.claim("evt_1")).toBe(false);
    expect(new EventLedger(path).claim("evt_1")).toBe(false);
    ledger.release("evt_1");
    expect(new EventLedger(path).claim("evt_1")).toBe(true); // the release was persisted
  });

  it("expires old ids", () => {
    const ledger = new EventLedger(undefined, 1000);
    expect(ledger.claim("evt_2", 0)).toBe(true);
    expect(ledger.claim("evt_2", 5000)).toBe(true);
  });
});
