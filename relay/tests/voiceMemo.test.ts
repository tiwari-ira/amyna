/** Visit recording via a Relay voice memo, transcribed with xAI speech-to-text. */
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import type { Schemas } from "../src/coreClient";
import { LiveTranscriber, TranscriptionError, type Transcriber, toSegments } from "../src/handlers/transcription";
import { parsePlatformEvent } from "../src/webhook";
import { setup } from "./support";

const STT = {
  text: "Hi, I'm Dr. Chen. We're doing an MRI with contrast.",
  words: [
    { text: "Hi,", start: 2.361, end: 2.541, speaker: 0 },
    { text: "I'm", start: 2.581, end: 2.681, speaker: 0 },
    { text: "Dr.", start: 2.741, end: 3.021, speaker: 0 },
    { text: "Chen.", start: 3.081, end: 3.361, speaker: 0 },
    { text: "We're", start: 3.582, end: 3.842, speaker: 1 },
    { text: "doing", start: 3.882, end: 4.002, speaker: 1 },
    { text: "an", start: 4.002, end: 4.182, speaker: 1 },
    { text: "MRI", start: 4.202, end: 4.5, speaker: 1 },
    { text: "with", start: 4.6, end: 4.8, speaker: 1 },
    { text: "contrast.", start: 4.9, end: 5.4, speaker: 1 },
  ],
};

const MEMO = {
  attachmentId: "att_1",
  url: "https://media.relayapp.im/signed/att_1",
  mimeType: "audio/mp4",
  sizeBytes: 175_000,
};

class FakeTranscriber implements Transcriber {
  refs: string[] = [];
  failures = 0;
  async transcribe(recordingRef: string): Promise<Schemas["VisitComplete"]> {
    this.refs.push(recordingRef);
    if (this.failures > 0) {
      this.failures -= 1;
      throw new TranscriptionError("no speech was detected");
    }
    return { language: "en", source: "relay_transcription", segments: toSegments(STT) };
  }
}

describe("toSegments", () => {
  it("groups consecutive words by diarized speaker", () => {
    expect(toSegments(STT)).toEqual([
      { speaker: "unknown", speaker_label: "Speaker 1", text: "Hi, I'm Dr. Chen.", start_ms: 2361, end_ms: 3361 },
      {
        speaker: "unknown",
        speaker_label: "Speaker 2",
        text: "We're doing an MRI with contrast.",
        start_ms: 3582,
        end_ms: 5400,
      },
    ]);
  });

  it("falls back to the full text when there are no word timings", () => {
    expect(toSegments({ text: " hello " })).toEqual([{ speaker: "unknown", text: "hello" }]);
    expect(toSegments({ text: "", words: [] })).toEqual([]);
  });
});

