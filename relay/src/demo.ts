/**
 * Simulated patient conversation through the real handlers (mock Relay adapter)
 * against a running Core:   make dev-local   then   make relay-demo
 */
import { resolve } from "node:path";

import { createContext } from "./app";
import { REPO_ROOT, loadConfig, loadDotEnv } from "./config";
import { newEventId } from "./handlers/approvalFlow";
import { handleIncoming } from "./handlers/incomingMessage";
import { MockRelayAdapter } from "./relayAdapter/mock";
import { ConversationStore } from "./store";
import type { InboundEvent } from "./webhook";

const USER = "relay-demo-patient";

type Step = { text: string } | { button: string } | { tap: string };

const SCRIPT: Step[] = [
  { text: "hi" },
  { text: "fn_pat_demo_001" },
  { button: "CONSENT_YES" },
  { button: "RECORD_YES" },
  { button: "REVIEW_BILL" },
  { text: "CLM-2026-0915-001" },
  { tap: "NO" },
  { tap: "NOT_SURE" },
  { tap: "APPROVE" },
  { tap: "DONE" },
];

async function main(): Promise<void> {
  loadDotEnv(resolve(REPO_ROOT, ".env"));
  const relay = new MockRelayAdapter(true);
  const ctx = createContext(loadConfig({ ...process.env, RELAY_MODE: "mock" }), {
    relay,
    store: new ConversationStore(),
  });
  for (const step of SCRIPT) {
    const event: InboundEvent = { id: newEventId(), kind: "message", from: USER };
    if ("text" in step) {
      event.text = step.text;
      console.log(`\nyou> ${step.text}`);
    } else if ("button" in step) {
      Object.assign(event, { kind: "button", buttonId: step.button });
      console.log(`\nyou> [${step.button}]`);
    } else {
      const lastCard = [...relay.outbox].reverse().find((m) => m.message.buttons?.some((b) => b.id.endsWith(`:${step.tap}`)));
      const button = lastCard?.message.buttons?.find((b) => b.id.endsWith(`:${step.tap}`));
      if (!button) throw new Error(`no card offers ${step.tap}`);
      Object.assign(event, { kind: "button", buttonId: button.id });
      console.log(`\nyou> [${button.label}]`);
    }
    await handleIncoming(ctx, event);
  }
}

main().catch((error) => {
  console.error(`\nrelay demo failed: ${(error as Error).message}`);
  console.error("Is Core running? Start it with `make dev-local`.");
  process.exit(1);
});
