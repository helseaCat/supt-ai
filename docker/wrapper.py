"""Lambda handler — thin entry point for supt-ai.

Supports two invocation modes:
1. Direct invocation: event contains pr_url directly (local testing, CLI)
2. API Gateway: event is an HTTP request from GitHub webhook
"""

import json
import logging

from lib.config import load_settings
from lib.outputs.base import PRContext
from lib.parser import extract_review
from lib.reviewer import run_review
from lib.router import build_destinations, route_review
from lib.webhook import parse_api_gateway_event, parse_webhook_payload, verify_signature

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Load settings once at cold start
settings = load_settings()
destinations = build_destinations(settings)


def handler(event, context):
    """Main Lambda entry point.

    Handles both direct invocations (pr_url in event) and API Gateway
    webhook events from GitHub.
    """
    logger.info("Received event: %s", json.dumps(event, default=str)[:2000])

    # Determine invocation mode
    if "requestContext" in event:
        return _handle_webhook(event)
    else:
        return _handle_direct(event)


def _handle_webhook(event: dict) -> dict:
    """Handle an API Gateway event from GitHub webhook."""
    raw_body, headers = parse_api_gateway_event(event)

    # Verify signature
    signature = headers.get("x-hub-signature-256", "")
    if not settings.webhook_secret:
        return _response(500, {"error": "WEBHOOK_SECRET not configured"})

    if not verify_signature(raw_body, signature, settings.webhook_secret):
        logger.warning("Invalid webhook signature")
        return _response(401, {"error": "Invalid signature"})

    # Parse the event
    event_type = headers.get("x-github-event", "")

    # Respond to ping immediately
    if event_type == "ping":
        return _response(200, {"message": "pong"})

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    webhook_event = parse_webhook_payload(payload, event_type)
    if not webhook_event:
        return _response(200, {"message": "Event ignored"})

    # Build PR context from webhook payload
    pr = payload.get("pull_request", {})
    pr_context = PRContext(
        url=webhook_event.pr_url,
        title=pr.get("title", ""),
        author=pr.get("user", {}).get("login", ""),
        branch=pr.get("head", {}).get("ref", ""),
        repo=payload.get("repository", {}).get("full_name", ""),
    )

    # Run the review
    return _run_and_respond(pr_context, "review")


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

    return _run_and_respond(pr_context, command)


def _run_and_respond(pr_context: PRContext, command: str) -> dict:
    """Run the review and return a structured response."""
    # Validate GitHub token
    if not settings.github_token:
        return _response(500, {"error": "GITHUB__USER_TOKEN not configured"})

    # Run the review
    result = run_review(pr_context.url, command, settings)

    if not result.success:
        return _response(500, {
            "error": "PR-Agent failed",
            "returncode": result.returncode,
            "stderr": result.errors[:2000],
            "stdout": result.output[:2000],
        })

    # Parse and route output
    review = extract_review(result.output)
    sent_to = []

    if review:
        sent_to = route_review(pr_context, review, destinations)
    else:
        logger.warning("Could not parse review YAML from output")

    return _response(200, {
        "message": "Review complete",
        "command": command,
        "pr_url": pr_context.url,
        "sent_to": sent_to,
    })


def _response(status_code: int, body: dict) -> dict:
    """Build a Lambda response compatible with API Gateway."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
