"""GitHub PR Review output adapter.

Posts review findings as inline comments on the PR diff via the GitHub Pull
Request Review API, with overflow findings included in the review body.
"""

from __future__ import annotations

import logging

from lib.github_client import GitHubClient
from lib.outputs.base import OutputDestination
from lib.review_engine import PRContext

logger = logging.getLogger(__name__)

# Maximum number of inline comments per review (GitHub API limit consideration).
_MAX_INLINE_COMMENTS = 50


class GitHubPROutput(OutputDestination):
    """Posts review as inline PR comments via the GitHub Pull Request Review API.

    Maps findings with valid file/line references to inline comments (up to 50).
    Remaining findings and the overall summary go into the review body text.
    Submits with event="COMMENT" (never APPROVE or REQUEST_CHANGES).
    """

    def __init__(self, github_client: GitHubClient) -> None:
        """Initialize with an authenticated GitHub client.

        Args:
            github_client: GitHubClient instance with a valid installation token.
        """
        self._github_client = github_client

    @property
    def name(self) -> str:
        """Return the destination identifier."""
        return "github"

    def send(self, pr_context: PRContext, review: dict) -> None:
        """Post the review to the GitHub PR.

        Fetches the list of files in the PR diff, maps findings to inline
        comments where possible, builds a review body with the summary and
        any overflow findings, and submits via the Pull Request Review API.

        Args:
            pr_context: PR metadata (owner, repo, pr_number, etc.).
            review: Validated review dict conforming to ReviewSchema.
        """
        # Get the set of files present in the PR diff.
        diff_files = self._get_diff_files(pr_context)

        findings = review.get("findings", [])
        inline_comments, overflow_findings = self._build_inline_comments(findings, diff_files)
        body = self._build_review_body(review, overflow_findings)

        # Build the review payload.
        payload: dict = {
            "event": "COMMENT",
            "body": body,
        }
        if inline_comments:
            payload["comments"] = inline_comments

        # Submit the review.
        self._github_client.post_review(
            owner=pr_context.owner,
            repo=pr_context.repo,
            pr_number=pr_context.pr_number,
            review=payload,
        )
        logger.info(
            "Posted PR review with %d inline comments and %d overflow findings.",
            len(inline_comments),
            len(overflow_findings),
        )

    def _get_diff_files(self, pr_context: PRContext) -> set[str]:
        """Fetch the set of file paths present in the PR diff.

        Args:
            pr_context: PR metadata.

        Returns:
            A set of file paths that were changed in the PR.
        """
        try:
            diff_text = self._github_client.get_pr_diff(
                owner=pr_context.owner,
                repo=pr_context.repo,
                pr_number=pr_context.pr_number,
            )
            # Parse file paths from the unified diff.
            files: set[str] = set()
            for line in diff_text.splitlines():
                if line.startswith("+++ b/"):
                    files.add(line[6:])
            return files
        except Exception:
            logger.warning("Failed to fetch PR diff for file list, using empty set.")
            return set()

    def _build_inline_comments(
        self, findings: list[dict], diff_files: set[str]
    ) -> tuple[list[dict], list[dict]]:
        """Map findings to inline review comments for files in the diff.

        Findings with a file_path present in diff_files become inline comments
        (up to _MAX_INLINE_COMMENTS). Findings that reference files not in the
        diff, or that exceed the inline cap, are returned as overflow.

        Args:
            findings: List of finding dicts from the review.
            diff_files: Set of file paths changed in the PR.

        Returns:
            A tuple of (inline_comments, overflow_findings).
        """
        inline_comments: list[dict] = []
        overflow_findings: list[dict] = []

        for finding in findings:
            file_path = finding.get("file_path", "")

            # Only create inline comments for files actually in the diff.
            if file_path and file_path in diff_files and len(inline_comments) < _MAX_INLINE_COMMENTS:
                comment = {
                    "path": file_path,
                    "line": finding.get("end_line", finding.get("start_line", 1)),
                    "body": self._format_finding_body(finding),
                }
                inline_comments.append(comment)
            else:
                overflow_findings.append(finding)

        return inline_comments, overflow_findings

    def _build_review_body(self, review: dict, overflow_findings: list[dict]) -> str:
        """Build the markdown review body with summary and overflow findings.

        Args:
            review: The full review dict.
            overflow_findings: Findings that couldn't become inline comments.

        Returns:
            Formatted markdown string for the review body.
        """
        parts: list[str] = []

        # Summary section
        summary = review.get("summary", "")
        if summary:
            parts.append("## Review Summary\n")
            parts.append(summary)
            parts.append("")

        # Effort score
        effort_score = review.get("effort_score", 3)
        parts.append(f"**Effort Score:** {effort_score}/5")
        parts.append("")

        # Security concerns
        security_concerns = review.get("security_concerns", "")
        if security_concerns:
            parts.append(f"**Security Concerns:** {security_concerns}")
            parts.append("")

        # Tests assessment
        tests_assessment = review.get("tests_assessment", "")
        if tests_assessment:
            parts.append(f"**Tests Assessment:** {tests_assessment}")
            parts.append("")

        # Overflow findings
        if overflow_findings:
            parts.append("## Additional Findings\n")
            for finding in overflow_findings:
                severity = finding.get("severity", "info")
                category = finding.get("category", "")
                title = finding.get("title", "")
                explanation = finding.get("explanation", "")
                file_path = finding.get("file_path", "")
                start_line = finding.get("start_line", "")
                end_line = finding.get("end_line", "")

                location = ""
                if file_path:
                    location = f" (`{file_path}"
                    if start_line:
                        location += f":{start_line}"
                        if end_line and end_line != start_line:
                            location += f"-{end_line}"
                    location += "`)"

                parts.append(f"- **[{severity}/{category}]** {title}{location}")
                if explanation:
                    parts.append(f"  {explanation}")
                parts.append("")

        return "\n".join(parts).strip()

    def _format_finding_body(self, finding: dict) -> str:
        """Format a single finding as an inline comment body.

        Format: "**[severity/category]** title\n\nexplanation"

        Args:
            finding: A single finding dict.

        Returns:
            Formatted markdown string for the inline comment.
        """
        severity = finding.get("severity", "info")
        category = finding.get("category", "")
        title = finding.get("title", "")
        explanation = finding.get("explanation", "")

        header = f"**[{severity}/{category}]** {title}"
        if explanation:
            return f"{header}\n\n{explanation}"
        return header
