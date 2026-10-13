/**
 * Patient-facing copy and buttons. Core notifications are re-rendered here from
 * their `kind` + `data`, so wording can change without touching Core.
 * Button ids on Core notifications are `ntf:<notificationId>:<CHOICE>`; the
 * reply is forwarded to Core as a signed event (handlers/approvalFlow.ts).
 */
import type { FinchNodeClaimSummary, PatientNotification } from "../coreClient";
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
  connectDone: { id: "CONNECT_DONE", label: "I'm done connecting" },
  skipPhone: { id: "SKIP_PHONE", label: "Don't call me" },
  callInsurer: { id: "CALL_INSURER", label: "Call them" },
  callLater: { id: "CALL_LATER", label: "Not now" },
} as const satisfies Record<string, Button>;

/** Plain-language versions of Core error codes the patient can act on. */
const CORE_PROBLEMS: Record<string, string> = {
  finchnode_consent_inactive:
    'Your health-record sharing has ended, so I can\'t read your records. Send "restart" to connect them again.',
  finchnode_unavailable:
    "I couldn't reach your health records just now. Please try again in a minute.",
  finchnode_records_unavailable:
    "Some of your health records couldn't be loaded just now, so I didn't check the bill yet. Please try again in a minute.",
  finchnode_not_authorized:
    "Amyna's connection to health records isn't set up correctly. The Amyna team has to fix it before I can check bills.",
  payer_phone_missing:
    "I don't have your insurance company's phone number yet, so I can't call them. The Amyna team needs to add it.",
  number_not_allowed:
    "I'm not allowed to call that insurance number yet (it isn't on Amyna's approved list).",
  patient_session_required: "Sign-in isn't set up for this Amyna service yet.",
  dev_sessions_disabled: "Sign-in isn't set up for this Amyna service yet.",
  case_forbidden: "That case belongs to someone else, so I can't open it.",
  already_resolved: "That's already taken care of. I'll keep you posted here.",
  call_not_active: "That call has already ended. I'll keep you posted here.",
};

const money = (cents: number) => `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2 })}`;

/** "Jul 18" from "2026-07-18". */
function shortDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year!, month! - 1, day!)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function billLabel(bill: FinchNodeClaimSummary): string {
  const provider = (bill.provider_name ?? "Bill").replace(/\s*\(Synthetic\)/i, "");
  const label = `${shortDate(bill.date_of_service)} · ${money(bill.total_billed_cents)} · ${provider}`;
  return label.length > 80 ? `${label.slice(0, 79)}…` : label; // Relay labels: 1-80 chars
}

export const QUESTION_BUTTONS: Button[] = [BUTTONS.yes, BUTTONS.no, BUTTONS.notSure];
export const APPROVAL_BUTTONS: Button[] = [BUTTONS.review, BUTTONS.approve, BUTTONS.reject];
export const HANDOFF_BUTTONS: Button[] = [BUTTONS.done, BUTTONS.cancel];

/** Must be >= 10 chars; stored verbatim as the consent text in Core. */
export const DATA_CONSENT_TEXT =
  "I allow Amyna to read my FinchNode health records (encounters, procedures, " +
  "medications and claims) to review this bill.";
export const RECORDING_CONSENT_TEXT =
  "I agree that Amyna may record and transcribe this visit for my bill review. " +
  "My doctor and the provider agree to that recording too.";

