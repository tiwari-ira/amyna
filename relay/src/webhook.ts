/**
 * Webhooks.
 *  - Inbound from the Relay platform: Standard Webhooks signature verification,
 *    idempotency (durable ledger), normalization into InboundEvent.
 *  - Outbound to Core: HMAC identical to core/app/auth.py `sign_payload`.
 */
import { createHmac, timingSafeEqual } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

/** Standard Webhooks headers; Relay and `relaymessenger listen` both sign this way. */
export const PLATFORM_ID_HEADER = "webhook-id";
export const PLATFORM_TIMESTAMP_HEADER = "webhook-timestamp";
export const PLATFORM_SIGNATURE_HEADER = "webhook-signature";
export const SIGNATURE_TOLERANCE_S = 300;

export class SignatureError extends Error {}

export function hmacHex(secret: string, payload: string): string {
  return createHmac("sha256", secret).update(payload, "utf8").digest("hex");
}

/** `v1=<hex HMAC-SHA256(secret, "{timestamp}.{body}")>` -- must match Core. */
export function signCoreEvent(secret: string, timestamp: string, body: string): string {
  return `v1=${hmacHex(secret, `${timestamp}.${body}`)}`;
}

function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

/** `v1,<base64 HMAC-SHA256(key, "{id}.{timestamp}.{body}")>` with key = base64 of a `whsec_` secret. */
export function signStandardWebhook(secret: string, id: string, timestamp: string, body: string): string {
  const key = Buffer.from(secret.replace(/^whsec_/, ""), "base64");
  return `v1,${createHmac("sha256", key).update(`${id}.${timestamp}.${body}`, "utf8").digest("base64")}`;
}

/** Throws SignatureError unless the Standard Webhooks signature is valid and fresh. */
export function verifyPlatformSignature(
  secret: string,
  rawBody: string,
  headers: Record<string, string | undefined>,
  nowS: number = Date.now() / 1000,
): void {
  const id = headers[PLATFORM_ID_HEADER];
  const timestamp = headers[PLATFORM_TIMESTAMP_HEADER];
  const signatures = headers[PLATFORM_SIGNATURE_HEADER];
  if (!secret) throw new SignatureError("RELAY_WEBHOOK_SECRET is not configured");
  if (!id || !timestamp || !signatures) throw new SignatureError("missing signature headers");
  if (!/^\d+$/.test(timestamp)) throw new SignatureError("invalid timestamp");
  if (Math.abs(nowS - Number(timestamp)) > SIGNATURE_TOLERANCE_S) {
    throw new SignatureError("timestamp outside tolerance (possible replay)");
  }
  const expected = signStandardWebhook(secret, id, timestamp, rawBody);
  // The header may carry several space-separated signatures during secret rotation.
  if (!signatures.split(" ").some((candidate) => safeEqual(expected, candidate))) {
    throw new SignatureError("signature mismatch");
  }
}

export type InboundKind = "message" | "button" | "recording" | "ignored";

export interface InboundEvent {
  id: string;
  kind: InboundKind;
  from: string;
  text?: string;
  buttonId?: string;
  recordingUrl?: string;
  /** An audio attachment (voice memo). `url` is a signed credential: never log it. */
  audio?: InboundAudio;
}

export interface InboundAudio {
  attachmentId: string;
  url: string;
  mimeType: string;
  durationMs?: number;
  sizeBytes?: number;
}

interface RelayEnvelope {
  event_type?: string;
  event_id?: string;
  data?: {
    direction?: string;
    sender_handle?: { handle?: string };
    parts?: {
      type?: string;
      value?: unknown;
      id?: string;
      url?: string;
      mime_type?: string;
      duration_ms?: number | null;
      size_bytes?: number;
    }[];
  };
}

/** Relay's `message.received` envelope. Other event types are acknowledged and ignored. */
function parseRelayEnvelope(event: RelayEnvelope): InboundEvent {
  if (!event.event_id) throw new Error("event is missing event_id");
  const data = event.data ?? {};
  const from = data.sender_handle?.handle;
  if (event.event_type !== "message.received" || data.direction !== "inbound" || !from) {
    return { id: event.event_id, kind: "ignored", from: from ?? "" };
  }
  // A button tap arrives as a text part whose value is the button's label.
  const parts = data.parts ?? [];
  const text = parts
    .filter((part) => part.type === "text" && typeof part.value === "string")
    .map((part) => part.value as string)
    .join("\n");
  const audio = parts.find((part) => part.type === "media" && part.mime_type?.startsWith("audio/"));
  if (audio?.id && audio.url) {
    return {
      id: event.event_id,
      kind: "message",
      from,
      text,
      audio: {
        attachmentId: audio.id,
        url: audio.url,
        mimeType: audio.mime_type ?? "audio/*",
        durationMs: audio.duration_ms ?? undefined,
        sizeBytes: audio.size_bytes,
      },
    };
  }
  return { id: event.event_id, kind: "message", from, text };
}

/** Normalize a Relay platform event (or the legacy shape the mock demo and tests post). */
export function parsePlatformEvent(payload: unknown): InboundEvent {
  if (payload && typeof payload === "object" && "event_type" in payload) {
    return parseRelayEnvelope(payload as RelayEnvelope);
  }
  const event = payload as {
    id?: string;
    type?: string;
    from?: string | { id?: string };
    text?: string;
    button?: { id?: string };
    recording?: { url?: string };
  };
  const from = typeof event.from === "string" ? event.from : event.from?.id;
  if (!event.id || !from) throw new Error("event is missing id or sender");
  if (event.type === "button") return { id: event.id, kind: "button", from, buttonId: event.button?.id };
  if (event.type === "recording.completed") {
    return { id: event.id, kind: "recording", from, recordingUrl: event.recording?.url };
  }
  return { id: event.id, kind: "message", from, text: event.text ?? "" };
}

/** Durable idempotency ledger: each platform event id is processed at most once. */
export class EventLedger {
  private readonly seenAt = new Map<string, number>();

  constructor(
    private readonly path?: string,
    private readonly ttlMs = 7 * 24 * 3600 * 1000,
  ) {
    if (path && existsSync(path)) {
      const stored = JSON.parse(readFileSync(path, "utf8")) as Record<string, number>;
      for (const [id, at] of Object.entries(stored)) this.seenAt.set(id, at);
    }
  }

  /** Returns false if the event was already recorded; records it otherwise. */
  claim(id: string, now = Date.now()): boolean {
    for (const [key, at] of this.seenAt) if (now - at > this.ttlMs) this.seenAt.delete(key);
    if (this.seenAt.has(id)) return false;
    this.seenAt.set(id, now);
    this.persist();
    return true;
  }

  /** Forget an id so the platform's retry is processed (used when handling failed). */
  release(id: string): void {
    this.seenAt.delete(id);
    this.persist();
  }

  private persist(): void {
    if (!this.path) return;
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(Object.fromEntries(this.seenAt)));
  }
}
