# Relay service (docker compose --profile relay up). Build context: repo root.
FROM node:20-alpine
WORKDIR /repo
COPY shared shared
COPY relay/package.json relay/package-lock.json relay/
RUN cd relay && npm ci --no-audit --no-fund
COPY relay relay
WORKDIR /repo/relay
EXPOSE 3000

# Liveness probe: GET /health is unauthenticated (app.ts).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

# node:20-alpine ships an unprivileged `node` user (uid 1000).
USER node
CMD ["npx", "tsx", "src/index.ts"]
