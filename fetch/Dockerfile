# Fetch agents (docker compose --profile agents up). Needs *_SEED in .env.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /repo

COPY shared/python shared/python
COPY pyproject.toml ./
COPY intelligence intelligence
COPY finchnode finchnode
COPY fetch fetch
# The jobs service runs scripts/job_worker.py from this image.
COPY scripts scripts

RUN pip install --no-cache-dir -e shared/python -e . -r fetch/requirements.txt

# Non-root runtime user. uAgents may write its per-agent storage next to the
# working directory, so /repo is chowned and a writable HOME is provided.
RUN useradd --system --uid 10001 --create-home --home-dir /home/amyna amyna \
    && chown -R amyna:amyna /repo /home/amyna
ENV HOME=/home/amyna
EXPOSE 8000

# Liveness probe: the orchestrator's HTTP API (REST /trigger, mailbox) on :8000.
# TCP connect only -- the agents' HTTP surface is uAgents' own, not ours.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import socket, sys; s = socket.socket(); s.settimeout(3); sys.exit(0 if s.connect_ex(('127.0.0.1', 8000)) == 0 else 1)"]

USER amyna
CMD ["python", "-m", "fetch.agents.run_all"]
