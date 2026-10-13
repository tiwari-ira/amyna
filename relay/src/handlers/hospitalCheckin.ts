/** Ping the patient when a shared location looks like a hospital or clinic. */
import { BUTTONS, copy } from "../ux/messages";
import type { Conversation } from "../store";
import type { InboundEvent } from "../webhook";
import { answerRecordingChoice } from "./appointmentRecording";
import { checkConnect, isLinked } from "./connect";
import { askDataConsent } from "./consent";
import { type HandlerContext, say, serialize } from "./context";

const HOSPITAL_PROMPT_COOLDOWN_MS = 2 * 60 * 60 * 1000;
const LIVE_POLL_MS = 2 * 60 * 1000;
const AT_HOSPITAL =
  /\b(?:i['’]?m\s+)?at\s+(?:(?:a|the)\s+)?(hospital|clinic|er|urgent care)\b/i;

const liveWatches = new Map<string, ReturnType<typeof setInterval>>();

const BUSY = new Set([
  "recording",
  "processing_visit",
  "in_review",
  "awaiting_claim_id",
  "awaiting_data_consent",
  "awaiting_finchnode_connect",
  "awaiting_callback_phone",
  "awaiting_insurer_call",
]);

export function isAtHospitalText(text: string | undefined): boolean {
  return Boolean(text?.trim() && AT_HOSPITAL.test(text.trim()));
}

const REPROMPT_LOCATION = new Set([
  "new",
  "awaiting_patient_id",
  "awaiting_account_memo",
  "awaiting_data_consent",
  "awaiting_recording_choice",
  "ready_for_bill",
]);

export async function askForLocation(ctx: HandlerContext, convo: Conversation): Promise<void> {
  if (convo.locationAsked) return;
  convo.locationAsked = true;
  await say(ctx, convo, copy.locationAsk);
  if (!convo.chatId) return;
  try {
    const result = await ctx.relay.requestLocation(convo.chatId);
    if (result === "already_sharing") {
      convo.locationSharing = true;
      watchLiveLocation(ctx, convo.userId);
      await checkCoordinates(ctx, convo, await ctx.relay.readLocation(convo.chatId), false);
    }
  } catch (error) {
    ctx.log(`location request failed: ${(error as Error).message}`);
  }
}

export function rememberChat(convo: Conversation, chatId: string | undefined): void {
  if (chatId) convo.chatId = chatId;
}

export async function handleLocationEvent(
  ctx: HandlerContext,
  convo: Conversation,
  event: InboundEvent,
): Promise<boolean> {
  rememberChat(convo, event.chatId);
  if (event.locationShare === "ended") {
    convo.locationSharing = false;
    stopLiveWatch(convo.userId);
    // Relay will not show the share button again until the old share is stopped.
    if (!convo.visitId && REPROMPT_LOCATION.has(convo.stage)) {
      convo.locationAsked = false;
      await askForLocation(ctx, convo);
    }
    return true;
  }
  if (event.locationShare === "live") {
    convo.locationSharing = true;
    watchLiveLocation(ctx, convo.userId);
    if (convo.chatId) {
      await notePlace(ctx, convo, await ctx.relay.readLocation(convo.chatId), false);
    }
    return true;
  }
  if (event.place) {
    await notePlace(ctx, convo, event.place, true);
    return true;
  }
  if (isAtHospitalText(event.text)) {
    if (BUSY.has(convo.stage)) return false;
    await promptHospital(ctx, convo, "a hospital");
    return true;
  }
  if (event.text?.trim().toLowerCase() === "location") {
    convo.locationAsked = false;
    await askForLocation(ctx, convo);
    return true;
  }
  return false;
}

export function watchLiveLocation(ctx: HandlerContext, userId: string): void {
  if (liveWatches.has(userId)) return;
  const timer = setInterval(() => {
    void serialize(userId, async () => {
      const convo = ctx.store.get(userId);
      if (!convo.locationSharing || !convo.chatId) {
        stopLiveWatch(userId);
        return;
      }
      await checkCoordinates(ctx, convo, await ctx.relay.readLocation(convo.chatId), false);
      ctx.store.save();
    }).catch((error: Error) => ctx.log(`live location check failed: ${error.message}`));
  }, LIVE_POLL_MS);
  liveWatches.set(userId, timer);
}

export function stopLiveWatch(userId: string): void {
  const timer = liveWatches.get(userId);
  if (timer) clearInterval(timer);
  liveWatches.delete(userId);
}

async function notePlace(
  ctx: HandlerContext,
  convo: Conversation,
  here: { latitude: number; longitude: number } | undefined,
  tellIfNot: boolean,
): Promise<void> {
  if (!here) return;
  if (BUSY.has(convo.stage) || convo.stage === "awaiting_recording_choice" || convo.visitId) {
    convo.pendingPlace = { latitude: here.latitude, longitude: here.longitude };
    return;
  }
  await checkCoordinates(ctx, convo, here, tellIfNot);
}

/** If a pin arrived during consent, say so once the patient is free to record. */
export async function flushPendingPlace(ctx: HandlerContext, convo: Conversation): Promise<void> {
  const place = convo.pendingPlace;
  if (!place) return;
  if (BUSY.has(convo.stage) || convo.stage === "awaiting_recording_choice" || convo.visitId) return;
  convo.pendingPlace = undefined;
  await checkCoordinates(ctx, convo, place, false);
}

async function checkCoordinates(
  ctx: HandlerContext,
  convo: Conversation,
  here: { latitude: number; longitude: number } | undefined,
  tellIfNot: boolean,
): Promise<void> {
  if (!here) return;
  let hospital;
  try {
    hospital = await ctx.findHospital(here.latitude, here.longitude);
  } catch (error) {
    ctx.log(`hospital lookup failed: ${(error as Error).message}`);
    return;
  }
  if (!hospital) {
    if (tellIfNot) await say(ctx, convo, copy.notNearHospital);
    return;
  }
  await promptHospital(ctx, convo, hospital.name);
}

async function promptHospital(ctx: HandlerContext, convo: Conversation, name: string): Promise<void> {
  if (BUSY.has(convo.stage) || convo.stage === "awaiting_recording_choice" || convo.visitId) return;
  if (!convo.healthConsentGiven && !convo.recordsLinked) {
    convo.wantRecording = true;
    await askDataConsent(ctx, convo);
    return;
  }
  const now = Date.now();
  if (
    convo.lastHospitalName === name &&
    convo.lastHospitalPromptAt &&
    now - convo.lastHospitalPromptAt < HOSPITAL_PROMPT_COOLDOWN_MS
  ) {
    return;
  }
  convo.lastHospitalName = name;
  convo.lastHospitalPromptAt = now;
  if (!isLinked(convo)) {
    convo.wantRecording = true;
    if (convo.connectMode) {
      await say(ctx, convo, copy.hospitalNeedsLink);
      await checkConnect(ctx, convo);
      return;
    }
    await say(ctx, convo, copy.hospitalNeedsId);
    convo.stage = "awaiting_patient_id";
    return;
  }
  await say(ctx, convo, copy.nearHospital(name));
  convo.stage = "awaiting_recording_choice";
}

export async function startRecordingFromHospital(
  ctx: HandlerContext,
  convo: Conversation,
): Promise<void> {
  convo.wantRecording = true;
  if (!isLinked(convo)) {
    if (convo.connectMode) {
      await say(ctx, convo, copy.hospitalNeedsLink);
      await checkConnect(ctx, convo);
      return;
    }
    await say(ctx, convo, copy.hospitalNeedsId);
    convo.stage = "awaiting_patient_id";
    return;
  }
  if (!convo.caseId && convo.finchnodePatientId) {
    // Typed-id flow only: with Connect the case exists from the first message.
    const created = await ctx.core.createCase({
      patient: { finchnode_patient_id: convo.finchnodePatientId },
      channel: "relay",
    });
    convo.caseId = created.case_id;
    await askDataConsent(ctx, convo);
    return;
  }
  await answerRecordingChoice(ctx, convo, BUTTONS.recordYes.id);
}
