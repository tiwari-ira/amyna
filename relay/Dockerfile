# Relay service (docker compose --profile relay up). Build context: repo root.
FROM node:20-alpine
WORKDIR /repo
COPY shared shared
COPY relay/package.json relay/package-lock.json relay/
RUN cd relay && npm ci --no-audit --no-fund
COPY relay relay
WORKDIR /repo/relay
EXPOSE 3000
CMD ["npx", "tsx", "src/index.ts"]
