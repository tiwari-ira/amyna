/** Entry point for every patient event from the Relay platform. */
import { CoreError } from "../coreClient";
import type { Conversation } from "../store";
import { BUTTONS, copy } from "../ux/messages";
import type { InboundEvent } from "../webhook";
import {
  answerRecordingChoice,
  awaitVoiceMemo,
  checkVisitEvidence,
  onRecordingCompleted,
} from "./appointmentRecording";
import { answerOpenPrompt, captureClaimId, replyToNotification, reviewBill } from "./approvalFlow";
import { answerDataConsent, askDataConsent } from "./consent";
import { type HandlerContext, patientScope, say, serialize } from "./context";

const PATIENT_ID = /fn_pat_[A-Za-z0-9_]+/;
const TEXT_CHOICES: Record<string, string> = {
  yes: "YES",
  y: "YES",
  no: "NO",
  n: "NO",
  "not sure": "NOT_SURE",
  unsure: "NOT_SURE",
  approve: "APPROVE",
  reject: "REJECT",
  review: "REVIEW",
  done: "DONE",
  cancel: "CANCEL",
};

export function normalizeText(text: string | undefined): string | undefined {
  if (!text) return undefined;
  return TEXT_CHOICES[text.trim().toLowerCase().replace(/[.!]+$/, "")];
}

async function start(ctx: HandlerContext, convo: Conversation): Promise<void> {
  await say(ctx, convo, copy.welcome);
  convo.stage = "awaiting_patient_id";
}

async function capturePatientId(
  ctx: HandlerContext,
  convo: Conversation,
  text: string | undefined,
): Promise<void> {
  const patientId = text?.match(PATIENT_ID)?.[0];
  if (!patientId) {
    // Understand the patient's own words first; never demand an ID outright.
    // Amyna replies in plain English, then guides gently only if needed.
    const message = (text ?? "").trim();
    if (message) {
      const understood = await ctx.core.understandMessage(message);
      await say(ctx, convo, `${understood.reply}\n\n${copy.idGuidance}`);
    } else {
      await say(ctx, convo, copy.idGuidance);
    }
    return;
  }
  if (!convo.caseId || convo.finchnodePatientId !== patientId) {
    const created = await ctx.core.createCase({
      patient: { finchnode_patient_id: patientId },
      channel: "relay",
    });
    convo.finchnodePatientId = patientId;
    convo.caseId = created.case_id;
  }
  await askDataConsent(ctx, convo);
}

/** One patient event; serialized per patient. Core 4xx errors become a patient message
 * (retrying would fail the same way); 5xx/network errors propagate so the platform retries. */
export function handleIncoming(ctx: HandlerContext, event: InboundEvent): Promise<void> {
  return serialize(event.from, async () => {
    // Attach this patient's identity first: cases belong to the signed-in user.
    const scoped = { ...ctx, core: await patientScope(ctx, event.from) };
    try {
      await route(scoped, event);
    } catch (error) {
      if (!(error instanceof CoreError) || error.status >= 500) throw error;
      ctx.log(`core rejected ${event.kind} (${error.status} ${error.code})`);
      await say(scoped, scoped.store.get(event.from), copy.coreRejected(error.code));
    } finally {
      scoped.store.save();
    }
  });
}

async function route(ctx: HandlerContext, event: InboundEvent): Promise<void> {
  let convo = ctx.store.get(event.from);
  if (event.text?.trim().toLowerCase() === "restart") {
    convo = ctx.store.reset(event.from);
    await start(ctx, convo);
    return;
  }
  if (event.kind === "recording") {
    await onRecordingCompleted(ctx, convo, event.recordingUrl);
    return;
  }
  if (event.audio) {
    if (convo.stage !== "recording") return await say(ctx, convo, copy.audioNotExpected);
    ctx.log(`voice memo received (${event.audio.mimeType}, ${event.audio.sizeBytes ?? "?"} bytes)`);
    return await onRecordingCompleted(ctx, convo, event.audio.url, event.audio.mimeType);
  }
  const choice = event.buttonId ?? normalizeText(event.text);
  if (choice?.startsWith("ntf:")) {
    await replyToNotification(ctx, convo, choice, event.id);
    return;
  }
  switch (convo.stage) {
    case "new":
      return await start(ctx, convo);
    case "awaiting_patient_id":
      return await capturePatientId(ctx, convo, event.text);
    case "awaiting_data_consent":
      return await answerDataConsent(ctx, convo, choice);
    case "awaiting_recording_choice":
      return await answerRecordingChoice(ctx, convo, choice);
    case "recording":
      return await awaitVoiceMemo(ctx, convo, choice);
    case "processing_visit":
      if (await checkVisitEvidence(ctx, convo)) return;
      return await say(ctx, convo, copy.stillProcessingVisit);
    case "ready_for_bill":
      if (choice === BUTTONS.reviewBill.id || /review|bill/i.test(event.text ?? "")) {
        return await reviewBill(ctx, convo);
      }
      return await say(ctx, convo, copy.readyForBill());
    case "awaiting_claim_id":
      return await captureClaimId(ctx, convo, event.text);
    case "in_review":
      return await answerOpenPrompt(ctx, convo, choice, event.id);
    case "done":
      return await say(ctx, convo, copy.restart);
  }
}
