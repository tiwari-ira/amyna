import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { ConfigError, DEV_RELAY_SERVICE_TOKEN, loadConfig, loadDotEnv } from "../src/config";

describe("loadConfig", () => {
  it("defaults to mock mode with dev credentials", () => {
    const config = loadConfig({});
    expect(config.mode).toBe("mock");
    expect(config.serviceToken).toBe(DEV_RELAY_SERVICE_TOKEN);
    expect(config.coreBaseUrl).toBe("http://localhost:8080");
  });

  it("fails fast naming every missing variable for live mode", () => {
    expect(() => loadConfig({ RELAY_MODE: "live" })).toThrow(ConfigError);
    try {
      loadConfig({ RELAY_MODE: "live" });
    } catch (error) {
      expect((error as Error).message).toMatch(/RELAY_AGENT_TOKEN/);
      expect((error as Error).message).toMatch(/RELAY_WEBHOOK_SECRET/);
    }
  });

  it("requires real tokens in prod", () => {
    expect(() => loadConfig({ APP_ENV: "prod" })).toThrow(/RELAY_SERVICE_TOKEN/);
  });

  it("loads .env without overriding real environment values", () => {
    const path = join(mkdtempSync(join(tmpdir(), "env-")), ".env");
    writeFileSync(path, "# comment\nRELAY_PORT=4000\nCORE_BASE_URL=http://from-file\n");
    const env: NodeJS.ProcessEnv = { CORE_BASE_URL: "http://from-env" };
    loadDotEnv(path, env);
    expect(env.RELAY_PORT).toBe("4000");
    expect(env.CORE_BASE_URL).toBe("http://from-env");
  });
});
