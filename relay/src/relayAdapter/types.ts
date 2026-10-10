/** Relay platform adapter interface. Everything platform-specific lives behind it. */

export interface Button {
  id: string;
  label: string;
  /** A link button: opens this page in the app and sends nothing back (Relay URL button). */
  url?: string;
}

export interface OutboundMessage {
  text: string;
  buttons?: Button[];
}

export interface SharedLocation {
  latitude: number;
  longitude: number;
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
  /** Ask the patient to share their location in this chat. */
  requestLocation(chatId: string): Promise<"sent" | "already_sharing" | "unavailable">;
  /** Latest coordinates the patient is sharing, if any. */
  readLocation(chatId: string): Promise<SharedLocation | undefined>;
  /** Map a tapped button's label back to its id (platforms that return only the label). */
  resolveButton?(from: string, text: string): string | undefined;
}
