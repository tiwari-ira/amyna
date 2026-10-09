/** Per-patient conversation state, persisted to a JSON file (survives restarts). */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type Stage =
  | "new"
  | "awaiting_patient_id"
  | "awaiting_data_consent"
  | "awaiting_recording_choice"
  | "recording"
  | "processing_visit"
  | "ready_for_bill"
  | "awaiting_claim_id"
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
  claimId?: string;
  reportSent?: boolean;
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
