"""Agent settings from the environment (and the repo-root .env).

Seeds come ONLY from env: a missing seed fails loudly and seeds are never logged.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env", override=False)

DEV_FETCH_TOKEN = "dev-fetch-token"  # mirrors core/app/config.py DEV_SERVICE_TOKENS
# Points uAgents' Agentverse/Almanac calls at a closed local port so offline runs
# (tests, local Bureau) fail those calls instantly instead of reaching the internet.
OFFLINE_AGENTVERSE = "http://127.0.0.1:9"
SEED_HELP = 'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'


class MissingSeedError(RuntimeError):
    """An agent was started without its *_SEED environment variable."""


def require_seed(variable: str) -> str:
    """Return the seed from env or fail loudly (never log the value)."""
    value = os.environ.get(variable, "").strip()
    if not value:
        raise MissingSeedError(
            f"{variable} is not set. Agent seeds come only from the environment (.env). {SEED_HELP}"
        )
    return value


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class AgentSettings:
    app_env: str
    core_base_url: str
    fetch_token: str
    hello_world: bool
    request_timeout_s: float
    extraction_mode: str
    ollama_url: str
    ollama_model: str
    confirmation_threshold: float
    visit_agent_address: str | None
    claim_agent_address: str | None
    dispute_agent_address: str | None
    orchestrator_address: str | None
    orchestrator_handle: str | None

    @classmethod
    def from_env(cls) -> "AgentSettings":
        app_env = _env("APP_ENV", "dev")
        token = _env("FETCH_SERVICE_TOKEN")
        if not token and app_env in ("dev", "test"):
            token = DEV_FETCH_TOKEN
        if not token:
            raise RuntimeError("FETCH_SERVICE_TOKEN is required when APP_ENV=prod")
        return cls(
            app_env=app_env,
            core_base_url=_env("CORE_BASE_URL", "http://localhost:8080"),
            fetch_token=token,
            hello_world=_env("HELLO_WORLD", "0") in ("1", "true", "yes"),
            request_timeout_s=float(_env("AGENT_REQUEST_TIMEOUT_S", "60")),
            extraction_mode=_env("EXTRACTION_MODE", "rules"),
            ollama_url=_env("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=_env("OLLAMA_MODEL", "qwen3:1.7b"),
            confirmation_threshold=float(_env("CONFIRMATION_THRESHOLD", "0.9")),
            visit_agent_address=_env("VISIT_AGENT_ADDRESS") or None,
            claim_agent_address=_env("CLAIM_AGENT_ADDRESS") or None,
            dispute_agent_address=_env("DISPUTE_AGENT_ADDRESS") or None,
            orchestrator_address=_env("ORCHESTRATOR_ADDRESS") or None,
            orchestrator_handle=_env("ORCHESTRATOR_HANDLE") or None,
        )
