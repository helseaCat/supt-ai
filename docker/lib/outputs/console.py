"""Console output destination — prints review to stdout."""

import json
import logging

from lib.outputs.base import OutputDestination, PRContext

logger = logging.getLogger(__name__)


class ConsoleOutput(OutputDestination):
    """Prints the review to stdout as formatted JSON."""

    @property
    def name(self) -> str:
        return "console"

    def send(self, pr_context: PRContext, review: dict) -> None:
        """Print the review to stdout."""
        logger.info("Review for %s:\n%s", pr_context.url, json.dumps(review, indent=2))
