/**
 * Relay service configuration (reads the repo-root .env, never overrides real env).
 *
 * RELAY_MODE=mock -> in-memory outbox, no Relay account needed (default).
 * RELAY_MODE=live -> Relay platform via relayAdapter/live.ts (VERIFY endpoints).
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
/** Mirrors core/app/config.py dev defaults (used only when APP_ENV is dev/test). */
export const DEV_RELAY_SERVICE_TOKEN = "dev-relay-token";
export const DEV_CORE_WEBHOOK_SECRET = "dev-core-webhook-secret";

export type RelayMode = "mock" | "live";

export interface RelayConfig {
  mode: RelayMode;
  appEnv: string;
  port: number;
  coreBaseUrl: string;
  /** Bearer token for Core (and for Core -> /internal/notify). */
  serviceToken: string;
  /** HMAC secret for signed events to Core's POST /relay/webhook. */
  coreWebhookSecret: string;
  /** Relay platform credentials (live mode only). */
  relayAgentToken: string;
  relayWebhookSecret: string;
  relayApiBaseUrl: string;
  /** xAI speech-to-text for visit voice memos (live mode only). */
  xaiApiKey: string;
  xaiSttUrl: string;
  /** With live agents, how often and how long to check whether visit evidence is ready. */
  visitPollMs: number;
  visitWaitMs: number;
  dataDir: string;
}

export class ConfigError extends Error {}

/** Minimal KEY=VALUE .env parser (comments and blank lines ignored). */
export function loadDotEnv(path: string, env: NodeJS.ProcessEnv = process.env): void {
  if (!existsSync(path)) return;
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^["']|["']$/g, "");
    if (env[key] === undefined || env[key] === "") env[key] = value;
  }
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): RelayConfig {
  const get = (name: string, fallback = "") => (env[name] ?? "").trim() || fallback;
  const appEnv = get("APP_ENV", "dev");
  const devDefaults = appEnv === "dev" || appEnv === "test";
  const mode = get("RELAY_MODE", "mock");
  if (mode !== "mock" && mode !== "live") {
    throw new ConfigError(`RELAY_MODE must be mock or live, got "${mode}"`);
  }
  const config: RelayConfig = {
    mode,
    appEnv,
    port: Number(get("RELAY_PORT", "3000")),
    coreBaseUrl: get("CORE_BASE_URL", "http://localhost:8080"),
    serviceToken: get("RELAY_SERVICE_TOKEN", devDefaults ? DEV_RELAY_SERVICE_TOKEN : ""),
    coreWebhookSecret: get("CORE_WEBHOOK_SECRET", devDefaults ? DEV_CORE_WEBHOOK_SECRET : ""),
    relayAgentToken: get("RELAY_AGENT_TOKEN"),
    relayWebhookSecret: get("RELAY_WEBHOOK_SECRET"),
    relayApiBaseUrl: get("RELAY_API_BASE_URL"),
    xaiApiKey: get("XAI_API_KEY"),
    xaiSttUrl: get("XAI_STT_URL", "https://api.x.ai/v1/stt"),
    visitPollMs: Number(get("RELAY_VISIT_POLL_MS", "2000")),
    visitWaitMs: Number(get("RELAY_VISIT_WAIT_MS", "90000")),
    dataDir: get("RELAY_DATA_DIR", resolve(REPO_ROOT, "relay", "data")),
  };
  const missing: string[] = [];
  if (mode === "live") {
    if (!config.relayAgentToken) missing.push("RELAY_MODE=live requires RELAY_AGENT_TOKEN");
    if (!config.relayWebhookSecret) missing.push("RELAY_MODE=live requires RELAY_WEBHOOK_SECRET");
    if (!config.relayApiBaseUrl) missing.push("RELAY_MODE=live requires RELAY_API_BASE_URL");
    if (!config.xaiApiKey) missing.push("RELAY_MODE=live requires XAI_API_KEY (visit transcription)");
  }
  if (!config.serviceToken) missing.push("RELAY_SERVICE_TOKEN is required when APP_ENV=prod");
  if (!config.coreWebhookSecret) missing.push("CORE_WEBHOOK_SECRET is required when APP_ENV=prod");
  if (missing.length) {
    throw new ConfigError(
      `Amyna Relay cannot start:\n  - ${missing.join("\n  - ")}\n` +
        "Fill these in .env (see docs/SETUP_KEYS.md) or switch RELAY_MODE back to mock.",
    );
  }
  return config;
}
