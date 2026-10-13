/**
 * Bill review: ingest + analyze, then let Core drive the conversation through its
 * notifications (one question at a time, approval card, handoff). Button replies
 * go back to Core as signed, idempotent events. This service only advances what
 * Core leaves to the interface: preparing the dispute and starting the call.
 */
import { randomUUID } from "node:crypto";

import { CoreError, type FinchNodeClaimSummary } from "../coreClient";
import type { Conversation } from "../store";
import { BUTTONS, copy } from "../ux/messages";
import { type HandlerContext, requireCase, say } from "./context";
import { uploadAuthorizationMemo } from "./authorizationMemo";
import { deliverPending } from "./humanHandoff";
import { sendCaseReport } from "./patientReport";

/** Claim numbers as patients see them: CLM-..., FinchNode claim ids, FinchNode record ids. */
const CLAIM_NUMBER = /\b(?:CLM-[A-Za-z0-9-]+|claim-[A-Za-z0-9-]+|rec_[0-9a-f]{24})\b/i;
const TERMINAL = new Set(["RESOLVED", "REJECTED", "FAILED"]);
const MAX_ADVANCE_STEPS = 6;
const MAX_BILL_BUTTONS = 4;

export async function reviewBill(ctx: HandlerContext, convo: Conversation): Promise<void> {
  const caseId = requireCase(convo);
  const detail = await ctx.core.getCase(caseId);
  if (detail.claim) {
    // The itemized bill is already on the case (e.g. added after a summary-only claim).
    convo.claimId = detail.claim.claim_id;
    await analyzeAndReview(ctx, convo);
    return;
  }
  let bills: FinchNodeClaimSummary[] = [];
  try {
    bills = (await ctx.core.finchnodeClaims(caseId)).claims ?? [];
  } catch (error) {
    if (!(error instanceof CoreError) || error.status >= 500) throw error;
    // e.g. consent not recorded yet: fall back to asking for the claim number
  }
  convo.stage = "awaiting_claim_id";
  if (bills.length) {
    await say(ctx, convo, copy.pickBill(bills.slice(0, MAX_BILL_BUTTONS)));
    return;
  }
  await say(ctx, convo, copy.askClaimNumber);
}

export async function captureClaimId(
  ctx: HandlerContext,
  convo: Conversation,
  text: string | undefined,
  choice?: string,
): Promise<void> {
  if (choice === BUTTONS.reviewBill.id) {
    await reviewBill(ctx, convo);
    return;
  }
  const picked = choice?.startsWith("bill:") ? choice.slice("bill:".length) : text?.match(CLAIM_NUMBER)?.[0];
  if (!picked) {
    await say(ctx, convo, "I couldn't find a claim number. It looks like CLM-2026-0915-001.");
    return;
  }
  if (!convo.claimId) {
    let claim;
    try {
      claim = await ctx.core.ingestClaim({
        case_id: requireCase(convo),
        source: "finchnode",
        finchnode_claim_id: picked, // Core matches any capitalization
      });
    } catch (error) {
      if (!(error instanceof CoreError)) throw error;
      if (error.status === 404) {
        await say(
          ctx,
          convo,
          "I can't find that claim on your health records. Check the number on the bill, or " +
            'send "restart" to start over.',
        );
        return;
      }
      if (error.code === "claim_not_itemized") {
        convo.stage = "ready_for_bill";
        await say(ctx, convo, copy.billNotItemized(error.detail));
        return;
      }
      if (error.code !== "claim_exists") throw error;
      // A bill was attached meanwhile: review that one.
      claim = (await ctx.core.getCase(requireCase(convo))).claim;
      if (!claim) throw error;
    }
    convo.claimId = claim.claim_id;
    ctx.store.save(); // a platform retry after a later failure must not re-ingest
  }
  await analyzeAndReview(ctx, convo);
}

async function analyzeAndReview(ctx: HandlerContext, convo: Conversation): Promise<void> {
  await say(ctx, convo, copy.reviewing);
  // Idempotent in Core. With live agents it answers "pending" and Core notifies us later.
  await ctx.core.analyzeClaim(convo.claimId!);
  convo.stage = "in_review"; // only after Core accepted the work, so a retry re-runs this step
  await syncAndAdvance(ctx, convo);
}

/** After they approve the dispute: the agent asks, then dials the insurer number already on file. */
export async function answerInsurerCall(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
): Promise<void> {
  const pending = convo.pendingApproval;
  if (!pending) {
    convo.stage = "in_review";
    return;
  }
  if (choice !== BUTTONS.callInsurer.id && choice !== "YES") {
    convo.pendingApproval = undefined;
    convo.stage = "done";
    await say(ctx, convo, copy.insurerCallDeclined);
    return;
  }
  await uploadAuthorizationMemo(ctx, convo); // last chance before the call plays it
  if (ctx.config.askCallbackPhone && !convo.callbackAsked) {
    // Relay contacts carry no phone number, so the patient tells us where to reach them.
    const detail = await ctx.core.getCase(requireCase(convo));
    if (!detail.callback_phone_masked) {
      convo.callbackAsked = true;
      convo.stage = "awaiting_callback_phone"; // pendingApproval is kept for the next reply
      await say(ctx, convo, copy.askCallbackPhone());
      return;
    }
  }
  convo.pendingApproval = undefined;
  convo.stage = "in_review";
  await replyToNotification(ctx, convo, `ntf:${pending.notificationId}:APPROVE`, pending.eventId);
}

