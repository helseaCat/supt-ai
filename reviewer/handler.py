"""Lambda entry point for the agentic code reviewer."""

from typing import Any


def handler(event: dict, context: Any) -> dict:
    """Main Lambda entry point. Handles SQS events and direct invocations.

    SQS path: extracts PR metadata from message body, runs review, raises on failure.
    Direct path: accepts pr_url + metadata, returns result dict.
    """
    raise NotImplementedError("Handler implementation pending")
