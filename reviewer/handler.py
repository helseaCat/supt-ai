"""Lambda entry point for the agentic code reviewer.

Handles two invocation paths:
- SQS: Extracts PR metadata from the SQS record body and runs a review.
  On failure, raises to trigger SQS retry/DLQ behavior.
- Direct: Accepts PR metadata directly in the event dict (for local testing).
  Returns the review result as a response dict.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from lib.config import Settings, load_settings
from lib.github_client import GitHubClient
from lib.outputs import OutputRouter
from lib.outputs.console import ConsoleOutput
from lib.outputs.discord import DiscordOutput
from lib.outputs.github_comment import GitHubCommentOutput
from lib.review_engine import PRContext, ReviewEngine, ReviewResult
from lib.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level logging setup for Lambda (JSON structured logging)
# ---------------------------------------------------------------------------
# Configure root logger for Lambda — Lambda's default level is WARN,
# which suppresses INFO logs. We must explicitly set it to INFO.
logging.getLogger().setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any) -> dict:
    """Main Lambda entry point. Handles SQS events and direct invocations.

    SQS path: extracts PR metadata from message body, runs review, raises on failure.
    Direct path: accepts pr_url + metadata, returns result dict.

    Args:
        event: Lambda event payload (SQS Records or direct invocation dict).
        context: Lambda context object (provides get_remaining_time_in_millis).

    Returns:
        A response dict with statusCode and body.
    """
    if "Records" in event:
        return _handle_sqs(event, context)
    return _handle_direct(event, context)


# ---------------------------------------------------------------------------
# SQS invocation path
# ---------------------------------------------------------------------------


def _handle_sqs(event: dict, context: Any) -> dict:
    """Process SQS Records. Raises RuntimeError on failure to trigger SQS retry.

    Extracts PR metadata from the first SQS record body, builds a PRContext,
    and runs the review. On exception: logs and re-raises so SQS retries the
    message (eventually sending to DLQ after maxReceiveCount).

    Args:
        event: SQS event with "Records" list.
        context: Lambda context object.

    Returns:
        A dict with statusCode 200 on success.

    Raises:
        RuntimeError: On any failure (triggers SQS retry).
    """
    record = event["Records"][0]
    body = json.loads(record["body"])

    pr_url = body["pr_url"]
    action = body.get("action", "")
    title = body.get("title", "")
    author = body.get("author", "")
    branch = body.get("branch", "")
    repo_full = body["repo"]  # format: "owner/repo"

    owner, repo = repo_full.split("/", 1)
    # PR number is the last path segment of the PR URL
    pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])

    pr_context = PRContext(
        pr_url=pr_url,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        title=title,
        author=author,
        branch=branch,
    )

    try:
        _run_review(pr_context, context)
    except Exception as exc:
        logger.error(json.dumps({
            "event": "review_failed",
            "repo": f"{owner}/{repo}",
            "pr_number": pr_number,
            "error": str(exc),
        }))
        raise

    return {"statusCode": 200}


# ---------------------------------------------------------------------------
# Direct invocation path (local testing)
# ---------------------------------------------------------------------------


def _handle_direct(event: dict, context: Any) -> dict:
    """Process direct invocation for local testing.

    Accepts PR metadata directly from the event dict, runs the review, and
    returns the review result in the response body.

    Args:
        event: Dict with pr_url, action, title, author, branch, repo fields.
        context: Lambda context object (can be None for local testing).

    Returns:
        A dict with statusCode and the review result as the body.
    """
    pr_url = event["pr_url"]
    title = event.get("title", "")
    author = event.get("author", "")
    branch = event.get("branch", "")
    repo_full = event["repo"]  # format: "owner/repo"

    owner, repo = repo_full.split("/", 1)
    pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])

    pr_context = PRContext(
        pr_url=pr_url,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        title=title,
        author=author,
        branch=branch,
    )

    review_result = _run_review(pr_context, context)
    return {"statusCode": 200, "body": review_result}


# ---------------------------------------------------------------------------
# Review orchestration
# ---------------------------------------------------------------------------

_REQUIRED_CREDENTIALS = [
    ("github_app_id", "GITHUB_APP_ID"),
    ("github_app_private_key", "GITHUB_APP_PRIVATE_KEY"),
    ("github_app_installation_id", "GITHUB_APP_INSTALLATION_ID"),
    ("xai_api_key", "XAI_API_KEY"),
]


def _run_review(pr_context: PRContext, context: Any) -> dict:
    """Orchestrate a single review: auth -> engine -> route outputs.

    1. Generate a unique invocation_id for correlation
    2. Load settings via load_settings()
    3. Validate required credentials
    4. Create GitHubClient and generate installation token
    5. Fetch PR diff
    6. Create ToolRegistry and ReviewEngine
    7. Run the engine
    8. Route outputs to configured destinations
    9. Log structured summary

    Args:
        pr_context: Metadata about the PR being reviewed.
        context: Lambda context (provides get_remaining_time_in_millis).

    Returns:
        The review result dict.

    Raises:
        RuntimeError: If required credentials are missing or review fails.
    """
    invocation_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    # --- Load settings ---
    settings = load_settings()

    # --- Validate required credentials (Property 9) ---
    missing: list[str] = []
    for field_name, credential_name in _REQUIRED_CREDENTIALS:
        if not getattr(settings, field_name, ""):
            missing.append(credential_name)

    if missing:
        raise RuntimeError(
            f"Missing required credentials: {', '.join(missing)}"
        )

    # --- Create GitHub client ---
    github_client = GitHubClient(
        app_id=settings.github_app_id,
        private_key=settings.github_app_private_key,
        installation_id=settings.github_app_installation_id,
        request_timeout=settings.tool_timeout,
    )

    # --- Fetch PR diff ---
    diff = github_client.get_pr_diff(
        owner=pr_context.owner,
        repo=pr_context.repo,
        pr_number=pr_context.pr_number,
    )

    # --- Create ToolRegistry ---
    tool_registry = ToolRegistry(
        github_client=github_client,
        owner=pr_context.owner,
        repo=pr_context.repo,
        pr_number=pr_context.pr_number,
        head_ref=pr_context.branch,
    )

    # --- Remaining time helper ---
    if context and hasattr(context, "get_remaining_time_in_millis"):
        remaining_time_ms = context.get_remaining_time_in_millis
    else:
        # Fallback for local testing: assume 80 seconds remaining
        _local_start = time.perf_counter()
        remaining_time_ms = lambda: max(0, int(80_000 - (time.perf_counter() - _local_start) * 1000))  # noqa: E731

    # --- Create and run ReviewEngine ---
    engine = ReviewEngine(
        settings=settings,
        github_client=github_client,
        tool_registry=tool_registry,
        remaining_time_ms=remaining_time_ms,
    )

    result: ReviewResult = engine.run(pr_context, diff)

    # --- Build OutputRouter and dispatch ---
    destinations = _build_destinations(settings, github_client)
    router = OutputRouter(destinations)
    router.dispatch(pr_context, result.review)

    # --- Structured logging (Requirement 11.2, 11.5) ---
    duration_ms = int((time.perf_counter() - start_time) * 1000)
    log_entry = {
        "event": "review_complete",
        "invocation_id": invocation_id,
        "repo": f"{pr_context.owner}/{pr_context.repo}",
        "pr_number": pr_context.pr_number,
        "duration_ms": duration_ms,
        "finding_count": len(result.review.get("findings", [])),
        "iteration_count": result.iterations,
        "tool_calls": result.tool_calls,
        "tokens_prompt": result.tokens_prompt,
        "tokens_completion": result.tokens_completion,
    }
    logger.info(json.dumps(log_entry))

    return result.review


# ---------------------------------------------------------------------------
# Output destination builder
# ---------------------------------------------------------------------------


def _build_destinations(settings: Settings, github_client: GitHubClient) -> list:
    """Build output destination instances based on settings.destinations.

    Args:
        settings: Loaded application settings.
        github_client: Authenticated GitHub client for the GitHub PR output.

    Returns:
        List of OutputDestination instances.
    """
    from lib.outputs.base import OutputDestination

    destinations: list[OutputDestination] = []

    for dest_name in settings.destinations:
        if dest_name == "github":
            destinations.append(GitHubCommentOutput(github_client))
        elif dest_name == "discord":
            destinations.append(DiscordOutput(settings))
        elif dest_name == "console":
            destinations.append(ConsoleOutput())
        else:
            logger.warning("Unknown output destination '%s' — skipping.", dest_name)

    return destinations
