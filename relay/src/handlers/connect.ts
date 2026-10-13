/**
 * FinchNode hosted Connect: how a patient links their health records in production.
 *
 * Core opens a Connect session (FinchNode sandbox/production), the patient taps the
 * link, picks their hospital, signs in there and approves sharing on FinchNode's
 * pages. Core learns the result from FinchNode (consent.granted webhook) and pushes
 * a "finchnode_connected" notification; tapping "I'm done connecting" (or any
 * message) reads the session back, for setups without a public webhook URL.
 * In mock/demo modes Core answers connect_unavailable and patients type an id.
 */
import { CoreError } from "../coreClient";
import type { Conversation } from "../store";
import { BUTTONS, copy } from "../ux/messages";
import { answerRecordingChoice, offerRecording } from "./appointmentRecording";
import { uploadAuthorizationMemo } from "./authorizationMemo";
import { type HandlerContext, say } from "./context";
import { deliverPending } from "./humanHandoff";
import { askForLocation } from "./hospitalCheckin";

/** True once the patient's records are linked (typed id, or FinchNode Connect). */
export function isLinked(convo: Conversation): boolean {
  return Boolean(convo.finchnodePatientId || convo.recordsLinked);
}

/** Send a Connect link. False when Core links patients by typed id (mock/demo). */
export async function startConnect(ctx: HandlerContext, convo: Conversation): Promise<boolean> {
  let session;
  try {
    session = await ctx.core.startFinchNodeConnect({ case_id: convo.caseId ?? null });
  } catch (error) {
    if (error instanceof CoreError && error.code === "connect_unavailable") return false;
    throw error;
  }
  convo.caseId = session.case_id;
  convo.connectSessionId = session.session_id;
  convo.connectMode = true;
  convo.stage = "awaiting_finchnode_connect";
  ctx.store.save(); // a retry must reuse this case, not open another
  await uploadAuthorizationMemo(ctx, convo);
  await say(ctx, convo, copy.connectRecords(session.url));
  return true;
}

/** The patient says they're done (or writes anything): read the session back. */
export async function checkConnect(ctx: HandlerContext, convo: Conversation): Promise<void> {
  if (!convo.connectSessionId) {
    await startConnect(ctx, convo);
    return;
  }
  const session = await ctx.core.getFinchNodeConnect(convo.connectSessionId);
  if (session.connected) {
    await deliverPending(ctx, convo); // Core's "Your health records are connected." (once)
    await onFinchNodeConnected(ctx, convo);
    return;
  }
  if (session.status === "expired" || session.status === "canceled") {
    convo.connectSessionId = undefined; // links last 24 h and serve one patient: start over
    await startConnect(ctx, convo);
    return;
  }
  await say(ctx, convo, copy.connectStillWaiting(session.url));
}

/** Records are linked (push or read-back): continue with the visit recording offer. */
export async function onFinchNodeConnected(ctx: HandlerContext, convo: Conversation): Promise<void> {
  if (convo.recordsLinked) return; // push and read-back can both report it
  convo.recordsLinked = true;
  if (convo.wantRecording) {
    convo.wantRecording = false;
    await answerRecordingChoice(ctx, convo, BUTTONS.recordYes.id);
    return;
  }
  await offerRecording(ctx, convo);
  await askForLocation(ctx, convo);
}
