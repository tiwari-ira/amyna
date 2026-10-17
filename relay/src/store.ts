/** Per-patient conversation state, persisted to an append-only JSONL journal.
 *
 * save() used to rewrite every conversation on every message (~8ms at 5k
 * patients); now it appends one line per *changed* conversation and compacts
 * lazily. On load the last record per user wins. The legacy whole-array JSON
 * file is still imported on first read.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
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
  /** [3.2] Polling cursor: newest notification created_at we have already seen. */
  lastNotificationAt?: string;
}

export class ConversationStore {
  private readonly conversations = new Map<string, Conversation>();
  /** The exact JSON last persisted per user, to detect actual changes. */
  private readonly savedJson = new Map<string, string>();
  private dirtyOps = 0;
  private static readonly COMPACT_EVERY = 512;

  constructor(private readonly path?: string) {
    if (path && existsSync(path)) {
      const raw = readFileSync(path, "utf8").trim();
      if (raw.startsWith("[")) {
        // Legacy format: the whole file was one JSON array of conversations.
        const stored = JSON.parse(raw) as Conversation[];
        for (const convo of stored) {
          this.conversations.set(convo.userId, convo);
          this.savedJson.set(convo.userId, JSON.stringify(convo));
        }
      } else {
        for (const line of raw.split("\n")) {
          if (!line) continue;
          try {
            const convo = JSON.parse(line) as Conversation;
            this.conversations.set(convo.userId, convo);
            this.savedJson.set(convo.userId, line);
          } catch {
            // A torn last line (crash mid-append) is ignored.
          }
        }
      }
    }
  }

  get(userId: string): Conversation {
    const existing = this.conversations.get(userId);
    if (existing) return existing;
    const fresh: Conversation = { userId, stage: "new", delivered: {} };
    this.conversations.set(userId, fresh);
    return fresh;
  }

  /** Look up a conversation without creating one (unknown senders stay unknown). */
  peek(userId: string): Conversation | undefined {
    return this.conversations.get(userId);
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
    for (const convo of this.conversations.values()) {
      const json = JSON.stringify(convo);
      if (this.savedJson.get(convo.userId) === json) continue;
      appendFileSync(this.path, json + "\n");
      this.savedJson.set(convo.userId, json);
      this.dirtyOps += 1;
    }
    if (this.dirtyOps >= ConversationStore.COMPACT_EVERY) this.compact();
  }

  /** One snapshot line per conversation, dropping per-version history. */
  private compact(): void {
    this.dirtyOps = 0;
    if (!this.path) return;
    const body = [...this.conversations.values()].map((c) => JSON.stringify(c)).join("\n");
    writeFileSync(this.path, body ? body + "\n" : "");
  }
}
