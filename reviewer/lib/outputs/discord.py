"""Discord webhook output adapter.

Posts review results as a rich embed to a Discord webhook, with severity-based
color coding and a summary of the top findings.
"""

from __future__ import annotations

import logging

import requests

from lib.config import Settings
from lib.outputs.base import OutputDestination
from lib.review_engine import PRContext

logger = logging.getLogger(__name__)

# Discord embed color codes.
_COLOR_RED = 0xE74C3C  # Security concerns present
_COLOR_YELLOW = 0xF1C40F  # Findings present, no security concerns
_COLOR_GREEN = 0x2ECC71  # Clean review (no findings)

# Values of security_concerns that are treated as "no concern".
_NO_SECURITY_VALUES = {"", "None", "No", "none", "no"}

# Maximum number of findings shown in the embed.
_MAX_EMBED_FINDINGS = 5

# Request timeout for Discord webhook.
_DISCORD_TIMEOUT = 10


class DiscordOutput(OutputDestination):
    """Posts review embed to a Discord webhook.

    The embed uses color-coded severity, includes PR metadata, and shows
    the top findings. On failure, logs the error and continues without
    blocking other output destinations.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize with application settings.

        Args:
            settings: Settings instance containing discord_webhook_url.
        """
        self._webhook_url = settings.discord_webhook_url

    @property
    def name(self) -> str:
        """Return the destination identifier."""
        return "discord"

    def send(self, pr_context: PRContext, review: dict) -> None:
        """Post the review embed to Discord.

        If the webhook URL is empty or missing, logs a warning and returns.
        On HTTP failure or timeout, logs the error and returns without raising.

        Args:
            pr_context: PR metadata (title, url, author, branch).
            review: Validated review dict conforming to ReviewSchema.
        """
        if not self._webhook_url:
            logger.warning("Discord webhook URL is empty or missing — skipping Discord delivery.")
            return

        embed = self._build_embed(pr_context, review)
        payload = {"embeds": [embed]}

        try:
            resp = requests.post(
                self._webhook_url,
                json=payload,
                timeout=_DISCORD_TIMEOUT,
            )
            if resp.status_code >= 300:
                logger.error(
                    "Discord webhook returned HTTP %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except requests.Timeout:
            logger.error("Discord webhook request timed out after %ds.", _DISCORD_TIMEOUT)
        except requests.RequestException as exc:
            logger.error("Discord webhook request failed: %s", exc)

    def _build_embed(self, pr_context: PRContext, review: dict) -> dict:
        """Build the Discord embed payload from the review.

        Args:
            pr_context: PR metadata.
            review: Validated review dict.

        Returns:
            A dict representing the Discord embed object.
        """
        findings = review.get("findings", [])
        security_concerns = review.get("security_concerns", "")
        effort_score = review.get("effort_score", 3)

        color = self._severity_color(review)

        # Build description with author and branch.
        description = f"**Author:** {pr_context.author} | **Branch:** {pr_context.branch}"

        # Build fields.
        fields: list[dict] = []

        fields.append({
            "name": "Effort Score",
            "value": f"{effort_score}/5",
            "inline": True,
        })

        if security_concerns and security_concerns not in _NO_SECURITY_VALUES:
            fields.append({
                "name": "Security Concerns",
                "value": security_concerns[:1024],  # Discord field value limit
                "inline": False,
            })

        # Show first N findings by severity (already sorted in the schema).
        for finding in findings[:_MAX_EMBED_FINDINGS]:
            severity = finding.get("severity", "info")
            title = finding.get("title", "Untitled")
            file_path = finding.get("file_path", "")
            start_line = finding.get("start_line", "")
            end_line = finding.get("end_line", "")

            # Build location reference.
            location = ""
            if file_path:
                location = f"`{file_path}"
                if start_line:
                    location += f":{start_line}"
                    if end_line and end_line != start_line:
                        location += f"-{end_line}"
                location += "`"

            field_value = title
            if location:
                field_value += f"\n{location}"

            fields.append({
                "name": f"[{severity}]",
                "value": field_value[:1024],
                "inline": False,
            })

        embed: dict = {
            "title": pr_context.title,
            "url": pr_context.pr_url,
            "description": description,
            "color": color,
            "fields": fields,
        }

        return embed

    @staticmethod
    def _severity_color(review: dict) -> int:
        """Determine the embed color based on review content.

        - Red: security_concerns is non-empty and meaningful (not "None"/"No"/etc.)
        - Yellow: findings are present but no security concerns
        - Green: no findings at all

        Args:
            review: Validated review dict.

        Returns:
            Integer color code for the Discord embed.
        """
        security_concerns = review.get("security_concerns", "")
        findings = review.get("findings", [])

        # Red: active security concerns.
        if security_concerns and security_concerns not in _NO_SECURITY_VALUES:
            return _COLOR_RED

        # Yellow: findings present but no security concerns.
        if findings:
            return _COLOR_YELLOW

        # Green: clean review.
        return _COLOR_GREEN
