# Build context: repo root (docker-compose.yml). Core imports the shared
# contracts and the libraries (intelligence, finchnode, fetch agent logic).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /repo

COPY shared/python shared/python
COPY pyproject.toml ./
COPY intelligence intelligence
COPY finchnode finchnode
COPY fetch fetch
COPY core core

RUN pip install --no-cache-dir -e shared/python -e . -e "core[postgres]"

WORKDIR /repo/core
EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8080"]
