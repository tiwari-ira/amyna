/**
 * RELAY_MODE=live: Relay platform over HTTPS (https://docs.relayapp.im).
 *
 * Messages go to `POST /v1/messages` addressed by the person's handle. A Relay button
 * tap comes back as the label text, so the adapter remembers the last buttons sent to
 * each person and maps the label back to its id (`resolveButton`).
 *
 * Relay has no recording API (SDK checked 2026-10-04). The patient records the
 * appointment as a voice memo in the chat; it arrives as an audio media part with a
 * signed download URL (valid 60 minutes).
 */
import { randomUUID } from "node:crypto";

import type { RelayConfig } from "../config";
import { BUTTONS } from "../ux/messages";
import type { Button, OutboundMessage, RelayAdapter } from "./types";

const labelKey = (label: string) => label.trim().toLowerCase();

export class LiveRelayAdapter implements RelayAdapter {
  readonly mode = "live" as const;
  readonly simulatesRecording = false;
  private readonly lastButtons = new Map<string, Button[]>();

  constructor(
    private readonly config: RelayConfig,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  private async call<T>(path: string, body: unknown, idempotencyKey?: string): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.config.relayAgentToken}`,
      "Content-Type": "application/json",
    };
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const response = await this.fetchImpl(`${this.config.relayApiBaseUrl}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`Relay API ${path} failed: ${response.status}`);
    return (await response.json()) as T;
  }

  async sendMessage(to: string, message: OutboundMessage): Promise<{ id: string }> {
    const buttons = message.buttons ?? [];
    const parts: unknown[] = [{ type: "text", value: message.text }];
    if (buttons.length) {
      parts.push({ type: "buttons", items: buttons.slice(0, 5).map((b) => ({ label: b.label })) });
    }
    const sent = await this.call<{ message: { id: string } }>(
      "/v1/messages",
      { to: [to], message: { parts } },
      randomUUID(),
    );
    if (buttons.length) this.lastButtons.set(to, buttons);
    return { id: sent.message.id };
  }

  /** Falls back to the static buttons so a tap still resolves after a service restart. */
  resolveButton(from: string, text: string): string | undefined {
    const key = labelKey(text);
    const sent = this.lastButtons.get(from) ?? [];
    return [...sent, ...Object.values(BUTTONS)].find((b) => labelKey(b.label) === key)?.id;
  }

  async startRecording(): Promise<{ recordingId: string }> {
    return { recordingId: `simulated-${randomUUID()}` };
  }

  /** The signed URL is itself the credential; Relay says not to send the Agent Token with it. */
  async fetchRecording(url: string): Promise<Uint8Array> {
    const response = await this.fetchImpl(url);
    if (!response.ok) throw new Error(`recording download failed: ${response.status}`);
    return new Uint8Array(await response.arrayBuffer());
  }
}
