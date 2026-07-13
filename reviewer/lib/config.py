"""Configuration loading for the agentic code reviewer.

Provides the Settings dataclass and load_settings() to read configuration from
three sources with the following precedence (highest wins):
    environment variables > Secrets Manager > config.toml defaults
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    """All configuration values for the reviewer Lambda.

    Defaults match the values shipped in config.toml so the system works
    without any external configuration source.
    """

    # GitHub App
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_app_installation_id: str = ""

    # Webhook
    webhook_secret: str = ""

    # LLM
    xai_api_key: str = ""
    model: str = "grok-4.5"
    base_url: str = "https://api.x.ai/v1"

    # Agent Loop
    iteration_budget: int = 10
    max_tool_calls_per_turn: int = 5
    tool_timeout: int = 30

    # Discord
    discord_webhook_url: str = ""

    # Output
    destinations: list[str] = field(default_factory=lambda: ["console"])

    # Timeouts
    review_timeout: int = 90


# Mapping from TOML section.key → Settings field name.
_TOML_FIELD_MAP: dict[tuple[str, str], str] = {
    ("llm", "model"): "model",
    ("llm", "base_url"): "base_url",
    ("agent", "iteration_budget"): "iteration_budget",
    ("agent", "max_tool_calls_per_turn"): "max_tool_calls_per_turn",
    ("agent", "tool_timeout"): "tool_timeout",
    ("agent", "review_timeout"): "review_timeout",
    ("output", "destinations"): "destinations",
}

# Mapping from environment variable / secret key name → Settings field name.
_ENV_SECRET_FIELD_MAP: dict[str, str] = {
    "GITHUB_APP_ID": "github_app_id",
    "GITHUB_APP_PRIVATE_KEY": "github_app_private_key",
    "GITHUB_APP_INSTALLATION_ID": "github_app_installation_id",
    "WEBHOOK_SECRET": "webhook_secret",
    "XAI_API_KEY": "xai_api_key",
    "DISCORD_WEBHOOK_URL": "discord_webhook_url",
    "REVIEW_TIMEOUT": "review_timeout",
    "ITERATION_BUDGET": "iteration_budget",
}

# Fields that require int conversion when loaded from string sources.
_INT_FIELDS: set[str] = {"review_timeout", "iteration_budget"}


def _default_config_path() -> Path:
    """Return the default config.toml path relative to this module."""
    return Path(__file__).resolve().parent.parent / "config.toml"


def _load_from_secrets_manager(secrets_arn: str) -> dict[str, object]:
    """Fetch secret values from AWS Secrets Manager.

    Args:
        secrets_arn: The ARN or name of the secret to fetch.

    Returns:
        A dict mapping Settings field names to their values.
        Returns an empty dict if the secret is unreachable or unparseable.
    """
    try:
        import boto3  # noqa: PLC0415 — imported inside function for local dev compat

        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secrets_arn)
        secret_data: dict[str, str] = json.loads(response["SecretString"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Secrets Manager unreachable (%s): %s", secrets_arn, exc)
        return {}

    overrides: dict[str, object] = {}
    for key, field_name in _ENV_SECRET_FIELD_MAP.items():
        value = secret_data.get(key)
        if value:
            overrides[field_name] = int(value) if field_name in _INT_FIELDS else value
    return overrides


def _load_from_env() -> dict[str, object]:
    """Read known environment variables and map them to Settings fields.

    Only includes variables that are set and non-empty.

    Returns:
        A dict mapping Settings field names to their values.
    """
    overrides: dict[str, object] = {}
    for key, field_name in _ENV_SECRET_FIELD_MAP.items():
        value = os.environ.get(key)
        if value:
            overrides[field_name] = int(value) if field_name in _INT_FIELDS else value
    return overrides


def load_settings(
    config_path: str | None = None,
    secrets_arn: str | None = None,
) -> Settings:
    """Load settings from config.toml, Secrets Manager, and environment variables.

    Precedence (highest wins): env vars > Secrets Manager > config.toml defaults.

    Args:
        config_path: Optional explicit path to a config.toml file.
                     If None, discovers config.toml relative to this module
                     (i.e. reviewer/config.toml).
        secrets_arn: Optional ARN of the Secrets Manager secret to load.
                     If None, reads from os.environ["SECRETS_ARN"] if available.

    Returns:
        A Settings instance with all configuration sources merged.
    """
    path = Path(config_path) if config_path else _default_config_path()

    # Layer 1: config.toml
    overrides: dict[str, object] = {}
    if path.is_file():
        with open(path, "rb") as f:
            toml_data = tomllib.load(f)

        for (section, key), field_name in _TOML_FIELD_MAP.items():
            if section in toml_data and key in toml_data[section]:
                overrides[field_name] = toml_data[section][key]

    # Layer 2: Secrets Manager (only when ARN is available)
    arn = secrets_arn or os.environ.get("SECRETS_ARN")
    if arn:
        overrides.update(_load_from_secrets_manager(arn))

    # Layer 3: Environment variable overrides (highest precedence)
    overrides.update(_load_from_env())

    return Settings(**overrides)