/** Before the insurer call: the patient's own number for the human-merge leg (asked once). */
export async function captureCallbackPhone(
  ctx: HandlerContext,
  convo: Conversation,
  text: string | undefined,
  choice: string | undefined,
): Promise<void> {
  const pending = convo.pendingApproval;
  if (!pending) {
    convo.stage = "in_review";
    return;
  }
  if (choice !== BUTTONS.skipPhone.id && choice !== "NO") {
    try {
      const updated = await ctx.core.setCallbackPhone(requireCase(convo), text ?? "");
      await say(ctx, convo, copy.callbackPhoneSaved(updated.callback_phone_masked ?? "your phone"));
    } catch (error) {
      if (error instanceof CoreError && error.code === "invalid_phone") {
        await say(ctx, convo, copy.callbackPhoneInvalid);
        return;
      }
      throw error;
    }
  }
  convo.pendingApproval = undefined;
  convo.stage = "in_review";
  await replyToNotification(ctx, convo, `ntf:${pending.notificationId}:APPROVE`, pending.eventId);
}

/** A tap on a Core notification button (`ntf:<notificationId>:<CHOICE>`). */
export async function replyToNotification(
  ctx: HandlerContext,
  convo: Conversation,
  buttonId: string,
  eventId: string,
): Promise<void> {
  const [, notificationId, choice] = buttonId.split(":");
  const delivered = convo.delivered[notificationId ?? ""];
  if (!delivered || !choice) {
    await say(ctx, convo, copy.notUnderstood);
    return;
  }
  if (delivered.kind === "approval_request" && choice === "REVIEW") {
    const detail = await ctx.core.getCase(requireCase(convo));
    await say(ctx, convo, detail.dispute?.letter_text ?? "The dispute letter is not ready yet.");
    return;
  }
  if (delivered.kind === "approval_request" && choice === "APPROVE" && !convo.insurerCallAsked) {
    convo.insurerCallAsked = true;
    convo.pendingApproval = { notificationId: notificationId!, eventId };
    convo.stage = "awaiting_insurer_call";
    await say(ctx, convo, copy.askInsurerCall());
    return;
  }
  await ctx.core.sendSignedEvent({
    event_id: eventId,
    event_type: "button_reply",
    case_id: requireCase(convo),
    notification_id: notificationId,
    intervention_id: delivered.interventionId ?? null,
    dispute_id: delivered.disputeId ?? null,
    choice,
    occurred_at: new Date().toISOString(),
  });
  await syncAndAdvance(ctx, convo);
}

/** Typed (non-button) reply while a Core prompt is open: map it onto the latest card. */
export async function answerOpenPrompt(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
  eventId: string,
): Promise<void> {
  const open = Object.entries(convo.delivered)
    .filter(([, n]) => ["confirmation_question", "approval_request", "human_handoff"].includes(n.kind))
    .at(-1);
  if (!open || !choice) {
    // Nothing to answer yet: show anything Core queued (e.g. after a failed send) first.
    const shown = Object.keys(convo.delivered).length;
    await syncAndAdvance(ctx, convo);
    if (Object.keys(convo.delivered).length === shown) await say(ctx, convo, copy.notUnderstood);
    return;
  }
  await replyToNotification(ctx, convo, `ntf:${open[0]}:${choice}`, eventId);
}

const ACTIVE_CALL = new Set(["initiating", "in_progress", "paused_for_human"]);

/** Dial again after a call ended without a result. The first call still starts from approval. */
export async function redialInsurer(ctx: HandlerContext, convo: Conversation): Promise<boolean> {
  if (!convo.caseId) return false;
  const detail = await ctx.core.getCase(convo.caseId);
  const active = (detail.calls ?? []).some((call) => ACTIVE_CALL.has(call.status));
  if (detail.state !== "APPROVED" || !detail.dispute?.patient_authorization_id || active) {
    return false;
  }
  convo.stage = "in_review";
  await ctx.core.startCall(convo.caseId);
  await syncAndAdvance(ctx, convo);
  return true;
}

/** Show new Core notifications, then advance steps Core leaves to the interface. */
export async function syncAndAdvance(ctx: HandlerContext, convo: Conversation): Promise<void> {
  const caseId = requireCase(convo);
  for (let step = 0; step < MAX_ADVANCE_STEPS; step += 1) {
    await deliverPending(ctx, convo);
    const detail = await ctx.core.getCase(caseId);
    if (detail.state === "FINDINGS_CONFIRMED" && !detail.dispute) {
      // "pending" = live agents are drafting; Core notifies us when the package is ready.
      // A later sync re-asks, which Core and the agents treat as a no-op once it exists.
      const preparation = await ctx.core.prepareDispute(caseId);
      if (preparation.status === "pending") return;
      continue;
    }
    if (
      detail.state === "APPROVED" &&
      detail.dispute?.patient_authorization_id &&
      (detail.calls ?? []).length === 0
    ) {
      await ctx.core.startCall(caseId);
      continue;
    }
    if (TERMINAL.has(detail.state)) {
      convo.stage = "done";
      await sendCaseReport(ctx, convo, detail);
    }
    return;
  }
}

export function newEventId(): string {
  return `evt_${randomUUID().replace(/-/g, "").slice(0, 20)}`;
}
