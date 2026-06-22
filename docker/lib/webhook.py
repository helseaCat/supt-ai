"""GitHub webhook signature verification and event parsing."""

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# PR events we care about
SUPPORTED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@dataclass
class WebhookEvent:
    """Parsed webhook event."""

    pr_url: str
    action: str
    event_type: str


def verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature.

    Args:
        payload_body: Raw request body bytes.
        signature_header: Value of X-Hub-Signature-256 header.
        secret: The webhook secret shared with GitHub.

    Returns:
        True if signature is valid.
    """
    if not signature_header:
        logger.warning("No signature header present")
        return False

    expected = "sha256=" + hmac.HMAC(
        secret.encode(), payload_body, hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


def parse_api_gateway_event(event: dict) -> tuple[bytes, dict]:
    """Extract the raw body and headers from an API Gateway HTTP API event.

    Args:
        event: Lambda event from API Gateway HTTP API (v2 payload format).

    Returns:
        Tuple of (raw body bytes, headers dict with lowercase keys).
    """
    import base64

    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}

    body = event.get("body", "")
    if event.get("isBase64Encoded", False):
        raw_body = base64.b64decode(body)
    else:
        raw_body = body.encode("utf-8") if isinstance(body, str) else body

    return raw_body, headers


def parse_webhook_payload(payload: dict, event_type: str) -> WebhookEvent | None:
    """Extract PR URL and action from a GitHub webhook payload.

    Args:
        payload: Parsed JSON body of the webhook.
        event_type: Value of X-GitHub-Event header.

    Returns:
        WebhookEvent if this is a PR event we should handle, None otherwise.
    """
    # Only handle pull_request events
    if event_type != "pull_request":
        logger.info("Ignoring event type: %s", event_type)
        return None

    action = payload.get("action", "")
    if action not in SUPPORTED_ACTIONS:
        logger.info("Ignoring PR action: %s", action)
        return None

    pr = payload.get("pull_request", {})
    pr_url = pr.get("html_url")

    if not pr_url:
        logger.warning("No PR URL found in payload")
        return None

    # Skip draft PRs
    if pr.get("draft", False):
        logger.info("Skipping draft PR: %s", pr_url)
        return None

    return WebhookEvent(pr_url=pr_url, action=action, event_type=event_type)
