"""PHI redaction and fail-fast configuration."""

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.audit import PhiRedactingFilter, mask_member_ref, mask_phone, redact, redact_text
from app.config import ConfigError, Settings
from app.demo_support import build_isolated_app, clean_env


@pytest.fixture(autouse=True)
def _no_repo_env() -> Iterator[None]:
    """A configured developer .env (loaded into the process env by fetch imports) must
    never poison these configuration tests, including the direct Settings() below."""
    with clean_env():
        yield


def test_redact_text_masks_phi_patterns() -> None:
    text = "SSN 123-45-6789, phone +1 555-555-0100, mail jo@example.org, member SYN-MBR-000123"
    redacted = redact_text(text)
    for secret in ("123-45-6789", "555-0100", "jo@example.org", "SYN-MBR-000123"):
        assert secret not in redacted
    assert "[REDACTED-SSN]" in redacted


def test_redact_keeps_ids_and_drops_phi_keys() -> None:
    payload = {"case_id": "case_1234567890abcdef", "notes": "anything", "nested": [{"text": "x"}]}
    assert redact(payload) == {
        "case_id": "case_1234567890abcdef",
        "notes": "[REDACTED]",
        "nested": [{"text": "[REDACTED]"}],
    }


def test_masking_helpers() -> None:
    assert mask_phone("+15555550100").endswith("0100") and "5555" not in mask_phone("+15555550100")
    assert mask_member_ref("SYN-MBR-000123") == "•••123"


def test_log_filter_redacts_messages() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "ssn=%s", ("123-45-6789",), None)
    PhiRedactingFilter().filter(record)
    assert "123-45-6789" not in record.getMessage()


def _settings(**overrides: object) -> Settings:
    with clean_env():  # a configured developer .env must never leak into these checks
        app_env = overrides.pop("app_env", "test")
        return Settings(_env_file=None, app_env=app_env, **overrides)  # type: ignore[arg-type]


def test_default_settings_are_all_mock_and_valid() -> None:
    settings = _settings()
    settings.validate_for_startup()
    assert set(settings.modes().values()) <= {"mock", "outbox", "rules", "local_direct"}


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"xai_voice_mode": "live"}, "XAI_API_KEY"),
        ({"telephony_mode": "live"}, "TELNYX_API_KEY"),
        ({"finchnode_mode": "sandbox"}, "FINCHNODE_API_KEY"),
        ({"notify_mode": "relay_service", "relay_service_url": ""}, "RELAY_SERVICE_URL"),
        ({"app_env": "prod"}, "RELAY_SERVICE_TOKEN"),
    ],
)
def test_live_modes_fail_fast_naming_the_variable(overrides: dict, missing: str) -> None:
    settings = Settings(_env_file=None, **{"app_env": "test", **overrides})  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match=missing):
        settings.validate_for_startup()


def test_dev_fallback_tokens_only_outside_prod() -> None:
    assert all(_settings().service_tokens().values())
    prod = Settings(_env_file=None, app_env="prod")  # type: ignore[arg-type]
    assert not any(prod.service_tokens().values())
    assert prod.webhook_secret() == ""


