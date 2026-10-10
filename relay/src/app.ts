/** Builds the handler context and the HTTP server (index.ts just listens).
 *
 * Both inbound routes acknowledge first and work afterwards:
 *  - POST /webhook/relay: Relay allows 10 s per delivery and recommends "store the event,
 *    answer 2xx, then process" (docs.relayapp.im/webhooks). The event is claimed in the
 *    durable ledger (duplicates are ignored), answered 200, then handled in the
 *    background in per-patient order. A failure is logged and the patient is asked to
 *    send it again, since the platform will not retry an acknowledged event.
 *  - POST /internal/notify: Core's push. Answered 202 at once (Core must never wait on a
 *    patient's queue -- it may be the very request holding it), delivered in order, and a
 *    failed send is reported back to Core as a signed delivery_status event.
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { join } from "node:path";
import { timingSafeEqual } from "node:crypto";

import type { RelayConfig } from "./config";
import { CoreClient } from "./coreClient";
import { newEventId } from "./handlers/approvalFlow";
import { type HandlerContext, say } from "./handlers/context";
import { handleIncoming } from "./handlers/incomingMessage";
import { onCoreNotify, type CoreNotifyPayload } from "./handlers/humanHandoff";
import { LiveTranscriber, MockTranscriber } from "./handlers/transcription";
import { findNearbyHospital } from "./places/hospitals";
import { LiveRelayAdapter } from "./relayAdapter/live";
import { MockRelayAdapter } from "./relayAdapter/mock";
import { ConversationStore } from "./store";
import { copy } from "./ux/messages";
import {
  EventLedger,
  type InboundEvent,
  SignatureError,
  parsePlatformEvent,
  verifyPlatformSignature,
} from "./webhook";

export function createContext(config: RelayConfig, overrides: Partial<HandlerContext> = {}): HandlerContext {
  const relay = config.mode === "live" ? new LiveRelayAdapter(config) : new MockRelayAdapter(true);
  return {
    config,
    core: new CoreClient(config.coreBaseUrl, config.serviceToken, config.coreWebhookSecret),
    relay,
    store: new ConversationStore(join(config.dataDir, "conversations.json")),
    patientTokens: new Map<string, string>(),
    transcriber: relay.simulatesRecording ? new MockTranscriber() : new LiveTranscriber(config.xaiApiKey, config.xaiSttUrl),
    findHospital: findNearbyHospital,
    log: (message) => console.log(`[relay] ${message}`),
    ...overrides,
  };
}

/** The HTTP server plus a way to wait for acknowledged work (tests, graceful shutdown). */
export interface AmynaServer extends Server {
  idle(): Promise<void>;
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function bearerMatches(header: string | undefined, token: string): boolean {
  const provided = Buffer.from(header?.replace(/^Bearer\s+/i, "") ?? "");
  const expected = Buffer.from(token);
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}

/** Handle an acknowledged event. Never throws: the platform already has its 200. */
async function processEvent(ctx: HandlerContext, event: InboundEvent, ledger: EventLedger): Promise<void> {
  try {
    await handleIncoming(ctx, event);
  } catch (error) {
    ledger.release(event.id); // a replayed copy may be handled again
    ctx.log(`could not handle ${event.kind} event: ${(error as Error).message}`);
    await say(ctx, ctx.store.get(event.from), copy.tryAgain).catch((sendError: Error) =>
      ctx.log(`could not tell the patient: ${sendError.message}`),
    );
  }
}

/** Deliver a pushed notification; tell Core when the platform send failed. */
async function deliverPush(ctx: HandlerContext, payload: CoreNotifyPayload): Promise<void> {
  const { notification } = payload;
  try {
    await onCoreNotify(ctx, payload);
  } catch (error) {
    ctx.log(`push delivery failed: ${(error as Error).message}`);
    await ctx.core
      .sendSignedEvent({
        event_id: newEventId(),
        event_type: "delivery_status",
        case_id: notification.case_id,
        notification_id: notification.notification_id,
        choice: "failed",
        occurred_at: new Date().toISOString(),
      })
      .catch((reportError: Error) => ctx.log(`could not report the failure: ${reportError.message}`));
  }
}

export function createApp(
  ctx: HandlerContext,
  ledger = new EventLedger(join(ctx.config.dataDir, "events.json")),
): AmynaServer {
  const pending = new Set<Promise<void>>();
  const track = (work: Promise<void>) => {
    pending.add(work);
    void work.finally(() => pending.delete(work));
  };

  const server = createServer(async (req, res) => {
    try {
      if (req.method === "GET" && req.url === "/health") {
        return send(res, 200, { status: "ok", mode: ctx.config.mode });
      }
      if (req.method === "POST" && req.url === "/webhook/relay") {
        const raw = await readBody(req);
        if (ctx.config.mode === "live") {
          verifyPlatformSignature(ctx.config.relayWebhookSecret, raw, req.headers as Record<string, string>);
        }
        let event;
        try {
          event = parsePlatformEvent(JSON.parse(raw));
        } catch (error) {
          // Malformed events would fail the same way on every retry.
          return send(res, 400, { error: `malformed event: ${(error as Error).message}` });
        }
        if (event.kind === "ignored") return send(res, 200, { status: "ignored" });
        if (event.kind === "message" && !event.buttonId && event.text) {
          const buttonId = ctx.relay.resolveButton?.(event.from, event.text);
          if (buttonId) event = { ...event, buttonId };
        }
        if (!ledger.claim(event.id)) return send(res, 200, { status: "duplicate" });
        send(res, 200, { status: "accepted" });
        track(processEvent(ctx, event, ledger));
        return;
      }
      if (req.method === "POST" && req.url === "/internal/notify") {
        if (!bearerMatches(req.headers.authorization, ctx.config.serviceToken)) {
          return send(res, 401, { error: "unauthorized" });
        }
        let payload: CoreNotifyPayload;
        try {
          payload = JSON.parse(await readBody(req)) as CoreNotifyPayload;
        } catch {
          return send(res, 400, { error: "malformed notification" });
        }
        if (!payload?.notification?.notification_id || !payload.notification.case_id) {
          return send(res, 400, { error: "malformed notification" });
        }
        if (!ctx.store.byCase(payload.notification.case_id)) {
          // Not one of ours (yet): it stays in Core's outbox for the next pull.
          ctx.log(`no Relay conversation for case ${payload.notification.case_id}; left in the outbox`);
          return send(res, 200, { delivered: false });
        }
        send(res, 202, { delivered: true, delivery_ref: "relay:queued" });
        track(deliverPush(ctx, payload));
        return;
      }
      return send(res, 404, { error: "not found" });
    } catch (error) {
      if (error instanceof SignatureError) return send(res, 401, { error: error.message });
      ctx.log(`error: ${(error as Error).message}`);
      return send(res, 500, { error: "internal error" });
    }
  }) as AmynaServer;

  server.idle = async () => {
    while (pending.size) await Promise.allSettled([...pending]);
  };
  return server;
}
