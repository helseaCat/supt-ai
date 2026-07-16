"""GitHub PR Review output adapter.

Posts review findings as inline comments on the PR diff via the GitHub Pull
Request Review API, with overflow findings included in the review body.
"""

from __future__ import annotations

import logging
import re

from lib.github_client import GitHubClient
from lib.outputs.base import OutputDestination
from lib.review_engine import PRContext

logger = logging.getLogger(__name__)

# Maximum number of inline comments per review (GitHub API limit consideration).
_MAX_INLINE_COMMENTS = 50

# Regex to parse unified diff hunk headers: @@ -old_start,old_count +new_start,new_count @@
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitHubPROutput(OutputDestination):
    """Posts review as inline PR comments via the GitHub Pull Request Review API.

    Maps findings with valid file/line references to inline comments (up to 50).
    Only places inline comments on lines that exist within diff hunks.
    Remaining findings and the overall summary go into the review body text.
    Submits with event="COMMENT" (never APPROVE or REQUEST_CHANGES).
    """

    def __init__(self, github_client: GitHubClient) -> None:
        self._github_client = github_client

    @property
    def name(self) -> str:
        return "github"

    def send(self, pr_context: PRContext, review: dict) -> None:
        """Post the review to the GitHub PR."""
        # Parse the diff to get reviewable lines per file.
        diff_lines_map = self._get_diff_lines_map(pr_context)

        findings = review.get("findings", [])
        inline_comments, overflow_findings = self._build_inline_comments(
            findings, diff_lines_map
        )
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

    def _get_diff_lines_map(self, pr_context: PRContext) -> dict[str, set[int]]:
        """Parse the PR diff to build a map of reviewable lines per file.

        Only lines that appear on the "new" side of diff hunks (added or
        context lines) can receive inline comments via the GitHub API.

        Returns:
            A dict mapping file paths to sets of line numbers that are
            valid targets for inline comments. Empty dict on failure.
        """
        try:
            diff_text = self._github_client.get_pr_diff(
                owner=pr_context.owner,
                repo=pr_context.repo,
                pr_number=pr_context.pr_number,
            )
        except Exception:
            logger.warning("Failed to fetch PR diff, all findings go to review body.")
            return {}

        lines_map: dict[str, set[int]] = {}
        current_file: str | None = None
        current_line = 0

        for line in diff_text.splitlines():
            # Detect file change. Skip deleted files (+++ /dev/null).
            if line.startswith("+++ "):
                if line == "+++ /dev/null":
                    current_file = None
                    continue
                current_file = line[6:]  # strip "+++ b/"
                if current_file not in lines_map:
                    lines_map[current_file] = set()
                continue

            # Detect hunk header.
            hunk_match = _HUNK_HEADER_RE.match(line)
            if hunk_match:
                new_start = int(hunk_match.group(1))
                # Pure-deletion hunks have +0,0; don't set current_line to 0.
                if new_start > 0:
                    current_line = new_start
                continue

            if current_file is None:
                continue

            # Lines starting with '-' are deletions (old file only), skip.
            if line.startswith("-"):
                continue

            # Lines starting with '+' or ' ' (context) are on the new side.
            if line.startswith("+") or line.startswith(" "):
                lines_map[current_file].add(current_line)
                current_line += 1
            elif not line.startswith("\\"):
                # Any other line (e.g. "\ No newline at end of file") skip.
                pass

        return lines_map

    def _build_inline_comments(
        self, findings: list[dict], diff_lines_map: dict[str, set[int]]
    ) -> tuple[list[dict], list[dict]]:
        """Map findings to inline review comments for lines in diff hunks.

        A finding becomes an inline comment only if:
        1. Its file_path is in the diff
        2. Its end_line (or start_line) is within a diff hunk
        3. The inline comment cap hasn't been reached

        Everything else goes to overflow.
        """
        inline_comments: list[dict] = []
        overflow_findings: list[dict] = []

        for finding in findings:
            file_path = finding.get("file_path", "")
            end_line = finding.get("end_line", 0)
            start_line = finding.get("start_line", 0)
            target_line = end_line or start_line

            reviewable_lines = diff_lines_map.get(file_path, set())

            if (
                file_path
                and target_line
                and target_line in reviewable_lines
                and len(inline_comments) < _MAX_INLINE_COMMENTS
            ):
                comment = {
                    "path": file_path,
                    "line": target_line,
                    "body": self._format_finding_body(finding),
                }
                inline_comments.append(comment)
            else:
                overflow_findings.append(finding)

        return inline_comments, overflow_findings

    def _build_review_body(self, review: dict, overflow_findings: list[dict]) -> str:
        """Build the markdown review body with summary and overflow findings."""
        parts: list[str] = []

        summary = review.get("summary", "")
        if summary:
            parts.append("## Review Summary\n")
            parts.append(summary)
            parts.append("")

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
        """Format a single finding as an inline comment body."""
        severity = finding.get("severity", "info")
        category = finding.get("category", "")
        title = finding.get("title", "")
        explanation = finding.get("explanation", "")

        header = f"**[{severity}/{category}]** {title}"
        if explanation:
            return f"{header}\n\n{explanation}"
        return header
