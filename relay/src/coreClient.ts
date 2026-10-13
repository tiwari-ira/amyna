/**
 * Typed client for Amyna Core. The Relay service talks ONLY to Core.
 * Types are generated from Core's OpenAPI (`make ts-types` -> shared/ts/core.ts).
 */
import type { components } from "../../shared/ts/core";
import type { CoreApi } from "./handlers/context";
import { signCoreEvent } from "./webhook";

export type Schemas = components["schemas"];
export type Case = Schemas["Case"];
export type CaseDetail = Schemas["CaseDetail"];
export type HumanIntervention = Schemas["HumanIntervention"];
export type PatientNotification = Schemas["PatientNotification"];
export type Dispute = Schemas["Dispute"];
export type FinchNodeClaimSummary = Schemas["FinchNodeClaimSummary"];

export class CoreError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    /** Core's structured detail (``{code, message, ...extra}``), when it sent one. */
    readonly detail: Record<string, unknown> = {},
  ) {
    super(`${status} ${code}: ${message}`);
  }
}

type FetchLike = typeof fetch;

// Forwarded on every patient-bound call; Core resolves it to the owning account.
export const PATIENT_TOKEN_HEADER = "X-Amyna-Patient-Token";

export class CoreClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl: string,
    private readonly token: string,
    private readonly webhookSecret: string,
    private readonly fetchImpl: FetchLike = fetch,
    private readonly patientToken: string | null = null,
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, ""); // "http://core:8080/" must not become "//cases"
  }

  /** A client that forwards the patient's session token (identity binding, task 19). */
  withPatientToken(patientToken: string): CoreApi {
    return new CoreClient(this.baseUrl, this.token, this.webhookSecret, this.fetchImpl, patientToken);
  }

  /** The Amyna session for a Relay handle (Relay is the identity provider for `relay:` subjects). */
  startAuthSession(body: Schemas["AuthSessionCreate"]): Promise<Schemas["AuthSessionResult"]> {
    return this.request("POST", "/auth/session", body);
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
      "Content-Type": "application/json",
    };
    if (this.patientToken) headers[PATIENT_TOKEN_HEADER] = this.patientToken;
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return parse<T>(response);
  }

  createCase(body: Schemas["CaseCreate"]): Promise<Case> {
    return this.request("POST", "/cases", body);
  }

  getCase(caseId: string): Promise<CaseDetail> {
    return this.request("GET", `/cases/${caseId}`);
  }

  recordConsent(caseId: string, body: Schemas["ConsentCreate"]): Promise<Schemas["Consent"]> {
    return this.request("POST", `/cases/${caseId}/consents`, body);
  }

  notifications(caseId: string): Promise<PatientNotification[]> {
    return this.request("GET", `/cases/${caseId}/notifications`);
  }

  /** The patient's bills on FinchNode (summaries: `itemized=false` needs the itemized bill). */
  finchnodeClaims(caseId: string): Promise<Schemas["FinchNodeClaims"]> {
    return this.request("GET", `/cases/${caseId}/finchnode/claims`);
  }

  /** The patient's own number for joining an insurer call that pauses for them. */
  setCallbackPhone(caseId: string, phone: string): Promise<Case> {
    return this.request("PUT", `/cases/${caseId}/callback-phone`, { phone });
  }

  /** The patient's spoken authorization, played to the insurer when the call connects. */
  putAuthorizationRecording(
    caseId: string,
    body: { audio_base64: string; mime_type: string },
  ): Promise<{ stored: boolean; size_bytes: number }> {
    return this.request("PUT", `/cases/${caseId}/authorization-recording`, body);
  }

  /** Start linking records through FinchNode Connect (409 connect_unavailable in mock/demo). */
  startFinchNodeConnect(body: Schemas["FinchNodeConnectCreate"]): Promise<Schemas["FinchNodeConnectSession"]> {
    return this.request("POST", "/finchnode/connect-sessions", body);
  }

  /** Read a Connect session back (finishes linking when the patient is done). */
  getFinchNodeConnect(sessionId: string): Promise<Schemas["FinchNodeConnectSession"]> {
    return this.request("GET", `/finchnode/connect-sessions/${sessionId}`);
  }

  createVisit(body: Schemas["VisitCreate"]): Promise<Schemas["Visit"]> {
    return this.request("POST", "/visits", body);
  }

  completeVisit(visitId: string, body: Schemas["VisitComplete"]): Promise<Schemas["VisitEvidenceResult"]> {
    return this.request("POST", `/visits/${visitId}/complete`, body);
  }

  ingestClaim(body: Schemas["ClaimIngest"]): Promise<Schemas["Claim"]> {
    return this.request("POST", "/claims", body);
  }

  analyzeClaim(claimId: string): Promise<Schemas["ClaimAnalysisResult"]> {
    return this.request("POST", `/claims/${claimId}/analyze`);
  }

  resolveIntervention(
    interventionId: string,
    body: Schemas["HumanInterventionResolve"],
  ): Promise<Schemas["HumanResolutionResult"]> {
    return this.request("POST", `/human-intervention/${interventionId}/resolve`, body);
  }

  prepareDispute(caseId: string): Promise<Schemas["DisputePreparation"]> {
    return this.request("POST", "/disputes", { case_id: caseId });
  }

  approveDispute(disputeId: string, body: Schemas["DisputeApproval"]): Promise<Dispute> {
    return this.request("POST", `/disputes/${disputeId}/approve`, body);
  }

  rejectDispute(disputeId: string, body: Schemas["DisputeRejection"]): Promise<Dispute> {
    return this.request("POST", `/disputes/${disputeId}/reject`, body);
  }

  startCall(caseId: string): Promise<Schemas["CallSession"]> {
    return this.request("POST", "/voice/start", { case_id: caseId });
  }

  /** Understand the patient's own words (Amyna's language layer lives in Core). */
  understandMessage(message: string): Promise<Schemas["AssistantUnderstandResult"]> {
    return this.request("POST", "/assistant/understand", { message });
  }

  explainTerm(term: string): Promise<Schemas["AssistantTermExplanation"]> {
    return this.request("POST", "/assistant/explain", { term });
  }

  /** Signed, idempotent event to Core's POST /relay/webhook (HMAC over "{ts}.{body}"). */
  async sendSignedEvent(event: Schemas["RelayWebhookEvent"]): Promise<Schemas["RelayWebhookResult"]> {
    const body = JSON.stringify(event);
    const timestamp = String(Math.floor(Date.now() / 1000));
    const response = await this.fetchImpl(`${this.baseUrl}/relay/webhook`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Amyna-Timestamp": timestamp,
        "X-Amyna-Signature": signCoreEvent(this.webhookSecret, timestamp, body),
      },
      body,
    });
    return parse(response);
  }
}

function parseJson(text: string): unknown {
  try {
    return text ? (JSON.parse(text) as unknown) : undefined;
  } catch {
    return undefined; // e.g. a proxy's HTML error page
  }
}

/** Status first: a non-JSON error page is still a CoreError with its real status. */
async function parse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const json = parseJson(text);
  if (!response.ok) {
    const raw = (json as { detail?: unknown } | undefined)?.detail;
    const detail = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    const code = typeof detail.code === "string" ? detail.code : "error";
    const message = typeof detail.message === "string" ? detail.message : text.slice(0, 300);
    throw new CoreError(response.status, code, message, detail);
  }
  if (text && json === undefined) {
    throw new CoreError(502, "bad_core_response", "Core answered with something that is not JSON");
  }
  return json as T;
}
