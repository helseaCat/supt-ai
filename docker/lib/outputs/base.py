"""Base class for output destinations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PRContext:
    """Metadata about the reviewed pull request."""

    url: str = ""
    title: str = ""
    author: str = ""
    branch: str = ""
    repo: str = ""


class OutputDestination(ABC):
    """Interface for review output destinations."""

    @abstractmethod
    def send(self, pr_context: PRContext, review: dict) -> None:
        """Send a review result to this destination.

        Args:
            pr_context: Metadata about the reviewed PR.
            review: Parsed review YAML as a dict.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this destination."""
