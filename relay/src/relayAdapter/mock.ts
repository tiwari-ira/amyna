/** RELAY_MODE=mock: in-memory outbox + console output. No Relay account needed. */
import type { OutboundMessage, RelayAdapter } from "./types";

export interface SentMessage {
  to: string;
  message: OutboundMessage;
}

export class MockRelayAdapter implements RelayAdapter {
  readonly mode = "mock" as const;
  readonly outbox: SentMessage[] = [];
  private counter = 0;

  constructor(
    private readonly print = false,
    readonly simulatesRecording = true,
  ) {}

  async sendMessage(to: string, message: OutboundMessage): Promise<{ id: string }> {
    this.outbox.push({ to, message });
    if (this.print) {
      const buttons = message.buttons?.map((b) => `[${b.label}]`).join(" ") ?? "";
      console.log(`\nrelay -> ${to}:\n${message.text}${buttons ? `\n${buttons}` : ""}`);
    }
    this.counter += 1;
    return { id: `mock-msg-${this.counter}` };
  }

  async startRecording(): Promise<{ recordingId: string }> {
    this.counter += 1;
    return { recordingId: `mock-rec-${this.counter}` };
  }

  async fetchRecording(): Promise<Uint8Array> {
    return new Uint8Array();
  }
}