/** Said once, out loud, before any records are opened. Played back only as their own words. */
export const ACCOUNT_CONSENT_MEMO =
  "Hi, this is [your name]. I give full consent for my assistant to handle this medical bill for me, " +
  "and to bring me into the call whenever something only I can answer is needed.";

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
    "\"this bill looks wrong\".",
  connectRecords: (url: string): OutboundMessage => ({
    text:
      "First, connect your health records so I can check bills against what really happened. " +
      "Tap the link, pick your hospital or clinic, sign in there and approve sharing with Amyna. " +
      "Open it in your phone's browser: FinchNode emails you a link that must open in the same browser.",
    buttons: [{ id: "CONNECT_LINK", label: "Connect my health records", url }, BUTTONS.connectDone],
  }),
  connectStillWaiting: (url: string): OutboundMessage => ({
    text: "I don't see your records yet. Finish connecting with the link, then tap I'm done connecting.",
    buttons: [{ id: "CONNECT_LINK", label: "Connect my health records", url }, BUTTONS.connectDone],
  }),
  hospitalNeedsLink:
    "Looks like you're at a hospital. Connect your health records first, then I'll start the recording.",
  askClaimNumber: "Which bill? Send the claim number printed on it (for example CLM-2026-0915-001).",
  pickBill: (bills: FinchNodeClaimSummary[]): OutboundMessage => ({
    text: "Your claim is already on your records. Which bill should I check? You can also send a claim number.",
    buttons: bills.map((bill) => ({ id: `bill:${bill.external_claim_id}`, label: billLabel(bill) })),
  }),
  billNotItemized: (detail: Record<string, unknown>): OutboundMessage => ({
    text:
      `Your records show ${typeof detail.provider_name === "string" ? `a bill from ${detail.provider_name}` : "that bill"}` +
      (typeof detail.date_of_service === "string" ? ` for ${shortDate(detail.date_of_service)}` : "") +
      (typeof detail.total_billed_cents === "number" ? ` (${money(detail.total_billed_cents)})` : "") +
      ", but only as a total: it doesn't list the individual charges, so I can't check it yet. " +
      "Ask the hospital for the itemized bill. Once it's added to your case, tap Review my bill.",
    buttons: [BUTTONS.reviewBill],
  }),
  billPhotoUnsupported:
    "Thanks! I can't read bill photos or PDFs yet. Tap Review my bill to pick the bill from your health " +
    "records, or send the claim number printed on it.",
  askInsurerCall: (): OutboundMessage => ({
    text:
      "Should I call your insurance company now? I'll use the phone number already on file. " +
      "You don't start the call. I do, after you say yes.",
    buttons: [BUTTONS.callInsurer, BUTTONS.callLater],
  }),
  insurerCallDeclined: "Okay. I won't call your insurance company. Send \"restart\" if you want to go through the bill again.",
  askCallbackPhone: (): OutboundMessage => ({
    text:
      "One more thing before I call: if your insurance company asks for something only you can give, " +
      "like your Social Security number, I'll call your phone and add you to the same call so you can " +
      "tell them yourself. What number should I call? (Never send your SSN here.)",
    buttons: [BUTTONS.skipPhone],
  }),
  callbackPhoneSaved: (masked: string) =>
    `Thanks. If they need you, I'll call ${masked} and add you to the call. Calling your insurance company now.`,
  memoKept:
    "Got it. I'll play your recording to your insurance company at the start of the call, and I'll call you in if they need you.",
  memoNotKept:
    "Got it, thanks. I couldn't save the audio, so on the call I'll tell your insurance company you authorized it.",
  callbackPhoneInvalid: 'That doesn\'t look like a phone number. Send it like 555-123-4567, or tap "Don\'t call me".',
  tryAgain: "Sorry, something went wrong on my side. Please send that again in a minute.",
  // Shown only when the flow truly needs the patient ID (demo/sandbox identity).
  // Explained in plain English; the patient never has to know terms in advance.
  idGuidance:
    "Paste your patient ID (it starts with u_ or fn_pat_) and I'll link the records you already shared.",
  accountMemo: (): OutboundMessage => ({
    text:
      "Before I open anything, record a short voice memo (hold the mic in this chat) and say:\n\n" +
      `"${ACCOUNT_CONSENT_MEMO}"\n\n` +
      "That is your authorization for me to handle this bill and to call you in if the insurer asks for " +
      "something only you can answer, like a Social Security number. Send the memo here, or paste your " +
      "patient ID if you want to skip the recording.",
    buttons: [BUTTONS.recordSkip],
  }),
  locationAsk:
    "Share your location in this chat (Once is fine, or keep sharing while you're at the appointment) " +
    "and I'll ping you if it looks like you're at a hospital so you can record the visit. " +
    'You can also send "I\'m at the hospital".',
  nearHospital: (name: string): OutboundMessage => ({
    text:
      `Looks like you're at ${name}. If you're at a hospital, do you want to record this visit?\n\n` +
      `"${RECORDING_CONSENT_TEXT}"`,
    buttons: [BUTTONS.recordYes, BUTTONS.recordSkip],
  }),
  notNearHospital:
    "Thanks. That doesn't look like a hospital or clinic, so I won't start a recording. Share again when you arrive.",
  hospitalNeedsId:
    "Looks like you're at a hospital. Send your FinchNode patient ID first and I'll start the recording.",
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
  visitNotes: (
    evidence: { description: string; negated?: boolean }[],
    heard?: string,
  ): OutboundMessage => ({
    text:
      (heard?.trim() ? `Here's what I heard:\n"${heard.trim()}"\n\n` : "") +
      (evidence.length
        ? `Here's what I noted from your visit: ${listItems(evidence.map(describeEvidence))}.`
        : "I went through your visit but didn't hear any procedures, tests or medications to check your bill against.") +
      "\n\nGive me a moment. I'm checking whether this bill is already on your records.",
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
    CORE_PROBLEMS[code] ??
    `Sorry, I couldn't do that step (${code}). Send "restart" to start over if you're stuck.`,
  /** A friendly message for a Core error code, or undefined when there is none. */
  coreProblem: (code: string): string | undefined => CORE_PROBLEMS[code],
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
