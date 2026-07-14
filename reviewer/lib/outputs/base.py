"""Base class for output destinations.

Defines the OutputDestination abstract base class that all output adapters
(GitHub PR, Discord, Console) must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from lib.review_engine import PRContext


class OutputDestination(ABC):
    """Abstract base class for review output destinations.

    Each destination receives the completed review and PR context, and is
    responsible for formatting and delivering the review to its target
    (e.g., GitHub PR comments, Discord webhook, console logs).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the identifier for this destination (e.g., 'github', 'discord')."""
        ...

    @abstractmethod
    def send(self, pr_context: PRContext, review: dict) -> None:
        """Deliver the review to this destination.

        Args:
            pr_context: Metadata about the pull request that was reviewed.
            review: The validated review dict conforming to ReviewSchema.

        Implementations should handle their own errors gracefully and never
        raise exceptions that would block other destinations.
        """
        ...
