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

export class CoreError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(`${status} ${code}: ${message}`);
  }
}

type FetchLike = typeof fetch;

// Forwarded on every patient-bound call; Core resolves it to the owning account.
export const PATIENT_TOKEN_HEADER = "X-Amyna-Patient-Token";

export class CoreClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
    private readonly webhookSecret: string,
    private readonly fetchImpl: FetchLike = fetch,
    private readonly patientToken: string | null = null,
  ) {}

  /** A client that forwards the patient's session token (identity binding, task 19). */
  withPatientToken(patientToken: string): CoreApi {
    return new CoreClient(this.baseUrl, this.token, this.webhookSecret, this.fetchImpl, patientToken);
  }

  /** Dev/test/staging demo session for a relay user. Disabled in production. */
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

async function parse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const json = text ? (JSON.parse(text) as unknown) : undefined;
  if (!response.ok) {
    const detail = (json as { detail?: { code?: string; message?: string } } | undefined)?.detail;
    throw new CoreError(response.status, detail?.code ?? "error", detail?.message ?? text.slice(0, 300));
  }
  return json as T;
}
