import { describe, expect, it, vi } from "vitest";

import { CoreClient, CoreError } from "../src/coreClient";
import { signCoreEvent } from "../src/webhook";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("CoreClient", () => {
  it("sends the bearer token and JSON body", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(201, { case_id: "case_1" }));
    const client = new CoreClient("http://core", "tok", "secret", fetchMock as unknown as typeof fetch);
    const created = await client.createCase({ patient: { finchnode_patient_id: "fn_pat_x" }, channel: "relay" });
    expect(created.case_id).toBe("case_1");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://core/cases");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(JSON.parse(init.body as string).channel).toBe("relay");
  });

  it("maps Core error bodies to CoreError", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(409, { detail: { code: "consent_required", message: "consent needed" } }),
    );
    const client = new CoreClient("http://core", "tok", "secret", fetchMock as unknown as typeof fetch);
    await expect(client.analyzeClaim("clm_1")).rejects.toMatchObject({ status: 409, code: "consent_required" });
    await expect(client.analyzeClaim("clm_1")).rejects.toBeInstanceOf(CoreError);
  });

  it("signs webhook events exactly like Core expects", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { status: "processed", event_id: "evt_1" }));
    const client = new CoreClient("http://core", "tok", "secret", fetchMock as unknown as typeof fetch);
    await client.sendSignedEvent({
      event_id: "evt_123456",
      event_type: "button_reply",
      case_id: "case_1",
      choice: "NO",
      occurred_at: "2026-09-16T00:00:00Z",
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe("http://core/relay/webhook");
    expect(headers["X-Amyna-Signature"]).toBe(
      signCoreEvent("secret", headers["X-Amyna-Timestamp"]!, init.body as string),
    );
  });
});