def test_isolated_apps_ignore_a_configured_repo_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression test: fetch imports load the repo .env into the process env; isolated
    apps must still run fully in mock mode (and the environment must be restored)."""
    monkeypatch.setenv("FINCHNODE_MODE", "sandbox")
    monkeypatch.setenv("FINCHNODE_API_KEY", "ck_test_x")
    monkeypatch.setenv("FINCHNODE_BASE_URL", "https://example.invalid")
    app = build_isolated_app(tmp_path / "core.db")
    assert app.state.container.finchnode.mode == "mock"
    assert os.environ["FINCHNODE_MODE"] == "sandbox"  # clean_env() restored it


def test_a_partial_telnyx_fallback_profile_is_named() -> None:
    """Fallback Telnyx profiles must be complete key + connection + number triplets."""
    settings = _settings(
        telephony_mode="live",
        telnyx_api_key="K",
        telnyx_connection_id="c1",
        telnyx_phone_number="+15555550199",
        public_base_url="https://example.ngrok-free.dev",
        xai_sip_uri="sip:test@sip.x.ai",
        telnyx_webhook_verify=False,
        telnyx_api_key_2="KEY_v2",
        telnyx_phone_number_2="+10000000002",
    )  # TELNYX_CONNECTION_ID_2 missing
    with pytest.raises(ConfigError, match="TELNYX_CONNECTION_ID_2"):
        settings.validate_for_startup()


# ---------------------------------------------------------------------------
# environment_mistakes: staging/production run fail-closed, never on mock adapters
# ---------------------------------------------------------------------------


def _live_overrides(app_env: str, finchnode_mode: str) -> dict[str, object]:
    """A minimally complete live configuration for `app_env` (all live adapters)."""
    return {
        "app_env": app_env,
        "database_url": "postgresql+psycopg://amyna:amyna_dev@db:5432/amyna",
        "public_base_url": "https://core.amyna.example.com",
        "notify_mode": "relay_service",
        "relay_service_url": "https://relay.amyna.example.com",
        "fetch_mode": "agentverse",
        "orchestrator_url": "https://agents.amyna.example.com",
        "xai_voice_mode": "live",
        "xai_api_key": "xai-test",
        "telephony_mode": "live",
        "telnyx_api_key": "KEY",
        "telnyx_connection_id": "conn",
        "telnyx_phone_number": "+15555550100",
        "telnyx_public_key": "pubkey",
        "telnyx_webhook_verify": True,
        "xai_sip_uri": "sip:test@sip.x.ai",
        "xai_webhook_signing_secret": "whsec_test",  # SIP attach mode requires the secret
        "finchnode_mode": finchnode_mode,
        "finchnode_api_key": "ck_live" if finchnode_mode == "production" else "ck_test_x",
        "finchnode_base_url": "" if finchnode_mode == "production" else "https://sandbox.example",
        "relay_service_token": "r",
        "fetch_service_token": "f",
        "voice_service_token": "v",
        "core_webhook_secret": "w",
    }


def test_dev_and_test_allow_mock_adapters() -> None:
    for env in ("dev", "test"):
        settings = _settings(app_env=env)
        assert settings.environment_mistakes() == []
        settings.validate_for_startup()  # no fail-closed enforcement outside staging/prod


@pytest.mark.parametrize(
    ("app_env", "finchnode_mode"), [("staging", "sandbox"), ("prod", "production")]
)
def test_a_fully_configured_env_has_no_mistakes(app_env: str, finchnode_mode: str) -> None:
    settings = _settings(**_live_overrides(app_env, finchnode_mode))
    assert settings.environment_mistakes() == []
    settings.validate_for_startup()


@pytest.mark.parametrize("app_env", ["staging", "prod"])
def test_env_requires_postgres_and_https_and_tokens(app_env: str) -> None:
    baseline = _live_overrides(app_env, "sandbox" if app_env == "staging" else "production")
    settings = _settings(**{**baseline, "database_url": ""})  # silent SQLite fallback
    assert any("PostgreSQL DATABASE_URL" in m for m in settings.environment_mistakes())
    settings = _settings(**{**baseline, "public_base_url": "http://localhost:8080"})
    assert any("https" in m for m in settings.environment_mistakes())
    settings = _settings(**{**baseline, "relay_service_token": "", "fetch_service_token": ""})
    assert any("SERVICE_TOKEN" in m for m in settings.environment_mistakes())


@pytest.mark.parametrize("app_env", ["staging", "prod"])
def test_env_refuses_mock_integrations(app_env: str) -> None:
    baseline = _live_overrides(app_env, "sandbox" if app_env == "staging" else "production")
    for flag, value, needle in (
        ("notify_mode", "outbox", "NOTIFY_MODE=relay_service"),
        ("fetch_mode", "local_direct", "FETCH_MODE=agentverse"),
        ("xai_voice_mode", "mock", "XAI_VOICE_MODE=live"),
        ("telephony_mode", "mock", "TELEPHONY_MODE=live"),
    ):
        settings = _settings(**{**baseline, flag: value})
        assert any(needle in m for m in settings.environment_mistakes())


@pytest.mark.parametrize("app_env", ["staging", "prod"])
def test_env_forces_telnyx_webhook_verification(app_env: str) -> None:
    baseline = _live_overrides(app_env, "sandbox" if app_env == "staging" else "production")
    settings = _settings(**{**baseline, "telnyx_webhook_verify": False})
    assert any("TELNYX_WEBHOOK_VERIFY" in m for m in settings.environment_mistakes())
    settings = _settings(**{**baseline, "telnyx_public_key": ""})
    assert any("TELNYX_PUBLIC_KEY" in m for m in settings.environment_mistakes())


@pytest.mark.parametrize(
    ("app_env", "expected", "wrong"),
    [("staging", "sandbox", "production"), ("prod", "production", "sandbox")],
)
def test_env_requires_the_right_finchnode_mode(app_env: str, expected: str, wrong: str) -> None:
    baseline = _live_overrides(app_env, expected)
    settings = _settings(**{**baseline, "finchnode_mode": wrong})
    assert any(f"FINCHNODE_MODE={expected}" in m for m in settings.environment_mistakes())


def test_production_refuses_a_tunnel_public_url() -> None:
    baseline = _live_overrides("prod", "production")
    settings = _settings(**{**baseline, "public_base_url": "https://x.ngrok-free.dev"})
    assert any("tunnel" in m for m in settings.environment_mistakes())
    staging = _settings(**_live_overrides("staging", "sandbox"))
    assert any("tunnel" in m for m in staging.environment_mistakes()) is False
