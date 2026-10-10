/**
 * Core -> patient notifications. Core pushes human-handoff requests here during
 * insurer calls (NOTIFY_MODE=relay_service), along with every other prompt
 * (questions, approval requests, status). In mock mode the service pulls the same
 * notifications from Core's outbox instead. Each one is rendered exactly once.
 */
import type { PatientNotification } from "../coreClient";
import type { Conversation } from "../store";
import { renderNotification } from "../ux/messages";
import { onFinchNodeConnected } from "./connect";
import { type HandlerContext, patientScope, requireCase, serialize } from "./context";
import { sendCaseReport } from "./patientReport";

/** Notifications being sent right now (push and pull can race for the same one). */
const inFlight = new Set<string>();

export async function deliverNotification(
  ctx: HandlerContext,
  convo: Conversation,
  notification: PatientNotification,
): Promise<string | undefined> {
  const id = notification.notification_id;
  if (convo.delivered[id] || inFlight.has(id)) return undefined;
  inFlight.add(id);
  try {
    const sent = await ctx.relay.sendMessage(convo.userId, renderNotification(notification));
    // Recorded only after a successful send, so a failed send is retried by the next pull.
    convo.delivered[id] = {
      kind: notification.kind,
      interventionId: notification.intervention_id,
      disputeId: notification.dispute_id,
    };
    return sent.id;
  } finally {
    inFlight.delete(id);
  }
}

/** Pull Core's outbox for this case and show anything not shown yet. */
export async function deliverPending(ctx: HandlerContext, convo: Conversation): Promise<void> {
  for (const notification of await ctx.core.notifications(requireCase(convo))) {
    await deliverNotification(ctx, convo, notification);
  }
}

export interface CoreNotifyPayload {
  notification: PatientNotification;
  case: { case_id: string; channel: string; finchnode_patient_id: string };
}

/** POST /internal/notify (Core -> Relay service). Throws when the platform send fails. */
export async function onCoreNotify(
  ctx: HandlerContext,
  payload: CoreNotifyPayload,
): Promise<{ delivered: boolean; delivery_ref?: string }> {
  const convo = ctx.store.byCase(payload.notification.case_id);
  if (!convo) {
    ctx.log(`no Relay conversation for case ${payload.notification.case_id}; skipping`);
    return { delivered: false };
  }
  return serialize(convo.userId, async () => {
    const ref = await deliverNotification(ctx, convo, payload.notification);
    await afterNotification(ctx, convo, payload.notification);
    ctx.store.save();
    return { delivered: true, delivery_ref: ref };
  });
}

/** Advance the conversation for pushes that end or unblock a step. */
async function afterNotification(
  ctx: HandlerContext,
  convo: Conversation,
  notification: PatientNotification,
): Promise<void> {
  if (notification.kind === "final_report") {
    // The conversation is over: add the itemized summary once, as the pull path does.
    convo.stage = "done";
    const core = await patientScope(ctx, convo.userId); // pushes carry no patient session
    await sendCaseReport(ctx, convo, await core.getCase(notification.case_id));
    return;
  }
  const event = notification.data?.event;
  if (event === "finchnode_connected" && convo.stage === "awaiting_finchnode_connect") {
    await onFinchNodeConnected(ctx, convo);
  }
}
