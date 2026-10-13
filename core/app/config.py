"""Core settings. One ``*_MODE`` flag per integration; live modes fail fast.

Values come from the environment and the repo-root ``.env`` (see .env.example).
Empty values are ignored, so a blank ``.env`` means "all defaults, all mock".
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from claimshield_contracts.enums import ServiceName

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = REPO_ROOT / ".data" / "amyna.db"

# Well-known development credentials. Used only when APP_ENV is dev/test and
# the real variable is empty. Mirrored in relay/src/config.ts and fetch/agents/settings.py.
DEV_SERVICE_TOKENS: dict[ServiceName, str] = {
    ServiceName.RELAY: "dev-relay-token",
    ServiceName.FETCH: "dev-fetch-token",
    ServiceName.VOICE: "dev-voice-token",
}
DEV_CORE_WEBHOOK_SECRET = "dev-core-webhook-secret"


class ConfigError(RuntimeError):
    """Raised at startup when a live integration is missing required settings."""


class Settings(BaseSettings):
    """All Core configuration. Field names map 1:1 to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_whitespace(cls, value: object) -> object:
        # .env edits easily leave stray spaces (e.g. XAI_VOICE_MODEL=... trailing space).
        return value.strip() if isinstance(value, str) else value

    # --- core
    app_env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    database_url: str = ""
    core_base_url: str = "http://localhost:8080"
    relay_service_token: str = ""
    fetch_service_token: str = ""
    voice_service_token: str = ""
    core_webhook_secret: str = ""
    confirmation_threshold: float = 0.9
    notify_mode: Literal["outbox", "relay_service"] = "outbox"
    relay_service_url: str = ""

    # --- voice + telephony
    xai_voice_mode: Literal["mock", "live"] = "mock"
    xai_api_key: str = ""
    xai_realtime_url: str = "wss://api.x.ai/v1/realtime"
    xai_voice_model: str = ""
    xai_voice_name: str = ""
    telephony_mode: Literal["mock", "live"] = "mock"
    # The signing secret xAI returns when the SIP phone number is registered
    # (docs: speech-to-speech/sip). Required when live voice bridges calls.
    xai_webhook_signing_secret: str = ""
    telnyx_api_key: str = ""
    telnyx_connection_id: str = ""
    telnyx_phone_number: str = ""
    telnyx_public_key: str = ""
    # false => accept Telnyx webhooks without an Ed25519 signature (demo only,
    # when the Mission Control public key is unavailable). Keep true in prod.
    telnyx_webhook_verify: bool = True
    public_base_url: str = ""
    xai_sip_uri: str = ""
    # Both demo insurers: the original synthetic line (+15555550100) and the
    # demo roster's current payer line from finchnode/fixtures + contracts
    # demo_scenario.json (+12487208903). Comma-separated E.164 list.
    telephony_allowed_numbers: str = "+15555550100,+12487208903"
    # Insurer phone directory used when the records carry no payer phone (FinchNode
    # coverage never does): "Payer Name=+1...;Other Payer=+1...". Numbers must
    # still be in TELEPHONY_ALLOWED_NUMBERS.
    payer_phones: str = ""
    # Human merge: a handoff (SSN/PIN request...) dials this phone and
    # bridges the human into the call. Must also be in TELEPHONY_ALLOWED_NUMBERS.
    human_handoff_phone: str = ""
    # true => the human merge may dial the case's own patient-confirmed callback number
    # even when it is not in TELEPHONY_ALLOWED_NUMBERS (one number per patient instead
    # of a single team phone). false => that number must be allowlisted too.
    telephony_allow_patient_phones: bool = False
    # Safe window for the patient to answer the merge call. If they have not
    # connected by then, the merge escalates to MERGE_FAILED: the patient is
    # texted, the insurer stays on the line, and the AI tells the representative
    # the member will verify later (it never provides the information itself).
    human_merge_timeout_s: int = 75
    # Voicemail detection on the insurer call: a machine answer hangs up before the
    # authorization recording plays. Automated phone menus can also be classified
    # as a machine, so turn this off when dialing a real insurer IVR.
    telephony_voicemail_detection: bool = True
    # Fallback Telnyx accounts, used in order when the primary is rejected
    # (auth/credit). Each must be a complete key + connection + number triplet.
    telnyx_api_key_2: str = ""
    telnyx_connection_id_2: str = ""
    telnyx_phone_number_2: str = ""
    telnyx_api_key_3: str = ""
    telnyx_connection_id_3: str = ""
    telnyx_phone_number_3: str = ""

    # --- fetch
    fetch_mode: Literal["local_direct", "agentverse"] = "local_direct"
    orchestrator_url: str = "http://localhost:8000"

    # --- finchnode + intelligence
    # mock => local fixtures. demo => FinchNode's public keyless fake demo API.
    # sandbox => FinchNode sandbox (ck_test_ key). production => the real
    # patient-authorized records API (requires an enabled FinchNode application).
    finchnode_mode: Literal["mock", "demo", "sandbox", "production"] = "mock"
    finchnode_api_key: str = ""
    finchnode_base_url: str = ""
    # Signing secret (whsec_...) of the FinchNode application's webhook (console ->
    # Applications -> Webhook signatures). Needed for POST /finchnode/webhook.
    finchnode_webhook_secret: str = ""
    # What a Connect session asks the patient to share (must be on the FinchNode app's list).
    finchnode_connect_categories: str = "demographics,encounters,labs,medications,claims,documents"
    # one-time | continuous ("" = the FinchNode application's own setting). Continuous
    # shares send records.updated when the hospital adds new records.
    finchnode_sync_mode: Literal["", "one-time", "continuous"] = ""
    # Where FinchNode's "Return to Amyna" button sends the patient (optional).
    finchnode_connect_return_url: str = ""
    extraction_mode: Literal["rules", "ollama"] = "rules"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"

    # ------------------------------------------------------------------ derived

    @property
    def resolved_database_url(self) -> str:
        """DATABASE_URL, or a local SQLite file when it is empty."""
        return self.database_url or f"sqlite:///{DEFAULT_SQLITE_PATH}"

    @property
    def allows_dev_defaults(self) -> bool:
        return self.app_env in ("dev", "test")

    def service_tokens(self) -> dict[ServiceName, str]:
        """Bearer token per caller, with dev fallbacks outside prod."""
        configured = {
            ServiceName.RELAY: self.relay_service_token,
            ServiceName.FETCH: self.fetch_service_token,
            ServiceName.VOICE: self.voice_service_token,
        }
        if self.allows_dev_defaults:
            return {name: value or DEV_SERVICE_TOKENS[name] for name, value in configured.items()}
        return configured

    def webhook_secret(self) -> str:
        """HMAC secret for POST /relay/webhook."""
        if self.core_webhook_secret:
            return self.core_webhook_secret
        return DEV_CORE_WEBHOOK_SECRET if self.allows_dev_defaults else ""

    def allowed_numbers(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.telephony_allowed_numbers.split(",") if n.strip())

    def connect_categories(self) -> list[str]:
        return [c.strip() for c in self.finchnode_connect_categories.split(",") if c.strip()]

    def payer_directory(self) -> list[tuple[str, str]]:
        """``PAYER_PHONES`` as (payer name, E.164) pairs. Names are not hard-coded."""
        entries: list[tuple[str, str]] = []
        for entry in self.payer_phones.replace("\n", ";").split(";"):
            name, sep, phone = entry.rpartition("=")
            if sep and name.strip() and phone.strip():
                entries.append((name.strip(), phone.strip()))
        return entries

    def payer_phone_for(self, payer_name: str | None) -> str | None:
        """Phone for a payer name, from ``PAYER_PHONES``.

        Exact name, then a name contained in the other, then the only number in
        the directory. FinchNode coverage has no phone, so a deployment with one
        line (the number it is allowed to dial) can call whatever payer the
        records name. Nothing here is tied to one claim or one insurer.
        """
        entries = self.payer_directory()
        if not entries or not payer_name:
            return None
        wanted = payer_name.strip().lower()
        for name, phone in entries:
            if name.lower() == wanted:
                return phone
        for name, phone in entries:
            key = name.lower()
            if len(key) >= 4 and len(wanted) >= 4 and (key in wanted or wanted in key):
                return phone
        phones = {phone for _, phone in entries}
        if len(phones) == 1:
            return next(iter(phones))
        return None

    def telnyx_fallback_profiles(self) -> tuple[tuple[str, str, str], ...]:
        """Complete (API key, connection id, caller number) fallback triplets."""
        profiles: list[tuple[str, str, str]] = []
        for key, connection_id, phone in (
            (self.telnyx_api_key_2, self.telnyx_connection_id_2, self.telnyx_phone_number_2),
            (self.telnyx_api_key_3, self.telnyx_connection_id_3, self.telnyx_phone_number_3),
        ):
            if key and connection_id and phone:
                profiles.append((key, connection_id, phone))
        return tuple(profiles)

    def modes(self) -> dict[str, str]:
        return {
            "FINCHNODE_MODE": self.finchnode_mode,
            "NOTIFY_MODE": self.notify_mode,
            "XAI_VOICE_MODE": self.xai_voice_mode,
            "TELEPHONY_MODE": self.telephony_mode,
            "EXTRACTION_MODE": self.extraction_mode,
            "FETCH_MODE": self.fetch_mode,
        }

    def using_dev_credentials(self) -> list[str]:
        """Names of credentials currently falling back to dev defaults."""
        names = [
            f"{name.value.upper()}_SERVICE_TOKEN"
            for name, value in {
                ServiceName.RELAY: self.relay_service_token,
                ServiceName.FETCH: self.fetch_service_token,
                ServiceName.VOICE: self.voice_service_token,
            }.items()
            if not value
        ]
        if not self.core_webhook_secret:
            names.append("CORE_WEBHOOK_SECRET")
        return names if self.allows_dev_defaults else []

    def missing_live_settings(self) -> list[str]:
        """Human-readable list of settings required by the selected live modes."""
        missing: list[str] = []

        def need(flag: str, *pairs: tuple[str, str]) -> None:
            missing.extend(f"{flag} requires {name}" for name, value in pairs if not value)

        if self.finchnode_mode in ("sandbox", "production"):
            # FINCHNODE_BASE_URL is optional: both use https://api.finchnode.com/api/v1.
            need(
                f"FINCHNODE_MODE={self.finchnode_mode}",
                ("FINCHNODE_API_KEY", self.finchnode_api_key),
            )
        if self.notify_mode == "relay_service":
            need("NOTIFY_MODE=relay_service", ("RELAY_SERVICE_URL", self.relay_service_url))
        if self.xai_voice_mode == "live":
            need(
                "XAI_VOICE_MODE=live",
                ("XAI_API_KEY", self.xai_api_key),
                ("XAI_REALTIME_URL", self.xai_realtime_url),
            )
            if self.telephony_mode == "live":
                need(
                    "TELEPHONY_MODE=live + XAI_VOICE_MODE=live",
                    ("XAI_WEBHOOK_SIGNING_SECRET", self.xai_webhook_signing_secret),
                )
        if self.telephony_mode == "live":
            live_pairs = [
                ("TELNYX_API_KEY", self.telnyx_api_key),
                ("TELNYX_CONNECTION_ID", self.telnyx_connection_id),
                ("TELNYX_PHONE_NUMBER", self.telnyx_phone_number),
                ("PUBLIC_BASE_URL", self.public_base_url),
                ("XAI_SIP_URI", self.xai_sip_uri),
            ]
            if self.telnyx_webhook_verify:
                live_pairs.append(("TELNYX_PUBLIC_KEY", self.telnyx_public_key))
            need("TELEPHONY_MODE=live", *live_pairs)
            for marker in ("2", "3"):
                parts = [
                    (f"TELNYX_API_KEY_{marker}", getattr(self, f"telnyx_api_key_{marker}")),
                    (
                        f"TELNYX_CONNECTION_ID_{marker}",
                        getattr(self, f"telnyx_connection_id_{marker}"),
                    ),
                    (
                        f"TELNYX_PHONE_NUMBER_{marker}",
                        getattr(self, f"telnyx_phone_number_{marker}"),
                    ),
                ]
                if any(value for _, value in parts) and not all(value for _, value in parts):
                    names = ", ".join(name for name, value in parts if not value)
                    missing.append(f"partial Telnyx fallback profile: missing {names}")
        if self.fetch_mode == "agentverse":
            need("FETCH_MODE=agentverse", ("ORCHESTRATOR_URL", self.orchestrator_url))
        if self.extraction_mode == "ollama":
            need(
                "EXTRACTION_MODE=ollama",
                ("OLLAMA_URL", self.ollama_url),
                ("OLLAMA_MODEL", self.ollama_model),
            )
        return missing

    def environment_mistakes(self) -> list[str]:
        """Fail-closed environment rules. Production and staging never run on
        mock adapters; staging mirrors production apart from sandbox data and
        test telephony numbers (docs/DEPLOYMENT.md)."""
        mistakes: list[str] = []
        if self.app_env not in ("staging", "prod"):
            return mistakes
        # Durable shared database: no silent SQLite fallback outside dev/test.
        if not self.database_url or not self.database_url.startswith("postgresql"):
            mistakes.append(f"APP_ENV={self.app_env} requires a PostgreSQL DATABASE_URL")
        # No webhook-signature bypass outside dev/test (TELNYX_WEBHOOK_VERIFY).
        if not self.telnyx_webhook_verify:
            mistakes.append(
                f"APP_ENV={self.app_env} refuses TELNYX_WEBHOOK_VERIFY=false "
                "(webhook signature verification is mandatory)"
            )
        if not self.telnyx_public_key:
            mistakes.append(f"APP_ENV={self.app_env} requires TELNYX_PUBLIC_KEY")
        # Public HTTPS endpoint for the Telnyx/Relay webhooks.
        if not self.public_base_url.startswith("https://") or "localhost" in self.public_base_url:
            mistakes.append(f"APP_ENV={self.app_env} requires an https PUBLIC_BASE_URL")
        # Patients must actually be notified through the Relay service.
        if self.notify_mode != "relay_service":
            mistakes.append(f"APP_ENV={self.app_env} requires NOTIFY_MODE=relay_service")
        # The agent network runs as a persistent service, not in-process logic.
        if self.fetch_mode != "agentverse":
            mistakes.append(f"APP_ENV={self.app_env} requires FETCH_MODE=agentverse")
        # No silent mock adapters.
        if self.xai_voice_mode != "live":
            mistakes.append(f"APP_ENV={self.app_env} requires XAI_VOICE_MODE=live")
        if self.telephony_mode != "live":
            mistakes.append(f"APP_ENV={self.app_env} requires TELEPHONY_MODE=live")
        expected_finchnode = "sandbox" if self.app_env == "staging" else "production"
        if self.finchnode_mode != expected_finchnode:
            mistakes.append(f"APP_ENV={self.app_env} requires FINCHNODE_MODE={expected_finchnode}")
        # FinchNode tells Core when a patient connects or revokes sharing by webhook.
        if not self.finchnode_webhook_secret:
            mistakes.append(f"APP_ENV={self.app_env} requires FINCHNODE_WEBHOOK_SECRET")
        if self.app_env in ("staging", "prod"):
            # Service tokens are real secrets outside dev/test (no dev fallbacks).
            for name, value in (
                ("RELAY_SERVICE_TOKEN", self.relay_service_token),
                ("FETCH_SERVICE_TOKEN", self.fetch_service_token),
                ("VOICE_SERVICE_TOKEN", self.voice_service_token),
                ("CORE_WEBHOOK_SECRET", self.core_webhook_secret),
            ):
                if not value:
                    mistakes.append(f"APP_ENV={self.app_env} requires {name}")
        if self.app_env == "prod" and "ngrok" in self.public_base_url:
            mistakes.append("APP_ENV=prod refuses a tunnel (ngrok) PUBLIC_BASE_URL")
        return mistakes

    def validate_for_startup(self) -> None:
        """Raise ConfigError naming every missing variable for the selected modes."""
        mistakes = self.missing_live_settings() + self.environment_mistakes()
        if mistakes:
            lines = "\n".join(f"  - {item}" for item in mistakes)
            raise ConfigError(
                "Amyna Core cannot start: live integrations are missing settings.\n"
                f"{lines}\n"
                "Fill these in .env (see docs/SETUP_KEYS.md) or switch the flag back to mock."
            )


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings (tests build their own Settings instead)."""
    return Settings()
