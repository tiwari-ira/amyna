/**
 * Amyna Relay service.
 *   POST /webhook/relay   Relay platform events (signature-verified in live mode, idempotent)
 *   POST /internal/notify Core -> patient notifications (bearer RELAY_SERVICE_TOKEN)
 *   GET  /health
 */
import { resolve } from "node:path";

import { createApp, createContext } from "./app";
import { REPO_ROOT, loadConfig, loadDotEnv } from "./config";

loadDotEnv(resolve(REPO_ROOT, ".env"));
const config = loadConfig();
const server = createApp(createContext(config));
server.listen(config.port, () => {
  console.log(`[relay] listening on :${config.port} (RELAY_MODE=${config.mode}, core=${config.coreBaseUrl})`);
});