describe("LiveTranscriber", () => {
  const recording = () => {
    const path = join(mkdtempSync(join(tmpdir(), "memo-")), "visit.m4a");
    writeFileSync(path, "audio");
    return `file://${path}`;
  };

  it("posts the audio last with diarization and medical keyterms", async () => {
    let sent: { url: string; init: RequestInit } | undefined;
    const fakeFetch = (async (url: string, init: RequestInit) => {
      sent = { url, init };
      return new Response(JSON.stringify(STT), { status: 200 });
    }) as unknown as typeof fetch;

    const result = await new LiveTranscriber("xai-key", "https://stt.test", fakeFetch).transcribe(recording());

    expect(result.segments).toHaveLength(2);
    expect(sent?.url).toBe("https://stt.test");
    expect((sent?.init.headers as Record<string, string>).Authorization).toBe("Bearer xai-key");
    const form = sent?.init.body as FormData;
    expect(form.get("diarize")).toBe("true");
    expect(form.getAll("keyterm")).toContain("lorazepam");
    expect([...form.keys()].at(-1)).toBe("file");
    expect((form.get("file") as File).type).toBe("audio/mp4");
  });

  it("treats rejected audio and silence as unusable, server errors as retryable", async () => {
    const respond = (status: number, body: unknown = {}) =>
      (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
    await expect(new LiveTranscriber("k", "u", respond(400)).transcribe(recording())).rejects.toBeInstanceOf(
      TranscriptionError,
    );
    await expect(
      new LiveTranscriber("k", "u", respond(200, { text: "", words: [] })).transcribe(recording()),
    ).rejects.toBeInstanceOf(TranscriptionError);
    const serverError = new LiveTranscriber("k", "u", respond(503)).transcribe(recording());
    await expect(serverError).rejects.toThrow(/503/);
    await expect(serverError).rejects.not.toBeInstanceOf(TranscriptionError);
  });
});

describe("voice memo recording flow", () => {
  async function toRecordingStage(options: Parameters<typeof setup>[0]) {
    const harness = setup({ voiceMemos: true, dataDir: mkdtempSync(join(tmpdir(), "relay-")), ...options });
    await harness.send({ text: "hi" });
    await harness.send({ text: "fn_pat_demo_001" });
    await harness.send({ kind: "button", buttonId: "CONSENT_YES" });
    await harness.send({ kind: "button", buttonId: "RECORD_YES" });
    return harness;
  }

  it("asks for a memo, then transcribes it and hands the segments to Core", async () => {
    const transcriber = new FakeTranscriber();
    const { core, relay, ctx, send } = await toRecordingStage({ transcriber });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/voice memo/);
    expect(core.visits).toHaveLength(0);

    await send({ text: "what now?" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/voice memo/);

    await send({ audio: MEMO });
    expect(transcriber.refs[0]).toMatch(/^file:\/\/.*\.m4a$/);
    expect(core.visits[0]).toMatchObject({ recording_consent_id: "cns_recording", recording_ref: transcriber.refs[0] });
    expect(core.transcripts[0]?.segments[1]).toMatchObject({ speaker_label: "Speaker 2", text: /MRI with contrast/ });
    expect(relay.outbox.at(-2)?.message.text).toMatch(/Give me a moment/);
    expect(relay.outbox.at(-1)?.message.text).toMatch(/noted from your visit: lorazepam/);
    expect(ctx.store.get("patient").stage).toBe("ready_for_bill");
  });

  it("asks again without claiming the case's one visit when the audio is unusable", async () => {
    const transcriber = new FakeTranscriber();
    transcriber.failures = 1;
    const { core, relay, send } = await toRecordingStage({ transcriber });

    await send({ audio: MEMO });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/couldn't transcribe/);
    expect(core.visits).toHaveLength(0);

    await send({ audio: MEMO });
    expect(core.visits).toHaveLength(1);
    expect(relay.outbox.at(-1)?.message.text).toMatch(/noted from your visit/);
  });

  it("waits for live agents to record the evidence before offering the bill review", async () => {
    const { core, relay, ctx, send } = await toRecordingStage({ transcriber: new FakeTranscriber() });
    ctx.config.visitPollMs = 5;
    ctx.config.visitWaitMs = 1000;
    let visitStatus = "transcribed";
    core.completeVisit = async () => ({ status: "pending", evidence: [] }) as unknown as Schemas["VisitEvidenceResult"];
    core.getCase = async () =>
      ({
        case_id: "case_1",
        visit: { status: visitStatus },
        evidence: [
          { description: "MRI with contrast", negated: false },
          { description: "NOT anesthesia", negated: true },
        ],
      }) as unknown as Awaited<ReturnType<typeof core.getCase>>;

    await send({ audio: MEMO });
    expect(ctx.store.get("patient").stage).toBe("processing_visit");
    await send({ text: "hello?" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/still going through your visit/);

    visitStatus = "evidence_extracted";
    await vi.waitFor(() => expect(ctx.store.get("patient").stage).toBe("ready_for_bill"));
    const notes = relay.outbox.at(-1)!.message;
    expect(notes.text).toMatch(/noted from your visit: MRI with contrast and no anesthesia\./);
    expect(notes.buttons?.map((b) => b.label)).toEqual(["Review my bill"]);
  });

  it("lets the patient skip while waiting, and ignores memos at other steps", async () => {
    const { core, relay, ctx, send } = await toRecordingStage({});
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    expect(ctx.store.get("patient").stage).toBe("ready_for_bill");
    await send({ audio: MEMO });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/only use recordings/);
    expect(core.visits).toHaveLength(0);
  });

  it("parses a Relay voice memo envelope", () => {
    const event = parsePlatformEvent({
      event_type: "message.received",
      event_id: "evt_memo",
      data: {
        direction: "inbound",
        sender_handle: { handle: "ira" },
        parts: [{ type: "media", id: "att_1", url: MEMO.url, mime_type: "audio/mp4", size_bytes: 175000, duration_ms: null }],
      },
    });
    expect(event).toMatchObject({ kind: "message", from: "ira", audio: { attachmentId: "att_1", mimeType: "audio/mp4" } });
  });
});
