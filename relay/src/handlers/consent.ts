/** Health-data consent (required before Core reads FinchNode records). */
import { BUTTONS, DATA_CONSENT_TEXT, copy } from "../ux/messages";
import type { Conversation } from "../store";
import { offerRecording } from "./appointmentRecording";
import { type HandlerContext, requireCase, say } from "./context";

export async function askDataConsent(ctx: HandlerContext, convo: Conversation): Promise<void> {
  await say(ctx, convo, copy.dataConsent());
  convo.stage = "awaiting_data_consent";
}

export async function answerDataConsent(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
): Promise<void> {
  if (choice === BUTTONS.consentYes.id || choice === "YES") {
    await ctx.core.recordConsent(requireCase(convo), {
      consent_type: "health_data_access",
      consent_text: DATA_CONSENT_TEXT,
      channel: "relay",
    });
    await offerRecording(ctx, convo);
    return;
  }
  if (choice === BUTTONS.consentNo.id || choice === "NO") {
    await say(ctx, convo, copy.consentDeclined);
    convo.stage = "done";
    return;
  }
  await say(ctx, convo, copy.notUnderstood);
}
