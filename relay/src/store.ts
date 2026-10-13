/** Per-patient conversation state, persisted to a JSON file (survives restarts). */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type Stage =
  | "new"
  | "awaiting_patient_id"
  | "awaiting_finchnode_connect"
  | "awaiting_data_consent"
  | "awaiting_account_memo"
  | "awaiting_recording_choice"
  | "recording"
  | "processing_visit"
  | "ready_for_bill"
  | "awaiting_claim_id"
  | "awaiting_callback_phone"
  | "awaiting_insurer_call"
  | "in_review"
  | "done";

export interface DeliveredNotification {
  kind: string;
  interventionId?: string | null;
  disputeId?: string | null;
}

export interface Conversation {
  userId: string;
  stage: Stage;
  finchnodePatientId?: string;
  caseId?: string;
  recordingConsentId?: string;
  visitId?: string;
  recordingRef?: string;
  /** Words heard in the visit memo, shown back to the patient with the notes. */
  heardText?: string;
  claimId?: string;
  reportSent?: boolean;
  chatId?: string;
  /** Set once they agree to health-record access, before a case exists to store it on. */
  healthConsentGiven?: boolean;
  /** The account-authorization voice memo, kept locally until Core has it. */
  authorizationMemo?: { path: string; mimeType: string; uploaded?: boolean };
  locationAsked?: boolean;
  locationSharing?: boolean;
  wantRecording?: boolean;
  lastHospitalName?: string;
  lastHospitalPromptAt?: number;
  /** A pin that arrived while another question was on screen. Checked once that question is done. */
  pendingPlace?: { latitude: number; longitude: number };
  /** FinchNode Connect (sandbox/production): the open session and whether records are linked. */
  connectSessionId?: string;
  connectMode?: boolean;
  recordsLinked?: boolean;
  /** An approval waiting for the patient's callback number (asked once). */
  pendingApproval?: { notificationId: string; eventId: string };
  callbackAsked?: boolean;
  /** Asked once, after they approve the dispute, before the agent dials the insurer. */
  insurerCallAsked?: boolean;
  /** Core notifications already shown to the patient (rendered exactly once). */
  delivered: Record<string, DeliveredNotification>;
}

export class ConversationStore {
  private readonly conversations = new Map<string, Conversation>();

  constructor(private readonly path?: string) {
    if (path && existsSync(path)) {
      const stored = JSON.parse(readFileSync(path, "utf8")) as Conversation[];
      for (const convo of stored) this.conversations.set(convo.userId, convo);
    }
  }

  get(userId: string): Conversation {
    const existing = this.conversations.get(userId);
    if (existing) return existing;
    const fresh: Conversation = { userId, stage: "new", delivered: {} };
    this.conversations.set(userId, fresh);
    return fresh;
  }

  byCase(caseId: string): Conversation | undefined {
    return [...this.conversations.values()].find((c) => c.caseId === caseId);
  }

  reset(userId: string): Conversation {
    this.conversations.delete(userId);
    return this.get(userId);
  }

  save(): void {
    if (!this.path) return;
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify([...this.conversations.values()], null, 2));
  }
}
