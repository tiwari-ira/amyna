# Fetch agents (docker compose --profile agents up). Needs *_SEED in .env.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /repo

COPY shared/python shared/python
COPY pyproject.toml ./
COPY intelligence intelligence
COPY finchnode finchnode
COPY fetch fetch

RUN pip install --no-cache-dir -e shared/python -e . -r fetch/requirements.txt

EXPOSE 8000
CMD ["python", "-m", "fetch.agents.run_all"]
