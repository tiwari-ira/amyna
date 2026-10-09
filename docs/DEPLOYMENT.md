# Deploying Amyna

This document describes Amyna's deployment environments, how configuration is
validated, and the production launch checklist. It is deliberately conservative:
if a value here conflicts with a vendor account, the vendor's current docs win.

## Environments

Amyna deploys four ways, in increasing strictness:

| Environment | Purpose | Data | Integrations |
|---|---|---|---|
| `local`   | Developer machine | synthetic fixtures | mock adapters permitted |
| `test`    | CI / unit tests    | synthetic fixtures | mock adapters only |
| `staging` | Pre-launch proof   | **synthetic patients only** | real sandbox FinchNode, test telephony, test Relay |
| `production` | Real patients   | real patient-authorized data | live adapters, fail-closed |

Secrets must never be committed. Production secrets come from a secrets manager
(1Password Connect, AWS/GCP/Azure secret store, etc.).

## How configuration is validated

`APP_ENV` is set with the other environment variables. `core/app/config.py`
enforces:

* `missing_live_settings()` — flipping any `*_MODE` to a live value without its
  keys is a startup error listing the missing variables.
* `environment_mistakes()` — in `staging` and `production` the process refuses to
  start if **any** of the following are true:
  * no PostgreSQL `DATABASE_URL` (no silent SQLite fallback);
  * `TELNYX_WEBHOOK_VERIFY` is not `true`, or `TELNYX_PUBLIC_KEY` is empty;
  * `PUBLIC_BASE_URL` is not `https://` (and, in production, not a tunnel/ngrok);
  * `NOTIFY_MODE`, `FETCH_MODE`, `XAI_VOICE_MODE`, `TELEPHONY_MODE` are not their
    live values (no silent mock adapters in staging/production);
  * `FINCHNODE_MODE` is not `sandbox` for staging or `production` for production;
  * any real service token is missing (no dev tokens outside dev/test).

## Staging first

Deploy staging before production. Use `.env.staging.example` as the template and
verify the full path with a synthetic test identity:

```
patient -> Relay -> Core -> Orchestrator -> Visit/Claim agents
        -> findings -> approval -> Dispute -> xAI Realtime
        -> Telnyx -> synthetic insurer -> beep, merge, hang up -> Relay
```

Promote to production only after the staging run is green end to end.

## Production architecture

Deploy as persistent, auto-restarting services — not a laptop or a tunnel:

```
Core (FastAPI) + Relay (Node) + Fetch agents + PostgreSQL
webhooks over https
secrets from a secrets manager
backups + monitoring + alerts
```

Health checks: `GET /health` returns `database` and the `modes` map. Alerts:
database connection failure, webhook signature failures, call failures, and
queue depth on async pipelines. See [docs/SECURITY.md](SECURITY.md) for the
privacy and webhook rules, and [.env.production.example](../.env.production.example)
for the full variable list.

## What deploys (persistent, auto-restarting)

`deploy/docker-compose.prod.yml` runs:

| Service | Role |
|---|---|
| `db` | PostgreSQL 16 (volume + health check; backups via the `backup` service) |
| `core` | Amyna Core API (the state owner; fails closed on missing live settings) |
| `relay` | Node Relay service (patient-facing channel) |
| `agents` | Fetch network: public `amyna-orchestrator` + internal specialists |
| `jobs` | Durable-job worker (`scripts/job_worker.py` -> `/internal/jobs/drain`) |
| `backup` | Daily `pg_dump` with rotation (see "Backups & restore") |

All secrets come from the environment / a secrets manager (never committed).

## Backups, encryption, least privilege (task 27)

- Backups: the `backup` service dumps daily to a volume (`./backup.sh`),
  rotating after `BACKUP_KEEP_DAYS` (default 14). **Restore testing**: restore a
  dump into a scratch database and run `make smoke` against it before trusting
  backups in an incident.
- Encryption at rest: enable on your managed Postgres/volume; the dump volume
  is PHI-adjacent storage and must inherit it.
- Least privilege: the DB user must not be a superuser in production;
  connection uses `DATABASE_URL` only, no public Postgres port.

## Observability (task 29)

`GET /internal/metrics` (scope `metrics:read`, the FETCH identity holds it)
exposes request counts, average latency, queue depth, active calls, open
interventions, and stuck cases. Point your monitor at it and alert on
`dead > 0` in `queue_depth` or any stuck-case growth. Every response carries
`X-Amyna-Request-Id` for tracing; logs are PHI-redacted (`core/app/audit.py`).

## Secrets rotation

If a secret may have been exposed (a synced `.env`, logs, chat history), rotate it
at the source: xAI API key, Telnyx keys + connection + public key, Relay tokens,
Agentverse API key, and every service token. Rotation never requires a code change.
