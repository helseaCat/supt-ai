"""Discord webhook output destination."""

import logging

import requests

from lib.config import Settings
from lib.outputs.base import OutputDestination, PRContext

logger = logging.getLogger(__name__)


class DiscordOutput(OutputDestination):
    """Posts review as an embed to a Discord webhook."""

    def __init__(self, settings: Settings):
        self._webhook_url = settings.discord_webhook_url
        self._embed_color = settings.discord_embed_color

    @property
    def name(self) -> str:
        return "discord"

    def send(self, pr_context: PRContext, review: dict) -> None:
        """Post the review embed to Discord."""
        if not self._webhook_url:
            logger.warning("Discord webhook URL not configured, skipping")
            return

        payload = self._build_embed(pr_context, review)

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

    def _build_embed(self, pr_context: PRContext, review: dict) -> dict:
        """Build a Discord embed payload from the parsed review."""
        r = review.get("review", {})

        effort = self._clean(r.get("estimated_effort_to_review_[1-5]", "?"))
        tests = self._clean(r.get("relevant_tests", "?"))
        security = self._clean(r.get("security_concerns", "None"))
        issues = r.get("key_issues_to_review", [])

        # Title: PR title or fallback
        title = pr_context.title or "PR Review Complete"

        # Description with author, branch, repo
        description_parts = []
        if pr_context.author:
            description_parts.append(f"**Author:** {pr_context.author}")
        if pr_context.branch:
            description_parts.append(f"**Branch:** `{pr_context.branch}`")
        if pr_context.repo:
            description_parts.append(f"**Repo:** {pr_context.repo}")
        description = "\n".join(description_parts)

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
                "title": title,
                "url": pr_context.url,
                "description": description,
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
