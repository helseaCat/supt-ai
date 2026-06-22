"""Lambda handler — thin entry point for supt-ai.

Delegates to lib modules for config, review execution, and output routing.
"""

import json
import logging

from lib.config import load_settings
from lib.parser import extract_review
from lib.reviewer import run_review
from lib.router import build_destinations, route_review

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Load settings once at cold start
settings = load_settings()
destinations = build_destinations(settings)


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

    # Validate GitHub token
    if not settings.github_token:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "GITHUB__USER_TOKEN not configured"}),
        }

    # Run the review
    result = run_review(pr_url, command, settings)

    if not result.success:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": "PR-Agent failed",
                "returncode": result.returncode,
                "stderr": result.errors[:2000],
                "stdout": result.output[:2000],
            }),
        }

    # Parse and route output
    review = extract_review(result.output)
    sent_to = []

    if review:
        sent_to = route_review(pr_url, review, destinations)
    else:
        logger.warning("Could not parse review YAML from output")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Review complete",
            "command": command,
            "pr_url": pr_url,
            "sent_to": sent_to,
        }),
    }
