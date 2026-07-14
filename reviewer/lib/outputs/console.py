"""Console output adapter.

Logs review summary information at INFO level. Useful for local development
and testing where Discord/GitHub outputs are not configured.
"""

from __future__ import annotations

import logging

from lib.outputs.base import OutputDestination
from lib.review_engine import PRContext

logger = logging.getLogger(__name__)


class ConsoleOutput(OutputDestination):
    """Logs review results to the console at INFO level.

    Provides a simple output destination for local development and testing
    that shows the review summary, finding count, and effort score.
    """

    @property
    def name(self) -> str:
        """Return the destination identifier."""
        return "console"

    def send(self, pr_context: PRContext, review: dict) -> None:
        """Log the review summary to the console.

        Args:
            pr_context: PR metadata.
            review: Validated review dict conforming to ReviewSchema.
        """
        findings = review.get("findings", [])
        summary = review.get("summary", "")
        effort_score = review.get("effort_score", 3)
        security_concerns = review.get("security_concerns", "")

        finding_count = len(findings)
        severity_counts: dict[str, int] = {}
        for finding in findings:
            sev = finding.get("severity", "info")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        logger.info(
            "Review complete for %s/%s PR #%d (%s)",
            pr_context.owner,
            pr_context.repo,
            pr_context.pr_number,
            pr_context.title,
        )
        logger.info("  Summary: %s", summary[:200] if summary else "(no summary)")
        logger.info("  Findings: %d total %s", finding_count, dict(severity_counts) or "")
        logger.info("  Effort Score: %d/5", effort_score)

        if security_concerns:
            logger.info("  Security Concerns: %s", security_concerns[:200])
