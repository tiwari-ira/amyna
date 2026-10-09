/** Everything a handler needs. Built once in index.ts (or by demo.ts / tests). */
import type { RelayConfig } from "../config";
import type { CoreClient } from "../coreClient";
import type { NearbyHospital } from "../places/hospitals";
import type { RelayAdapter, OutboundMessage } from "../relayAdapter/types";
import type { Conversation, ConversationStore } from "../store";
import type { Transcriber } from "./transcription";

/** Public surface of CoreClient (lets tests pass a stub). */
export type CoreApi = Pick<CoreClient, keyof CoreClient>;

export type HospitalFinder = (
  latitude: number,
  longitude: number,
) => Promise<NearbyHospital | undefined>;

export interface HandlerContext {
  config: RelayConfig;
  core: CoreApi;
  relay: RelayAdapter;
  store: ConversationStore;
  transcriber: Transcriber;
  findHospital: HospitalFinder;
  /** Patient session tokens by platform user id (memory only; never persisted). */
  patientTokens: Map<string, string>;
  log: (message: string) => void;
}

/**
 * Resolve the Core client scoped to this patient: cases created with it belong
 * to the authenticated identity. Amyna's session = the identity binding; a
 * typed patient ID is never a credential (task 19).
 */
export async function patientScope(ctx: HandlerContext, from: string): Promise<CoreApi> {
  let token = ctx.patientTokens.get(from);
  if (!token) {
    const session = await ctx.core.startAuthSession({ subject: `relay:${from}` });
    token = session.patient_token;
    ctx.patientTokens.set(from, token);
  }
  return ctx.core.withPatientToken(token);
}

export async function say(
  ctx: HandlerContext,
  convo: Conversation,
  message: OutboundMessage | string,
): Promise<void> {
  await ctx.relay.sendMessage(convo.userId, typeof message === "string" ? { text: message } : message);
}

export function requireCase(convo: Conversation): string {
  if (!convo.caseId) throw new Error(`conversation ${convo.userId} has no case yet`);
  return convo.caseId;
}

const queues = new Map<string, Promise<unknown>>();

/** Run handlers for one patient strictly one at a time (double taps, push vs webhook). */
export function serialize<T>(key: string, work: () => Promise<T>): Promise<T> {
  const previous = queues.get(key) ?? Promise.resolve();
  const next = previous.catch(() => undefined).then(work);
  const tail = next.catch(() => undefined);
  queues.set(key, tail);
  void tail.then(() => {
    if (queues.get(key) === tail) queues.delete(key);
  });
  return next;
}
