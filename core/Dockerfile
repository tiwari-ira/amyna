# Build context: repo root (docker-compose.yml). Core imports the shared
# contracts and the libraries (intelligence, finchnode, fetch agent logic).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# ffmpeg converts the patient's voice-memo authorization (AAC/M4A) to MP3,
# the format Telnyx can play into the insurer call.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /repo

COPY shared/python shared/python
COPY pyproject.toml ./
COPY intelligence intelligence
COPY finchnode finchnode
COPY fetch fetch
COPY core core

RUN pip install --no-cache-dir -e shared/python -e . -e "core[postgres]"

# Non-root runtime user; the app never needs to write outside /repo/core data.
RUN useradd --system --uid 10001 --no-create-home amyna
WORKDIR /repo/core
EXPOSE 8080

# Liveness probe: /health is unauthenticated and reports DB reachability.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200 else 1)"]

USER amyna
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8080"]
