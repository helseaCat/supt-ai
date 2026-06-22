"""Output router — dispatches review results to active destinations."""

import logging

from lib.config import Settings
from lib.outputs.base import OutputDestination
from lib.outputs.console import ConsoleOutput
from lib.outputs.discord import DiscordOutput

logger = logging.getLogger(__name__)


def build_destinations(settings: Settings) -> list[OutputDestination]:
    """Instantiate output destinations based on config.

    Args:
        settings: Application settings with active destinations list.

    Returns:
        List of active OutputDestination instances.
    """
    registry: dict[str, OutputDestination] = {
        "console": ConsoleOutput(),
        "discord": DiscordOutput(settings),
    }

    active = []
    for dest_name in settings.destinations:
        if dest_name in registry:
            active.append(registry[dest_name])
        else:
            logger.warning("Unknown output destination: %s", dest_name)

    return active


def route_review(
    pr_url: str, review: dict, destinations: list[OutputDestination]
) -> list[str]:
    """Send review to all active destinations.

    Args:
        pr_url: URL of the reviewed pull request.
        review: Parsed review YAML as a dict.
        destinations: Active output destinations.

    Returns:
        List of destination names that were sent to.
    """
    sent_to = []
    for dest in destinations:
        try:
            dest.send(pr_url, review)
            sent_to.append(dest.name)
        except Exception as e:
            logger.error("Failed to send to %s: %s", dest.name, e)

    return sent_to
