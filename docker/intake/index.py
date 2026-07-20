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

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    # Handle /review command on PR comments
    if event_type == "issue_comment":
        return _handle_issue_comment(payload)

    # Only handle pull_request events
    if event_type != "pull_request":
        return _response(200, {"message": f"Ignored event: {event_type}"})

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
    _enqueue_review(pr_url, action, pr, payload)

    logger.info("Enqueued review for %s (action: %s)", pr_url, action)
    return _response(200, {"message": "Review queued", "pr_url": pr_url})


# ---------------------------------------------------------------------------
# /review command handler
# ---------------------------------------------------------------------------

REVIEW_COMMAND = "/review"


def _handle_issue_comment(payload: dict) -> dict:
    """Handle issue_comment events — triggers review on /review command."""
    action = payload.get("action", "")
    if action != "created":
        return _response(200, {"message": f"Ignored comment action: {action}"})

    # Exact command match — only "/review" as the first token
    comment_body = payload.get("comment", {}).get("body", "").strip()
    first_token = comment_body.lower().split()[0] if comment_body else ""
    if first_token != REVIEW_COMMAND:
        return _response(200, {"message": "Not a /review command"})

    # Only allow repo collaborators / PR author to trigger reviews
    commenter = payload.get("comment", {}).get("user", {}).get("login", "")
    pr_author = payload.get("issue", {}).get("user", {}).get("login", "")
    author_association = payload.get("comment", {}).get("author_association", "")
    allowed_associations = {"OWNER", "MEMBER", "COLLABORATOR"}

    if commenter != pr_author and author_association not in allowed_associations:
        logger.info("Ignoring /review from non-collaborator: %s", commenter)
        return _response(200, {"message": "Not authorized to trigger /review"})

    # issue_comment fires for both issues and PRs — only PRs have pull_request key
    issue = payload.get("issue", {})
    if "pull_request" not in issue:
        return _response(200, {"message": "Comment is not on a PR"})

    pr_url = issue.get("pull_request", {}).get("html_url", "")
    if not pr_url:
        # Fallback to issue html_url (GitHub PR URLs work for both)
        pr_url = issue.get("html_url", "")

    if not pr_url:
        return _response(400, {"error": "Could not determine PR URL"})

    # For issue_comment, we don't have head ref directly — reviewer will fetch it
    _enqueue_review(pr_url, "review_requested_by_comment", issue, payload)

    logger.info(
        "Enqueued /review for %s (requested by %s)", pr_url, commenter
    )
    return _response(200, {"message": "Review queued", "pr_url": pr_url})


def _enqueue_review(pr_url: str, action: str, pr_or_issue: dict, payload: dict) -> None:
    """Enqueue a review job to SQS."""
    sqs = boto3.client("sqs")
    message = {
        "pr_url": pr_url,
        "action": action,
        "title": pr_or_issue.get("title", ""),
        "author": pr_or_issue.get("user", {}).get("login", ""),
        "branch": pr_or_issue.get("head", {}).get("ref", ""),
        "repo": payload.get("repository", {}).get("full_name", ""),
    }

    sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps(message),
    )
