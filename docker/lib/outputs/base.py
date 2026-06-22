"""Base class for output destinations."""

from abc import ABC, abstractmethod


class OutputDestination(ABC):
    """Interface for review output destinations."""

    @abstractmethod
    def send(self, pr_url: str, review: dict) -> None:
        """Send a review result to this destination.

        Args:
            pr_url: URL of the reviewed pull request.
            review: Parsed review YAML as a dict.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this destination."""
