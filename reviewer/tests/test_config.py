"""Unit tests for config loading and precedence.

Validates: Requirements 13.1, 13.2, 13.3, 13.7
"""

from __future__ import annotations

import json
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from lib.config import Settings, load_settings


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove all env vars that load_settings reads so tests are isolated."""
    for key in (
        "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_INSTALLATION_ID",
        "WEBHOOK_SECRET", "XAI_API_KEY", "DISCORD_WEBHOOK_URL",
        "REVIEW_TIMEOUT", "ITERATION_BUDGET", "SECRETS_ARN",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def config_toml(tmp_path):
    """Write a temporary config.toml and return its path."""
    content = """
[llm]
model = "grok-3-mini"
base_url = "https://custom.endpoint/v1"

[agent]
iteration_budget = 20
review_timeout = 120

[output]
destinations = ["discord", "github"]
"""
    path = tmp_path / "config.toml"
    path.write_text(content)
    return str(path)


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_load_defaults():
    """load_settings with no config file and no env vars returns defaults."""
    settings = load_settings(config_path="/nonexistent/config.toml")
    assert settings.model == "grok-4.5"
    assert settings.base_url == "https://api.x.ai/v1"
    assert settings.iteration_budget == 10
    assert settings.review_timeout == 90
    assert settings.destinations == ["console"]
    assert settings.xai_api_key == ""


def test_load_from_config_toml(config_toml):
    """load_settings with a real config.toml picks up the toml values."""
    settings = load_settings(config_path=config_toml)
    assert settings.model == "grok-3-mini"
    assert settings.base_url == "https://custom.endpoint/v1"
    assert settings.iteration_budget == 20
    assert settings.review_timeout == 120
    assert settings.destinations == ["discord", "github"]


def test_env_var_overrides(config_toml, monkeypatch):
    """Environment variables override config.toml values (Req 13.2, 13.3)."""
    monkeypatch.setenv("XAI_API_KEY", "env-key-123")
    monkeypatch.setenv("REVIEW_TIMEOUT", "45")

    settings = load_settings(config_path=config_toml)
    assert settings.xai_api_key == "env-key-123"
    assert settings.review_timeout == 45  # env beats toml's 120


def _mock_boto3(mock_client):
    """Return a context manager that injects a fake boto3 module."""
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_client
    return patch.dict("sys.modules", {"boto3": mock_boto3})


def test_env_var_precedence_over_secrets(config_toml, monkeypatch):
    """Env vars beat Secrets Manager values (Req 13.3)."""
    secret_data = json.dumps({"XAI_API_KEY": "secret-key", "REVIEW_TIMEOUT": "60"})
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": secret_data}

    monkeypatch.setenv("XAI_API_KEY", "env-key-wins")

    with _mock_boto3(mock_client):
        settings = load_settings(config_path=config_toml, secrets_arn="arn:aws:test")

    assert settings.xai_api_key == "env-key-wins"  # env beats secret


def test_secrets_manager_loading(config_toml, monkeypatch):
    """Secrets Manager values are loaded correctly (Req 13.1)."""
    secret_data = json.dumps({
        "GITHUB_APP_ID": "12345",
        "GITHUB_APP_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----",
        "XAI_API_KEY": "xai-secret-key",
    })
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": secret_data}

    with _mock_boto3(mock_client):
        settings = load_settings(config_path=config_toml, secrets_arn="arn:aws:test")

    assert settings.github_app_id == "12345"
    assert settings.github_app_private_key == "-----BEGIN RSA PRIVATE KEY-----"
    assert settings.xai_api_key == "xai-secret-key"


def test_secrets_manager_unreachable(config_toml, caplog):
    """When Secrets Manager is unreachable, warning is logged and settings load (Req 13.6)."""
    mock_client = MagicMock()
    mock_client.get_secret_value.side_effect = RuntimeError("Network timeout")

    with _mock_boto3(mock_client):
        with caplog.at_level(logging.WARNING):
            settings = load_settings(config_path=config_toml, secrets_arn="arn:aws:test")

    assert "unreachable" in caplog.text.lower()
    # Settings still load from toml
    assert settings.model == "grok-3-mini"


def test_int_fields_cast_from_string(monkeypatch):
    """REVIEW_TIMEOUT="60" should become int 60 (not str)."""
    monkeypatch.setenv("REVIEW_TIMEOUT", "60")
    monkeypatch.setenv("ITERATION_BUDGET", "15")

    settings = load_settings(config_path="/nonexistent/config.toml")
    assert settings.review_timeout == 60
    assert isinstance(settings.review_timeout, int)
    assert settings.iteration_budget == 15
    assert isinstance(settings.iteration_budget, int)


def test_missing_credentials_detection():
    """Property 9 / Req 13.7: when required credentials are missing, fields are empty strings."""
    settings = load_settings(config_path="/nonexistent/config.toml")

    required_fields = {
        "github_app_id": settings.github_app_id,
        "github_app_private_key": settings.github_app_private_key,
        "github_app_installation_id": settings.github_app_installation_id,
        "xai_api_key": settings.xai_api_key,
    }
    missing = [name for name, val in required_fields.items() if not val]
    assert len(missing) == 4
    assert "github_app_id" in missing
    assert "xai_api_key" in missing
