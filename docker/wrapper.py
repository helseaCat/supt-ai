"""Lambda handler wrapper for the supt-ai Docker image.

Invokes PR-Agent to review a pull request and returns the output.
"""

import json
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    """Main Lambda entry point.

    Expects event to contain:
        pr_url: URL of the pull request to review
        command: (optional) PR-Agent command — defaults to "review"
    """
    logger.info("Received event: %s", json.dumps(event, default=str))

    pr_url = event.get("pr_url")
    if not pr_url:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing required field: pr_url"}),
        }

    command = event.get("command", "review")

    # Validate GitHub token is available
    if not os.environ.get("GITHUB__USER_TOKEN"):
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "GITHUB__USER_TOKEN environment variable not set"}),
        }

    # Build PR-Agent CLI invocation
    cli_args = [
        sys.executable, "-m", "pr_agent.cli",
        f"--pr_url={pr_url}",
        command,
    ]

    logger.info("Running PR-Agent: %s", " ".join(cli_args))

    # Set PR-Agent config via environment variables
    env = os.environ.copy()
    env.setdefault("CONFIG.GIT_PROVIDER", "github")
    env.setdefault("CONFIG.PUBLISH_OUTPUT", "false")
    env.setdefault("CONFIG.VERBOSITY_LEVEL", "2")

    try:
        result = subprocess.run(
            cli_args,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )

        output = result.stdout or ""
        errors = result.stderr or ""

        logger.info("PR-Agent stdout: %s", output[:2000])
        if errors:
            logger.warning("PR-Agent stderr: %s", errors[:2000])

        if result.returncode != 0:
            return {
                "statusCode": 500,
                "body": json.dumps({
                    "error": "PR-Agent exited with non-zero status",
                    "returncode": result.returncode,
                    "stderr": errors[:2000],
                    "stdout": output[:2000],
                }),
            }

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Review complete",
                "command": command,
                "pr_url": pr_url,
                "output": output,
            }),
        }

    except subprocess.TimeoutExpired:
        return {
            "statusCode": 504,
            "body": json.dumps({"error": "PR-Agent timed out after 90 seconds"}),
        }
    except Exception as e:
        logger.exception("Unexpected error running PR-Agent")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
