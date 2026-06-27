"""Intake Lambda — validates GitHub webhook and enqueues to SQS.

This Lambda responds to GitHub within seconds. The heavy review work
happens asynchronously in the reviewer Lambda via SQS.
"""

import base64
import hashlib
import hmac
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

QUEUE_URL = os.environ["QUEUE_URL"]
SECRETS_ARN = os.environ["SECRETS_ARN"]

SUPPORTED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}

# Cache secrets across invocations (Lambda container reuse)
_webhook_secret: str | None = None


def _get_webhook_secret() -> str:
    """Fetch webhook secret from Secrets Manager (cached)."""
    global _webhook_secret
    if _webhook_secret is None:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=SECRETS_ARN)
        secrets = json.loads(response["SecretString"])
        _webhook_secret = secrets.get("WEBHOOK_SECRET", "")
    return _webhook_secret


def _verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not signature_header:
        return False
    expected = "sha256=" + hmac.HMAC(
        secret.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    """API Gateway HTTP API entry point."""
    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}

    # Extract raw body
    body = event.get("body", "")
    if event.get("isBase64Encoded", False):
        raw_body = base64.b64decode(body)
    else:
        raw_body = body.encode("utf-8") if isinstance(body, str) else body

    # Verify signature
    secret = _get_webhook_secret()
    if not secret:
        return _response(500, {"error": "WEBHOOK_SECRET not configured"})

    signature = headers.get("x-hub-signature-256", "")
    if not _verify_signature(raw_body, signature, secret):
        logger.warning("Invalid webhook signature")
        return _response(401, {"error": "Invalid signature"})

    # Handle ping
    event_type = headers.get("x-github-event", "")
    if event_type == "ping":
        return _response(200, {"message": "pong"})

    # Only handle pull_request events
    if event_type != "pull_request":
        return _response(200, {"message": f"Ignored event: {event_type}"})

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    # Filter to supported actions
    action = payload.get("action", "")
    if action not in SUPPORTED_ACTIONS:
        return _response(200, {"message": f"Ignored action: {action}"})

    # Skip draft PRs
    pr = payload.get("pull_request", {})
    if pr.get("draft", False):
        return _response(200, {"message": "Skipped draft PR"})

    pr_url = pr.get("html_url", "")
    if not pr_url:
        return _response(400, {"error": "No PR URL in payload"})

    # Enqueue the review job
    sqs = boto3.client("sqs")
    message = {
        "pr_url": pr_url,
        "action": action,
        "title": pr.get("title", ""),
        "author": pr.get("user", {}).get("login", ""),
        "branch": pr.get("head", {}).get("ref", ""),
        "repo": payload.get("repository", {}).get("full_name", ""),
    }

    sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps(message),
    )

    logger.info("Enqueued review for %s (action: %s)", pr_url, action)
    return _response(200, {"message": "Review queued", "pr_url": pr_url})
