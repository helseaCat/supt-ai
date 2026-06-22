"""Application settings loaded from environment variables and config.toml."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import tomllib


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

    # LLM
    xai_api_key: str = ""

    # Discord
    discord_webhook_url: str = ""
    discord_embed_color: int = 5814783

    # Output
    destinations: list[str] = field(default_factory=lambda: ["console"])

    # Timeouts
    review_timeout: int = 90


def load_settings(config_path: str | None = None) -> Settings:
    """Load settings from config.toml then override with environment variables."""
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

    # Environment overrides (always win)
    settings.github_token = os.environ.get("GITHUB__USER_TOKEN", "")
    settings.xai_api_key = os.environ.get("XAI_API_KEY", "")
    settings.discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")

    timeout = os.environ.get("REVIEW_TIMEOUT")
    if timeout:
        settings.review_timeout = int(timeout)

    return settings
