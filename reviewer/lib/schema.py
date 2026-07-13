"""Review Schema validation and utilities.

Defines the structured output format for agentic code reviews and provides
functions to validate LLM output, build fallback reviews, and serialize
review objects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


# --- Constants ---

REQUIRED_FIELDS = ["findings", "summary", "effort_score", "security_concerns", "tests_assessment"]

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

VALID_SEVERITIES = {"critical", "warning", "info"}

VALID_CATEGORIES = {
    "bug",
    "security",
    "performance",
    "maintainability",
    "style",
    "documentation",
}


# --- Dataclasses ---


@dataclass
class Finding:
    """A single review finding."""

    severity: str  # "critical" | "warning" | "info"
    category: str  # "bug" | "security" | "performance" | "maintainability" | "style" | "documentation"
    file_path: str
    start_line: int
    end_line: int
    title: str  # max 120 chars
    explanation: str  # max 500 chars


@dataclass
class ReviewSchema:
    """Validated review output structure."""

    findings: list[Finding] = field(default_factory=list)  # max 50, ordered by severity
    summary: str = ""  # max 1000 chars
    effort_score: int = 3  # 1-5
    security_concerns: str = ""  # empty string if none
    tests_assessment: str = ""  # empty string if none
    parsing_warning: bool = False  # True if LLM failed to produce valid JSON


# --- Public Functions ---


def validate_review(raw_json: str) -> ReviewSchema | None:
    """Parse and validate a JSON string against the Review Schema.

    Returns a ReviewSchema if valid, None if parsing fails or required fields
    are missing. Truncates/caps fields to their limits. Sorts and limits
    findings to 50.
    """
    # Parse JSON
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    # Check required fields
    for field_name in REQUIRED_FIELDS:
        if field_name not in data:
            return None

    # Validate and build findings
    raw_findings = data.get("findings", [])
    if not isinstance(raw_findings, list):
        return None

    findings: list[Finding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue

        severity = item.get("severity", "")
        category = item.get("category", "")

        if severity not in VALID_SEVERITIES:
            continue
        if category not in VALID_CATEGORIES:
            continue

        title = str(item.get("title", ""))[:120]
        explanation = str(item.get("explanation", ""))[:500]
        file_path = str(item.get("file_path", ""))
        start_line = _to_int(item.get("start_line", 0))
        end_line = _to_int(item.get("end_line", 0))

        findings.append(
            Finding(
                severity=severity,
                category=category,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                title=title,
                explanation=explanation,
            )
        )

    # Sort findings by severity (critical first, then warning, then info)
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 99))

    # Cap at 50 findings
    findings = findings[:50]

    # Validate and truncate summary
    summary = str(data.get("summary", ""))[:1000]

    # Validate effort_score: must be int in [1, 5], clamp if out of range
    effort_score = _clamp_effort_score(data.get("effort_score"))

    # String fields (empty string if none)
    security_concerns = str(data.get("security_concerns", "") or "")
    tests_assessment = str(data.get("tests_assessment", "") or "")

    # parsing_warning from data (default False)
    parsing_warning = bool(data.get("parsing_warning", False))

    return ReviewSchema(
        findings=findings,
        summary=summary,
        effort_score=effort_score,
        security_concerns=security_concerns,
        tests_assessment=tests_assessment,
        parsing_warning=parsing_warning,
    )


def build_fallback_review(raw_text: str) -> ReviewSchema:
    """Build a minimal valid ReviewSchema from raw text when validation fails.

    Used when the LLM fails to produce conformant output after the correction
    turn. Wraps the raw text in a valid envelope with parsing_warning=True.
    """
    return ReviewSchema(
        findings=[],
        summary=str(raw_text)[:1000],
        effort_score=3,
        security_concerns="",
        tests_assessment="",
        parsing_warning=True,
    )


def review_to_dict(review: ReviewSchema) -> dict:
    """Convert a ReviewSchema to a plain dict suitable for JSON serialization."""
    return {
        "findings": [
            {
                "severity": f.severity,
                "category": f.category,
                "file_path": f.file_path,
                "start_line": f.start_line,
                "end_line": f.end_line,
                "title": f.title,
                "explanation": f.explanation,
            }
            for f in review.findings
        ],
        "summary": review.summary,
        "effort_score": review.effort_score,
        "security_concerns": review.security_concerns,
        "tests_assessment": review.tests_assessment,
        "parsing_warning": review.parsing_warning,
    }


# --- Private Helpers ---


def _to_int(value) -> int:
    """Safely convert a value to int, defaulting to 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clamp_effort_score(value) -> int:
    """Validate and clamp effort_score to [1, 5]."""
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 3  # default if not convertible
    return max(1, min(5, score))
