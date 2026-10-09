/** Relay platform adapter interface. Everything platform-specific lives behind it. */

export interface Button {
  id: string;
  label: string;
}

export interface OutboundMessage {
  text: string;
  buttons?: Button[];
}

export interface RelayAdapter {
  readonly mode: "mock" | "live";
  /** True when recording is simulated: the visit "completes" at once with the synthetic demo
   * transcript. False when the patient sends a real voice memo of the appointment. */
  readonly simulatesRecording: boolean;
  /** Send a message (optionally with quick-reply buttons) to a Relay user. */
  sendMessage(to: string, message: OutboundMessage): Promise<{ id: string }>;
  /** Ask the platform to record the user's appointment. */
  startRecording(to: string): Promise<{ recordingId: string }>;
  /** Download recorded audio (a voice memo's signed URL). */
  fetchRecording(url: string): Promise<Uint8Array>;
  /** Map a tapped button's label back to its id (platforms that return only the label). */
  resolveButton?(from: string, text: string): string | undefined;
}
