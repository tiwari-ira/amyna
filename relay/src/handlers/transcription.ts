/**
 * Visit transcription (Member 2). Relay has no transcription API, so the live
 * transcriber calls xAI speech-to-text directly; Core only ever sees the result,
 * a VisitComplete body (speaker-labelled segments). Mock returns the synthetic
 * demo transcript.
 */
import { readFileSync } from "node:fs";
import { basename, extname, resolve } from "node:path";

import { REPO_ROOT } from "../config";
import type { Schemas } from "../coreClient";

export interface Transcriber {
  transcribe(recordingRef: string): Promise<Schemas["VisitComplete"]>;
}

/** The recording itself is unusable (bad audio, no speech). Retrying the same audio won't help. */
export class TranscriptionError extends Error {}

const DEMO_SCENARIO = resolve(
  REPO_ROOT,
  "shared/python/claimshield_contracts/fixtures/demo_scenario.json",
);

export class MockTranscriber implements Transcriber {
  async transcribe(): Promise<Schemas["VisitComplete"]> {
    const scenario = JSON.parse(readFileSync(DEMO_SCENARIO, "utf8")) as {
      transcript: Schemas["VisitComplete"];
    };
    return scenario.transcript;
  }
}

/** Biases recognition toward the services the Visit Agent extracts and bills are coded for. */
export const MEDICAL_KEYTERMS = [
  "MRI",
  "CT scan",
  "X-ray",
  "ultrasound",
  "contrast",
  "gadolinium",
  "lorazepam",
  "Ativan",
  "midazolam",
  "sedation",
  "anesthesia",
  "anesthesiologist",
  "IV",
  "milligrams",
  "lab work",
  "blood draw",
];

const AUDIO_TYPES: Record<string, string> = {
  ".m4a": "audio/mp4",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".ogg": "audio/ogg",
  ".webm": "audio/webm",
};

export interface SttWord {
  text: string;
  start?: number;
  end?: number;
  speaker?: number | null;
}

export interface SttResponse {
  text?: string;
  words?: SttWord[];
}

const toMs = (seconds: number | undefined) =>
  seconds === undefined ? null : Math.round(seconds * 1000);

/** Consecutive words from the same diarized speaker become one segment. Diarization numbers
 * voices but doesn't know roles, so speakers stay "unknown" with a "Speaker N" label. */
export function toSegments(response: SttResponse): Schemas["TranscriptSegmentIn"][] {
  const segments: Schemas["TranscriptSegmentIn"][] = [];
  let current: { speaker: number | null; words: SttWord[] } | null = null;
  const flush = () => {
    if (!current) return;
    const text = current.words.map((w) => w.text).join(" ").replace(/\s+([.,!?;:])/g, "$1").trim();
    if (text) {
      segments.push({
        speaker: "unknown",
        speaker_label: current.speaker === null ? null : `Speaker ${current.speaker + 1}`,
        text,
        start_ms: toMs(current.words[0]?.start),
        end_ms: toMs(current.words.at(-1)?.end),
      });
    }
  };
  for (const word of response.words ?? []) {
    const speaker = word.speaker ?? null;
    if (!current || current.speaker !== speaker) {
      flush();
      current = { speaker, words: [] };
    }
    current.words.push(word);
  }
  flush();
  if (!segments.length && response.text?.trim()) {
    segments.push({ speaker: "unknown", text: response.text.trim() });
  }
  return segments;
}

export class LiveTranscriber implements Transcriber {
  constructor(
    private readonly apiKey: string,
    private readonly sttUrl = "https://api.x.ai/v1/stt",
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async transcribe(recordingRef: string): Promise<Schemas["VisitComplete"]> {
    if (!recordingRef.startsWith("file://")) {
      throw new Error(`live transcription needs a stored recording, got ${recordingRef}`);
    }
    const path = recordingRef.slice("file://".length);
    const type = AUDIO_TYPES[extname(path).toLowerCase()] ?? "application/octet-stream";

    const form = new FormData();
    form.append("language", "en");
    form.append("format", "true");
    form.append("diarize", "true");
    for (const term of MEDICAL_KEYTERMS) form.append("keyterm", term);
    // xAI requires the file to be the last field.
    form.append("file", new Blob([readFileSync(path)], { type }), basename(path));

    const response = await this.fetchImpl(this.sttUrl, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.apiKey}` },
      body: form,
    });
    if (response.status === 401 || response.status === 403) {
      // Our key, not the patient's audio: don't ask them to re-record.
      throw new Error(`speech-to-text is not configured (XAI_API_KEY rejected: ${response.status})`);
    }
    if (response.status >= 400 && response.status < 500 && response.status !== 429) {
      throw new TranscriptionError(`speech-to-text rejected the audio (${response.status})`);
    }
    if (!response.ok) throw new Error(`speech-to-text failed: ${response.status}`);

    const segments = toSegments((await response.json()) as SttResponse);
    if (!segments.length) throw new TranscriptionError("no speech was detected");
    return { language: "en", source: "relay_transcription", segments };
  }
}
