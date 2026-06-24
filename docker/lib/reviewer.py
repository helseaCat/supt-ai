"""PR-Agent invocation logic."""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass

from lib.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """Result of a PR-Agent invocation."""

    success: bool
    output: str = ""
    errors: str = ""
    returncode: int = 0


def run_review(pr_url: str, command: str, settings: Settings) -> ReviewResult:
    """Invoke PR-Agent CLI and return the result.

    Args:
        pr_url: URL of the pull request to review.
        command: PR-Agent command (review, describe, improve, etc.).
        settings: Application settings.

    Returns:
        ReviewResult with success status and captured output.
    """
    cli_args = [
        sys.executable, "-m", "pr_agent.cli",
        f"--pr_url={pr_url}",
        command,
    ]

    logger.info("Running PR-Agent: %s", " ".join(cli_args))

    # Pass config through environment variables
    env = os.environ.copy()
    env.setdefault("CONFIG.GIT_PROVIDER", settings.git_provider)
    env.setdefault("CONFIG.PUBLISH_OUTPUT", str(settings.publish_output).lower())
    env.setdefault("CONFIG.VERBOSITY_LEVEL", str(settings.verbosity_level))

    # Inject secrets into subprocess env (PR-Agent/litellm read these directly)
    if settings.xai_api_key:
        env["XAI_API_KEY"] = settings.xai_api_key
    if settings.webhook_secret:
        env["WEBHOOK_SECRET"] = settings.webhook_secret

    try:
        result = subprocess.run(
            cli_args,
            capture_output=True,
            text=True,
            timeout=settings.review_timeout,
            env=env,
        )

        output = result.stdout or ""
        errors = result.stderr or ""

        if result.returncode != 0:
            logger.warning(
                "PR-Agent exited with code %d: %s", result.returncode, errors[:500]
            )
            return ReviewResult(
                success=False,
                output=output,
                errors=errors,
                returncode=result.returncode,
            )

        return ReviewResult(success=True, output=output, errors=errors)

    except subprocess.TimeoutExpired:
        logger.error("PR-Agent timed out after %ds", settings.review_timeout)
        return ReviewResult(success=False, errors="Timed out")
