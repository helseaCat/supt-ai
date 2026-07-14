"""Output routing for review results.

Provides the OutputRouter which dispatches completed reviews to all configured
output destinations (GitHub PR, Discord, Console). Individual destination
failures are logged but never block delivery to other destinations.
"""

from __future__ import annotations

import logging

from lib.outputs.base import OutputDestination
from lib.review_engine import PRContext

logger = logging.getLogger(__name__)

__all__ = ["OutputDestination", "OutputRouter"]


class OutputRouter:
    """Dispatches review results to configured output destinations.

    Iterates through all registered destinations and calls send() on each.
    If one destination fails, the error is logged and remaining destinations
    still receive the review.
    """

    def __init__(self, destinations: list[OutputDestination]) -> None:
        """Initialize the router with a list of output destinations.

        Args:
            destinations: List of OutputDestination instances to dispatch to.
        """
        self._destinations = destinations

    def dispatch(self, pr_context: PRContext, review: dict) -> None:
        """Send the review to all configured destinations.

        Each destination is called independently. If a destination raises an
        exception, the error is logged and the next destination is attempted.

        Args:
            pr_context: Metadata about the pull request that was reviewed.
            review: The validated review dict conforming to ReviewSchema.
        """
        for destination in self._destinations:
            try:
                destination.send(pr_context, review)
                logger.info("Review dispatched to '%s' successfully.", destination.name)
            except Exception:
                logger.exception(
                    "Failed to dispatch review to '%s' — continuing to next destination.",
                    destination.name,
                )
