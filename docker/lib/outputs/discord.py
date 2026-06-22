"""Discord webhook output destination."""

import logging

import requests

from lib.config import Settings
from lib.outputs.base import OutputDestination

logger = logging.getLogger(__name__)


class DiscordOutput(OutputDestination):
    """Posts review as an embed to a Discord webhook."""

    def __init__(self, settings: Settings):
        self._webhook_url = settings.discord_webhook_url
        self._embed_color = settings.discord_embed_color

    @property
    def name(self) -> str:
        return "discord"

    def send(self, pr_url: str, review: dict) -> None:
        """Post the review embed to Discord."""
        if not self._webhook_url:
            logger.warning("Discord webhook URL not configured, skipping")
            return

        payload = self._build_embed(pr_url, review)

        try:
            resp = requests.post(self._webhook_url, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                logger.info("Discord notification sent")
            else:
                logger.warning(
                    "Discord returned %s: %s", resp.status_code, resp.text[:200]
                )
        except Exception as e:
            logger.warning("Failed to send Discord notification: %s", e)

    def _build_embed(self, pr_url: str, review: dict) -> dict:
        """Build a Discord embed payload from the parsed review."""
        r = review.get("review", {})

        effort = self._clean(r.get("estimated_effort_to_review_[1-5]", "?"))
        tests = self._clean(r.get("relevant_tests", "?"))
        security = self._clean(r.get("security_concerns", "None"))
        issues = r.get("key_issues_to_review", [])

        fields = [
            {"name": "Effort to Review", "value": f"{effort}/5", "inline": True},
            {"name": "Tests Added", "value": tests, "inline": True},
            {"name": "Security Concerns", "value": security, "inline": False},
        ]

        if issues:
            issue_lines = []
            for issue in issues[:3]:
                header = self._clean(issue.get("issue_header", "Issue"))
                content = self._clean(issue.get("issue_content", ""))
                file = self._clean(issue.get("relevant_file", ""))
                issue_lines.append(f"**{header}** (`{file}`)\n{content}")
            fields.append({
                "name": "Key Issues",
                "value": "\n\n".join(issue_lines)[:1024],
                "inline": False,
            })

        return {
            "embeds": [{
                "title": "PR Review Complete",
                "url": pr_url,
                "color": self._embed_color,
                "fields": fields,
                "footer": {"text": "supt-ai | PR-Agent + Grok"},
            }]
        }

    @staticmethod
    def _clean(value) -> str:
        """Strip whitespace from YAML block scalar values."""
        if isinstance(value, str):
            return value.strip()
        return str(value)
