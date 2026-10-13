/** Entry point for every patient event from the Relay platform. */
import { CoreError } from "../coreClient";
import type { Conversation } from "../store";
import { BUTTONS, DATA_CONSENT_TEXT, copy } from "../ux/messages";
import type { InboundEvent } from "../webhook";
import {
  answerRecordingChoice,
  awaitVoiceMemo,
  checkVisitEvidence,
  offerRecording,
  onRecordingCompleted,
} from "./appointmentRecording";
import {
  answerOpenPrompt,
  answerInsurerCall,
  redialInsurer,
  captureCallbackPhone,
  captureClaimId,
  replyToNotification,
  reviewBill,
} from "./approvalFlow";
import { keepAuthorizationMemo, uploadAuthorizationMemo } from "./authorizationMemo";
import { checkConnect } from "./connect";
import { answerDataConsent, askDataConsent } from "./consent";
import { type HandlerContext, patientScope, requireCase, say, serialize } from "./context";
import {
  askForLocation,
  flushPendingPlace,
  handleLocationEvent,
  rememberChat,
  startRecordingFromHospital,
} from "./hospitalCheckin";

/** Typed ids (mock/demo only): fixtures, FinchNode subjects, FinchNode demo patients. */
const PATIENT_ID = /fn_pat_[A-Za-z0-9_]+|u_[0-9a-fA-F]{16}|patient-demo-[a-z0-9-]+/;
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
  call: "CALL_INSURER",
};

export function normalizeText(text: string | undefined): string | undefined {
  if (!text) return undefined;
  return TEXT_CHOICES[text.trim().toLowerCase().replace(/[.!]+$/, "")];
}

async function start(ctx: HandlerContext, convo: Conversation): Promise<void> {
  await say(ctx, convo, copy.welcome);
  // Consent comes before a patient ID, a location, or a recording.
  await askDataConsent(ctx, convo);
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
  await uploadAuthorizationMemo(ctx, convo);
  if (convo.healthConsentGiven) {
    await ctx.core.recordConsent(requireCase(convo), {
      consent_type: "health_data_access",
      consent_text: DATA_CONSENT_TEXT,
      channel: "relay",
    });
  }
  await askForLocation(ctx, convo);
  if (convo.wantRecording) {
    convo.wantRecording = false;
    await offerRecording(ctx, convo);
    return;
  }
  convo.stage = "ready_for_bill";
}

/** One patient event; serialized per patient. Core errors the patient can act on (and every
 * 4xx) become a patient message; other 5xx/network errors propagate to the caller. */
export function handleIncoming(ctx: HandlerContext, event: InboundEvent): Promise<void> {
  return serialize(event.from, async () => {
    try {
      // Attach this patient's identity first: cases belong to the signed-in user.
      const scoped = { ...ctx, core: await patientScope(ctx, event.from) };
      await route(scoped, event);
    } catch (error) {
      if (!(error instanceof CoreError)) throw error;
      const friendly = copy.coreProblem(error.code);
      if (!friendly && error.status >= 500) throw error;
      ctx.log(`core rejected ${event.kind} (${error.status} ${error.code})`);
      await say(ctx, ctx.store.get(event.from), friendly ?? copy.coreRejected(error.code));
    } finally {
      ctx.store.save();
    }
  });
}

async function route(ctx: HandlerContext, event: InboundEvent): Promise<void> {
  let convo = ctx.store.get(event.from);
  rememberChat(convo, event.chatId);
  if (event.text?.trim().toLowerCase() === "restart") {
    convo = ctx.store.reset(event.from);
    await start(ctx, convo);
    return;
  }
  if (await handleLocationEvent(ctx, convo, event)) return;
  if (event.kind === "recording") {
    await onRecordingCompleted(ctx, convo, event.recordingUrl);
    return;
  }
  if (event.audio) {
    if (convo.stage === "awaiting_account_memo") {
      ctx.log(`account authorization memo received (${event.audio.mimeType})`);
      const kept = await keepAuthorizationMemo(ctx, convo, event.audio.url, event.audio.mimeType);
      await say(ctx, convo, kept ? copy.memoKept : copy.memoNotKept);
      convo.stage = "awaiting_patient_id";
      await say(ctx, convo, copy.idGuidance);
      return;
    }
    if (convo.stage !== "recording") return await say(ctx, convo, copy.audioNotExpected);
    ctx.log(`voice memo received (${event.audio.mimeType}, ${event.audio.sizeBytes ?? "?"} bytes)`);
    return await onRecordingCompleted(ctx, convo, event.audio.url, event.audio.mimeType);
  }
  if (event.attachment && !event.text?.trim()) {
    return await say(ctx, convo, copy.billPhotoUnsupported);
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
    case "awaiting_finchnode_connect":
      return await checkConnect(ctx, convo);
    case "awaiting_data_consent":
      if (!choice && event.text?.trim()) {
        const understood = await ctx.core.understandMessage(event.text.trim());
        await say(ctx, convo, `${understood.reply}\n\n${copy.dataConsent().text}`);
        return;
      }
      return await answerDataConsent(ctx, convo, choice);
    case "awaiting_account_memo":
      if (choice === BUTTONS.recordSkip.id || choice === "NO") {
        convo.stage = "awaiting_patient_id";
        await say(ctx, convo, copy.idGuidance);
        return;
      }
      if (event.text && PATIENT_ID.test(event.text)) {
        return await capturePatientId(ctx, convo, event.text);
      }
      if (event.text?.trim()) {
        const understood = await ctx.core.understandMessage(event.text.trim());
        await say(ctx, convo, `${understood.reply}\n\n${copy.accountMemo().text}`);
        return;
      }
      await say(ctx, convo, copy.accountMemo());
      return;
    case "awaiting_recording_choice":
      if ((choice === BUTTONS.recordYes.id || choice === "YES") && !convo.caseId) {
        return await startRecordingFromHospital(ctx, convo);
      }
      await answerRecordingChoice(ctx, convo, choice);
      await flushPendingPlace(ctx, convo);
      return;
    case "recording":
      return await awaitVoiceMemo(ctx, convo, choice);
    case "processing_visit":
      if (await checkVisitEvidence(ctx, convo)) return;
      return await say(ctx, convo, copy.stillProcessingVisit);
    case "ready_for_bill":
      if (choice === BUTTONS.recordYes.id) {
        return await answerRecordingChoice(ctx, convo, choice);
      }
      if (choice === BUTTONS.reviewBill.id || /review|bill/i.test(event.text ?? "")) {
        return await reviewBill(ctx, convo);
      }
      return await say(ctx, convo, copy.readyForBill());
    case "awaiting_claim_id":
      return await captureClaimId(ctx, convo, event.text, choice);
    case "awaiting_insurer_call":
      return await answerInsurerCall(ctx, convo, choice);
    case "awaiting_callback_phone":
      return await captureCallbackPhone(ctx, convo, event.text, choice);
    case "in_review":
      if (choice === BUTTONS.callInsurer.id) {
        if (await redialInsurer(ctx, convo)) return;
        return await say(ctx, convo, copy.notUnderstood);
      }
      return await answerOpenPrompt(ctx, convo, choice, event.id);
    case "done":
      if (choice === BUTTONS.callInsurer.id && (await redialInsurer(ctx, convo))) return;
      return await say(ctx, convo, copy.restart);
  }
}
