"""Application settings loaded from Secrets Manager, environment variables, and config.toml."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    """Centralized application configuration."""

    # PR-Agent
    git_provider: str = "github"
    publish_output: bool = False
    verbosity_level: int = 2
    model: str = ""

    # GitHub
    github_token: str = ""

    # Webhook
    webhook_secret: str = ""

    # LLM
    xai_api_key: str = ""

    # Discord
    discord_webhook_url: str = ""
    discord_embed_color: int = 5814783

    # Output
    destinations: list[str] = field(default_factory=lambda: ["console"])

    # Timeouts
    review_timeout: int = 90


def _load_secrets_from_aws() -> dict:
    """Fetch secrets from AWS Secrets Manager if SECRETS_ARN is set."""
    secrets_arn = os.environ.get("SECRETS_ARN")
    if not secrets_arn:
        return {}

    try:
        import boto3

        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secrets_arn)
        return json.loads(response["SecretString"])
    except Exception as e:
        logger.warning("Failed to load secrets from Secrets Manager: %s", e)
        return {}


def load_settings(config_path: str | None = None) -> Settings:
    """Load settings from Secrets Manager, config.toml, and environment variables.

    Priority (highest wins): env vars > Secrets Manager > config.toml
    """
    settings = Settings()

    # Load from config.toml if available
    if config_path is None:
        config_path = os.environ.get("CONFIG_PATH", "/var/task/config.toml")

    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, "rb") as f:
            config = tomllib.load(f)

        # Output section
        output = config.get("output", {})
        if "destinations" in output:
            settings.destinations = output["destinations"]

        # Discord section
        discord = config.get("discord", {})
        if "embed_color" in discord:
            settings.discord_embed_color = discord["embed_color"]

        # PR-Agent section
        pr_agent = config.get("pr_agent", {})
        if "git_provider" in pr_agent:
            settings.git_provider = pr_agent["git_provider"]
        if "publish_output" in pr_agent:
            settings.publish_output = pr_agent["publish_output"]
        if "verbosity_level" in pr_agent:
            settings.verbosity_level = pr_agent["verbosity_level"]
        if "model" in pr_agent:
            settings.model = pr_agent["model"]

    # Load from Secrets Manager (overrides config.toml)
    secrets = _load_secrets_from_aws()
    if secrets:
        settings.github_token = secrets.get("GITHUB__USER_TOKEN", "")
        settings.webhook_secret = secrets.get("WEBHOOK_SECRET", "")
        settings.xai_api_key = secrets.get("XAI_API_KEY", "")
        settings.discord_webhook_url = secrets.get("DISCORD_WEBHOOK_URL", "")
        logger.info("Loaded secrets from Secrets Manager")

    # Environment overrides (always win — for local dev with .env)
    if os.environ.get("GITHUB__USER_TOKEN"):
        settings.github_token = os.environ["GITHUB__USER_TOKEN"]
    if os.environ.get("WEBHOOK_SECRET"):
        settings.webhook_secret = os.environ["WEBHOOK_SECRET"]
    if os.environ.get("XAI_API_KEY"):
        settings.xai_api_key = os.environ["XAI_API_KEY"]
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        settings.discord_webhook_url = os.environ["DISCORD_WEBHOOK_URL"]

    timeout = os.environ.get("REVIEW_TIMEOUT")
    if timeout:
        settings.review_timeout = int(timeout)

    return settings
