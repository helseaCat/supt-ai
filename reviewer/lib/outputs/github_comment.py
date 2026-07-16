"""GitHub issue comment output adapter.

Posts review findings as a single issue comment on the PR. Deletes any
previous comment by this bot before posting a new one, ensuring only one
review comment is visible at a time.
"""

from __future__ import annotations

import logging

from lib.github_client import GitHubClient
from lib.outputs.base import OutputDestination
from lib.review_engine import PRContext

logger = logging.getLogger(__name__)

# Marker to identify comments posted by this bot.
_BOT_COMMENT_MARKER = "<!-- supt-ai-review -->"


class GitHubCommentOutput(OutputDestination):
    """Posts review as an issue comment on the PR.

    Deletes previous bot comments before posting a new one, so only the
    latest review is visible. Uses a hidden HTML marker to identify its
    own comments.
    """

    def __init__(self, github_client: GitHubClient) -> None:
        self._github_client = github_client

    @property
    def name(self) -> str:
        return "github"

    def send(self, pr_context: PRContext, review: dict) -> None:
        """Delete old bot comment and post new review comment."""
        # Delete previous bot comments.
        self._delete_previous_comments(pr_context)

        # Build the comment body.
        body = self._build_comment_body(pr_context, review)

        # Post the new comment.
        self._github_client.post_issue_comment(
            owner=pr_context.owner,
            repo=pr_context.repo,
            issue_number=pr_context.pr_number,
            body=body,
        )
        logger.info("Posted review comment on PR #%d.", pr_context.pr_number)

    def _delete_previous_comments(self, pr_context: PRContext) -> None:
        """Delete all previous comments by this bot on the PR."""
        try:
            comments = self._github_client.list_issue_comments(
                owner=pr_context.owner,
                repo=pr_context.repo,
                issue_number=pr_context.pr_number,
            )
            for comment in comments:
                body = comment.get("body", "")
                if _BOT_COMMENT_MARKER in body:
                    comment_id = comment.get("id")
                    if comment_id:
                        try:
                            self._github_client.delete_issue_comment(
                                owner=pr_context.owner,
                                repo=pr_context.repo,
                                comment_id=comment_id,
                            )
                            logger.info("Deleted previous bot comment %d.", comment_id)
                        except Exception:
                            logger.warning("Failed to delete comment %d, continuing.", comment_id)
        except Exception:
            logger.warning("Failed to list comments for cleanup, continuing.")

    def _build_comment_body(self, pr_context: PRContext, review: dict) -> str:
        """Build the full markdown comment body from the review."""
        parts: list[str] = [_BOT_COMMENT_MARKER, ""]

        # Summary
        summary = review.get("summary", "")
        if summary:
            parts.append("## Review Summary\n")
            parts.append(summary)
            parts.append("")

        # Metadata
        effort_score = review.get("effort_score", 3)
        parts.append(f"**Effort Score:** {effort_score}/5")
        parts.append("")

        security_concerns = review.get("security_concerns", "")
        if security_concerns:
            parts.append(f"**Security Concerns:** {security_concerns}")
            parts.append("")

        tests_assessment = review.get("tests_assessment", "")
        if tests_assessment:
            parts.append(f"**Tests Assessment:** {tests_assessment}")
            parts.append("")

        # Findings
        findings = review.get("findings", [])
        if findings:
            parts.append("## Findings\n")
            for finding in findings:
                severity = finding.get("severity", "info")
                category = finding.get("category", "")
                title = finding.get("title", "")
                explanation = finding.get("explanation", "")
                file_path = finding.get("file_path", "")
                start_line = finding.get("start_line", "")
                end_line = finding.get("end_line", "")

                # Build file link
                location = ""
                if file_path:
                    # Link to file in the PR's head commit
                    file_url = f"https://github.com/{pr_context.owner}/{pr_context.repo}/blob/{pr_context.branch}/{file_path}"
                    if start_line:
                        file_url += f"#L{start_line}"
                        if end_line and end_line != start_line:
                            file_url += f"-L{end_line}"
                    location = f" ([`{file_path}:{start_line}`]({file_url}))"

                parts.append(f"- **[{severity}/{category}]** {title}{location}")
                if explanation:
                    parts.append(f"  > {explanation}")
                parts.append("")
        else:
            parts.append("*No findings. Clean code!*")
            parts.append("")

        return "\n".join(parts).strip()
