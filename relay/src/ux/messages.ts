/**
 * Patient-facing copy and buttons. Core notifications are re-rendered here from
 * their `kind` + `data`, so wording can change without touching Core.
 * Button ids on Core notifications are `ntf:<notificationId>:<CHOICE>`; the
 * reply is forwarded to Core as a signed event (handlers/approvalFlow.ts).
 */
import type { PatientNotification } from "../coreClient";
import type { Button, OutboundMessage } from "../relayAdapter/types";

export const BUTTONS = {
  consentYes: { id: "CONSENT_YES", label: "I consent" },
  consentNo: { id: "CONSENT_NO", label: "No thanks" },
  recordYes: { id: "RECORD_YES", label: "Record my visit" },
  recordSkip: { id: "RECORD_SKIP", label: "Skip" },
  reviewBill: { id: "REVIEW_BILL", label: "Review my bill" },
  yes: { id: "YES", label: "Yes" },
  no: { id: "NO", label: "No" },
  notSure: { id: "NOT_SURE", label: "Not sure" },
  review: { id: "REVIEW", label: "Review" },
  approve: { id: "APPROVE", label: "Approve" },
  reject: { id: "REJECT", label: "Reject" },
  done: { id: "DONE", label: "Done" },
  cancel: { id: "CANCEL", label: "Cancel call" },
} as const satisfies Record<string, Button>;

export const QUESTION_BUTTONS: Button[] = [BUTTONS.yes, BUTTONS.no, BUTTONS.notSure];
export const APPROVAL_BUTTONS: Button[] = [BUTTONS.review, BUTTONS.approve, BUTTONS.reject];
export const HANDOFF_BUTTONS: Button[] = [BUTTONS.done, BUTTONS.cancel];

/** Must be >= 10 chars; stored verbatim as the consent text in Core. */
export const DATA_CONSENT_TEXT =
  "I allow Amyna to read my FinchNode health records (encounters, procedures, " +
  "medications and claims) to review this bill.";
export const RECORDING_CONSENT_TEXT =
  "I agree that Amyna may record and transcribe this appointment for my bill review.";

/** The extractor marks ruled-out services as "NOT anesthesia"; patients read "no anesthesia". */
function describeEvidence(item: { description: string; negated?: boolean }): string {
  return item.negated ? `no ${item.description.replace(/^NOT\s+/i, "")}` : item.description;
}

/** "a", "a and b", "a, b and c". */
export function listItems(items: string[]): string {
  if (items.length <= 2) return items.join(" and ");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

export const copy = {
  welcome:
    "Hi! I'm Amyna. I check medical bills against your health records and help fix " +
    "billing mistakes.\n\nTell me what's going on in your own words -- for example " +
    "\"this bill looks wrong\" -- or send me a photo of the bill.",
  // Shown only when the flow truly needs the patient ID (demo/sandbox identity).
  // Explained in plain English; the patient never has to know terms in advance.
  idGuidance:
    "When you're ready, paste your patient ID (the code starting with fn_pat_ on " +
    "your records paperwork) and I'll link your health records. If you don't know " +
    "it, just tell me what the bill says and I'll help you find it.",
  dataConsent: (): OutboundMessage => ({
    text: `To review your bills I need your permission:\n\n"${DATA_CONSENT_TEXT}"`,
    buttons: [BUTTONS.consentYes, BUTTONS.consentNo],
  }),
  consentDeclined: "Understood. I won't access your records. Send \"restart\" any time.",
  recordingOffer: (): OutboundMessage => ({
    text:
      "Want me to record and transcribe your appointment so I can check the bill against " +
      `what actually happened?\n\n"${RECORDING_CONSENT_TEXT}"`,
    buttons: [BUTTONS.recordYes, BUTTONS.recordSkip],
  }),
  recordingStarted: "Recording started. I'll process it when your appointment ends.",
  sendVoiceMemo: (): OutboundMessage => ({
    text:
      "Great. Record your appointment as a voice memo (hold the mic button in this chat) " +
      "and send it here. I'll transcribe it and note what was done.",
    buttons: [BUTTONS.recordSkip],
  }),
  transcribing: "Got your recording. Give me a moment to go through it.",
  stillProcessingVisit:
    "I'm still going through your visit. I'll message you as soon as I'm done.",
  visitTakingLong:
    "Going through your visit is taking longer than usual. Send me any message and I'll check again.",
  transcriptionFailed: (reason: string): OutboundMessage => ({
    text: `I couldn't transcribe that recording (${reason}). Please send the voice memo again.`,
    buttons: [BUTTONS.recordSkip],
  }),
  audioNotExpected:
    "Thanks, but I only use recordings right after you tap Record my visit. Send \"restart\" to start over.",
  visitNotes: (evidence: { description: string; negated?: boolean }[]): OutboundMessage => ({
    text:
      (evidence.length
        ? `Here's what I noted from your visit: ${listItems(evidence.map(describeEvidence))}.`
        : "I went through your visit but didn't hear any procedures, tests or medications to check your bill against.") +
      "\n\nWhen your bill arrives, tap Review my bill and I'll compare it with your visit.",
    buttons: [BUTTONS.reviewBill],
  }),
  readyForBill: (): OutboundMessage => ({
    text: "Tap Review my bill when you're ready.",
    buttons: [BUTTONS.reviewBill],
  }),
  reviewing: "Reviewing your bill against your records and visit recording...",
  restart: "Send \"restart\" to review another bill.",
  notUnderstood: "Sorry, I didn't catch that. Please use the buttons above.",
  coreRejected: (code: string) =>
    `Sorry, I couldn't do that step (${code}). Send "restart" to start over if you're stuck.`,
};

function withNotificationIds(notificationId: string, buttons: Button[]): Button[] {
  return buttons.map((b) => ({ id: `ntf:${notificationId}:${b.id}`, label: b.label }));
}

/** Render a Core notification for the patient. */
export function renderNotification(n: PatientNotification): OutboundMessage {
  const buttons = (n.buttons ?? []).map((b) => ({ id: b.id, label: b.label }));
  switch (n.kind) {
    case "confirmation_question":
      return { text: n.text, buttons: withNotificationIds(n.notification_id, QUESTION_BUTTONS) };
    case "approval_request":
      return { text: n.text, buttons: withNotificationIds(n.notification_id, APPROVAL_BUTTONS) };
    case "human_handoff":
      return {
        text: n.text,
        buttons: withNotificationIds(n.notification_id, buttons.length ? buttons : HANDOFF_BUTTONS),
      };
    default:
      return { text: n.text };
  }
}
