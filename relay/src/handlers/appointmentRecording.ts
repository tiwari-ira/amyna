/**
 * Appointment recording: recording consent -> the patient sends a voice memo of the
 * appointment -> ingest audio, transcribe, and send the transcript to Core (which
 * extracts evidence). In mock mode the recording "completes" immediately.
 */
import { BUTTONS, RECORDING_CONSENT_TEXT, copy } from "../ux/messages";
import type { Conversation } from "../store";
import { ingestRecording } from "./audioIngest";
import type { Schemas } from "../coreClient";
import { reviewBill } from "./approvalFlow";
import { type HandlerContext, requireCase, say, serialize } from "./context";
import { TranscriptionError } from "./transcription";

export async function offerRecording(ctx: HandlerContext, convo: Conversation): Promise<void> {
  await say(ctx, convo, copy.recordingOffer());
  convo.stage = "awaiting_recording_choice";
}

export async function answerRecordingChoice(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
): Promise<void> {
  if (choice === BUTTONS.recordSkip.id || choice === "NO") {
    await say(ctx, convo, copy.readyForBill());
    convo.stage = "ready_for_bill";
    return;
  }
  if (choice !== BUTTONS.recordYes.id && choice !== "YES") {
    await say(ctx, convo, copy.notUnderstood);
    return;
  }
  if (!convo.recordingConsentId) {
    const consent = await ctx.core.recordConsent(requireCase(convo), {
      consent_type: "recording",
      consent_text: RECORDING_CONSENT_TEXT,
      channel: "relay",
    });
    convo.recordingConsentId = consent.consent_id;
  }
  convo.pendingPlace = undefined;
  convo.stage = "recording";
  if (!ctx.relay.simulatesRecording) {
    await say(ctx, convo, copy.sendVoiceMemo());
    return;
  }
  await ctx.relay.startRecording(convo.userId);
  await say(ctx, convo, copy.recordingStarted);
  await onRecordingCompleted(ctx, convo);
}

/** While waiting for the voice memo: Skip moves on, anything else repeats the ask. */
export async function awaitVoiceMemo(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
): Promise<void> {
  if (choice === BUTTONS.recordSkip.id || choice === "NO") {
    await say(ctx, convo, copy.readyForBill());
    convo.stage = "ready_for_bill";
    return;
  }
  await say(ctx, convo, ctx.relay.simulatesRecording ? copy.recordingStarted : copy.sendVoiceMemo());
}

export async function onRecordingCompleted(
  ctx: HandlerContext,
  convo: Conversation,
  recordingUrl?: string,
  mimeType?: string,
): Promise<void> {
  if (!convo.recordingConsentId) throw new Error("recording completed without recording consent");
  if (!convo.visitId) {
    if (recordingUrl) await say(ctx, convo, copy.transcribing);
    convo.recordingRef = await ingestRecording(ctx, recordingUrl, mimeType);
  }
  // Transcribe before creating the visit: Core allows one visit per case, so an
  // unusable memo must not claim it.
  let transcript;
  try {
    transcript = await ctx.transcriber.transcribe(convo.recordingRef ?? "");
  } catch (error) {
    if (!(error instanceof TranscriptionError)) throw error;
    ctx.log(`transcription failed: ${error.message}`);
    convo.recordingRef = undefined;
    await say(ctx, convo, copy.transcriptionFailed(error.message));
    return;
  }
  if (!convo.visitId) {
    const visit = await ctx.core.createVisit({
      case_id: requireCase(convo),
      recording_consent_id: convo.recordingConsentId,
      recording_ref: convo.recordingRef ?? "",
    });
    convo.visitId = visit.visit_id;
    ctx.store.save(); // a retry must not create a second visit
  }
  // Core makes this retry-safe: an already-stored transcript just re-runs extraction.
  const result = await ctx.core.completeVisit(convo.visitId, transcript);
  convo.heardText = transcript.segments
    .map((segment) => segment.text.trim())
    .filter(Boolean)
    .join(" ");
  if (result.status === "pending") {
    // FETCH_MODE=agentverse: the Visit Agent writes the evidence back to Core shortly.
    // Core takes no bill before that, so the patient waits here.
    convo.stage = "processing_visit";
    void watchVisitEvidence(ctx, convo.userId);
    return;
  }
  await showVisitNotes(ctx, convo, result.evidence);
}

async function showVisitNotes(
  ctx: HandlerContext,
  convo: Conversation,
  evidence: Schemas["EvidenceItem"][],
): Promise<void> {
  await say(ctx, convo, copy.visitNotes(evidence, convo.heardText));
  convo.stage = "ready_for_bill";
  await reviewBill(ctx, convo);
}

/** True (and the notes are shown) once the Visit Agent has recorded the visit's evidence. */
export async function checkVisitEvidence(ctx: HandlerContext, convo: Conversation): Promise<boolean> {
  const detail = await ctx.core.getCase(requireCase(convo));
  if (detail.visit?.status !== "evidence_extracted") return false;
  await showVisitNotes(ctx, convo, detail.evidence ?? []);
  return true;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Poll Core in the background so the webhook returns now. If the agents are slow, any
 * later message from the patient checks again (and survives a service restart). */
export async function watchVisitEvidence(ctx: HandlerContext, userId: string): Promise<void> {
  const deadline = Date.now() + ctx.config.visitWaitMs;
  while (Date.now() < deadline) {
    await sleep(ctx.config.visitPollMs);
    try {
      const settled = await serialize(userId, async () => {
        const convo = ctx.store.get(userId);
        if (convo.stage !== "processing_visit") return true;
        const ready = await checkVisitEvidence(ctx, convo);
        ctx.store.save();
        return ready;
      });
      if (settled) return;
    } catch (error) {
      ctx.log(`visit evidence check failed: ${(error as Error).message}`);
    }
  }
  await serialize(userId, async () => {
    const convo = ctx.store.get(userId);
    if (convo.stage === "processing_visit") await say(ctx, convo, copy.visitTakingLong);
  }).catch((error: Error) => ctx.log(`could not send visit status: ${error.message}`));
}
