/** Health-data consent (required before Core reads FinchNode records). */
import { BUTTONS, DATA_CONSENT_TEXT, copy } from "../ux/messages";
import type { Conversation } from "../store";
import { answerRecordingChoice, offerRecording } from "./appointmentRecording";
import { askForLocation } from "./hospitalCheckin";
import { type HandlerContext, requireCase, say } from "./context";

export async function askDataConsent(ctx: HandlerContext, convo: Conversation): Promise<void> {
  await say(ctx, convo, copy.dataConsent());
  convo.stage = "awaiting_data_consent";
}

/** Spoken authorization, before a patient ID or any record is opened. */
export async function askAccountMemo(ctx: HandlerContext, convo: Conversation): Promise<void> {
  await say(ctx, convo, copy.accountMemo());
  convo.stage = "awaiting_account_memo";
}

export async function answerDataConsent(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
): Promise<void> {
  if (choice === BUTTONS.consentYes.id || choice === "YES") {
    convo.healthConsentGiven = true;
    if (!convo.caseId) {
      await askAccountMemo(ctx, convo);
      return;
    }
    await ctx.core.recordConsent(requireCase(convo), {
      consent_type: "health_data_access",
      consent_text: DATA_CONSENT_TEXT,
      channel: "relay",
    });
    if (convo.wantRecording) {
      convo.wantRecording = false;
      await answerRecordingChoice(ctx, convo, BUTTONS.recordYes.id);
      return;
    }
    await offerRecording(ctx, convo);
    await askForLocation(ctx, convo);
    return;
  }
  if (choice === BUTTONS.consentNo.id || choice === "NO") {
    await say(ctx, convo, copy.consentDeclined);
    convo.stage = "done";
    return;
  }
  await say(ctx, convo, copy.notUnderstood);
}
