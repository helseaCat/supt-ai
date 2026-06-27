"""Lambda handler — thin entry point for supt-ai.

Supports two invocation modes:
1. SQS: event contains Records[] from the review queue (production path)
2. Direct invocation: event contains pr_url directly (local testing, CLI)
"""

import json
import logging
import os

from lib.config import load_settings
from lib.github_app import get_installation_token
from lib.outputs.base import PRContext
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

    Handles SQS events (production) and direct invocations (testing).
    """
    logger.info("Received event: %s", json.dumps(event, default=str)[:2000])

    # SQS event — process each record
    if "Records" in event:
        return _handle_sqs(event)
    else:
        return _handle_direct(event)


def _handle_sqs(event: dict) -> dict:
    """Handle SQS event from the review queue."""
    for record in event["Records"]:
        body = json.loads(record["body"])

        pr_context = PRContext(
            url=body["pr_url"],
            title=body.get("title", ""),
            author=body.get("author", ""),
            branch=body.get("branch", ""),
            repo=body.get("repo", ""),
        )

        result = _run_review(pr_context, "review")

        if not result["success"]:
            # Raise to let SQS retry (and eventually DLQ)
            raise RuntimeError(
                f"Review failed for {pr_context.url}: {result.get('error', 'unknown')}"
            )

    return {"message": "Reviews complete", "count": len(event["Records"])}


def _handle_direct(event: dict) -> dict:
    """Handle a direct Lambda invocation (local testing, CLI)."""
    pr_url = event.get("pr_url")
    if not pr_url:
        return _response(400, {"error": "Missing required field: pr_url"})

    command = event.get("command", "review")

    pr_context = PRContext(
        url=pr_url,
        title=event.get("title", ""),
        author=event.get("author", ""),
        branch=event.get("branch", ""),
        repo=event.get("repo", ""),
    )

    result = _run_review(pr_context, command)

    if not result["success"]:
        return _response(500, result)

    return _response(200, result)


def _run_review(pr_context: PRContext, command: str) -> dict:
    """Run the review and return a result dict."""
    # Generate a fresh installation token from GitHub App credentials
    if not all([
        settings.github_app_id,
        settings.github_app_private_key,
        settings.github_app_installation_id,
    ]):
        return {"success": False, "error": "GitHub App credentials not configured"}

    try:
        token = get_installation_token(
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key,
            installation_id=settings.github_app_installation_id,
        )
    except RuntimeError as e:
        logger.error("Failed to generate installation token: %s", e)
        return {"success": False, "error": f"GitHub App auth failed: {e}"}

    # Set token in environment so PR-Agent picks it up
    os.environ["GITHUB__USER_TOKEN"] = token

    # Run the review
    result = run_review(pr_context.url, command, settings)

    if not result.success:
        return {
            "success": False,
            "error": "PR-Agent failed",
            "returncode": result.returncode,
            "stderr": result.errors[:2000],
            "stdout": result.output[:2000],
        }

    # Parse and route output
    review = extract_review(result.output, result.errors)
    sent_to = []

    if review:
        sent_to = route_review(pr_context, review, destinations)
    else:
        logger.warning("Could not parse review YAML from output")

    return {
        "success": True,
        "message": "Review complete",
        "command": command,
        "pr_url": pr_context.url,
        "sent_to": sent_to,
    }


def _response(status_code: int, body: dict) -> dict:
    """Build a Lambda response compatible with API Gateway / direct invocation."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
