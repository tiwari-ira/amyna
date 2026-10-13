/**
 * The patient's spoken authorization memo, played to the insurer when the call connects.
 *
 * The platform's audio URL is short-lived and signed, so the memo is downloaded as soon
 * as it arrives and kept in relay/data (git-ignored) until a case exists. Then it is
 * uploaded to Core (which converts it for the phone) and the local copy is deleted.
 * Failures never block the conversation: without a memo, the call still happens and the
 * AI says the member authorized it.
 */
import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { CoreError } from "../coreClient";
import type { Conversation } from "../store";
import type { HandlerContext } from "./context";

/** Keep the memo. True when there is audio to play on the call. */
export async function keepAuthorizationMemo(
  ctx: HandlerContext,
  convo: Conversation,
  url: string,
  mimeType: string,
): Promise<boolean> {
  let audio: Uint8Array;
  try {
    audio = await ctx.relay.fetchRecording(url);
  } catch (error) {
    ctx.log(`authorization memo download failed (${(error as Error).name})`); // never the URL
    return false;
  }
  if (!audio.byteLength) return false;
  const directory = join(ctx.config.dataDir, "authorization");
  mkdirSync(directory, { recursive: true });
  const path = join(directory, `${randomUUID()}.audio`);
  writeFileSync(path, audio);
  convo.authorizationMemo = { path, mimeType: mimeType || "audio/mp4" };
  ctx.log(`authorization memo kept (${audio.byteLength} bytes)`);
  await uploadAuthorizationMemo(ctx, convo);
  return true;
}

/** Send the kept memo to Core once the case exists (idempotent; safe to call often). */
export async function uploadAuthorizationMemo(ctx: HandlerContext, convo: Conversation): Promise<void> {
  const memo = convo.authorizationMemo;
  if (!memo || memo.uploaded || !convo.caseId) return;
  try {
    const audio = readFileSync(memo.path);
    await ctx.core.putAuthorizationRecording(convo.caseId, {
      audio_base64: audio.toString("base64"),
      mime_type: memo.mimeType,
    });
  } catch (error) {
    const reason = error instanceof CoreError ? error.code : (error as Error).name;
    ctx.log(`authorization memo upload failed (${reason})`);
    return;
  }
  memo.uploaded = true;
  rmSync(memo.path, { force: true }); // PHI: Core has it now
  ctx.store.save();
}
