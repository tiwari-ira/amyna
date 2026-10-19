import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { ConversationStore } from "../src/store";

describe("ConversationStore", () => {
  it("get() creates a fresh conversation for an unknown sender", () => {
    const store = new ConversationStore();
    expect(store.get("ira")).toEqual({ userId: "ira", stage: "new", delivered: {} });
  });

  it("peek() never creates: unknown senders stay unknown", () => {
    const store = new ConversationStore();
    expect(store.peek("ira")).toBeUndefined();
    store.get("ira");
    expect(store.peek("ira")?.stage).toBe("new");
  });

  it("peek() returns the live conversation, including after reset", () => {
    const store = new ConversationStore();
    const convo = store.get("ira");
    convo.stage = "in_review";
    expect(store.peek("ira")?.stage).toBe("in_review");
    store.reset("ira");
    expect(store.peek("ira")?.stage).toBe("new");
  });

  it("persists changed conversations to an append-only journal, last record wins", () => {
    const path = join(mkdtempSync(join(tmpdir(), "store-")), "conversations.jsonl");
    const store = new ConversationStore(path);
    const convo = store.get("ira");
    convo.stage = "in_review";
    store.save();
    convo.stage = "done";
    store.save();
    // Two appends: one per changed version.
    expect(readFileSync(path, "utf8").trim().split("\n").length).toBe(2);
    const reloaded = new ConversationStore(path);
    expect(reloaded.peek("ira")?.stage).toBe("done");
  });

  it("a save with no changes appends nothing", () => {
    const path = join(mkdtempSync(join(tmpdir(), "store-")), "conversations.jsonl");
    const store = new ConversationStore(path);
    store.get("ira").stage = "in_review";
    store.save();
    const sizeAfterFirst = readFileSync(path, "utf8").length;
    store.save(); // nothing changed
    expect(readFileSync(path, "utf8").length).toBe(sizeAfterFirst);
  });

  it("imports the legacy whole-array JSON format on first read", () => {
    const path = join(mkdtempSync(join(tmpdir(), "store-")), "conversations.json");
    writeFileSync(
      path,
      JSON.stringify([{ userId: "legacy_user", stage: "done", delivered: {} }]),
    );
    const store = new ConversationStore(path);
    expect(store.peek("legacy_user")?.stage).toBe("done");
  });
});