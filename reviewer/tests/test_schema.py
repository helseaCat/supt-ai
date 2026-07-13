"""Property-based and unit tests for Review Schema.

Validates: Requirements 3.1, 3.2, 3.5, 3.6
- Property 6: Review schema validation enforces all constraints
- Property 7: Fallback review always produces a valid schema
"""
from __future__ import annotations

import json

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from lib.schema import (
    Finding,
    ReviewSchema,
    REQUIRED_FIELDS,
    SEVERITY_ORDER,
    VALID_CATEGORIES,
    VALID_SEVERITIES,
    build_fallback_review,
    review_to_dict,
    validate_review,
)

# ─── Strategies ──────────────────────────────────────────────────────────────

severity_st = st.sampled_from(sorted(VALID_SEVERITIES))
category_st = st.sampled_from(sorted(VALID_CATEGORIES))

finding_st = st.fixed_dictionaries({
    "severity": severity_st,
    "category": category_st,
    "file_path": st.text(min_size=1, max_size=50),
    "start_line": st.integers(min_value=1, max_value=1000),
    "end_line": st.integers(min_value=1, max_value=1000),
    "title": st.text(min_size=0, max_size=200),
    "explanation": st.text(min_size=0, max_size=700),
})

valid_review_st = st.fixed_dictionaries({
    "findings": st.lists(finding_st, min_size=0, max_size=10),
    "summary": st.text(min_size=0, max_size=1500),
    "effort_score": st.integers(min_value=1, max_value=5),
    "security_concerns": st.text(min_size=0, max_size=100),
    "tests_assessment": st.text(min_size=0, max_size=100),
})


# ─── Property 6: Review schema validation enforces all constraints ───────────
# Feature: agentic-code-review, Property 6: Review schema validation enforces all constraints


@given(data=valid_review_st)
@settings(max_examples=100)
def test_valid_json_with_all_fields_returns_review_schema(data):
    """For any valid JSON with all required fields and valid enum values,
    validate_review returns a ReviewSchema (not None).

    **Validates: Requirements 3.1, 3.2**
    """
    result = validate_review(json.dumps(data))
    assert result is not None
    assert isinstance(result, ReviewSchema)


@given(data=valid_review_st, field_to_remove=st.sampled_from(REQUIRED_FIELDS))
@settings(max_examples=100)
def test_missing_required_field_returns_none(data, field_to_remove):
    """For any JSON missing a required field, validate_review returns None.

    **Validates: Requirements 3.1**
    """
    data = dict(data)
    del data[field_to_remove]
    assert validate_review(json.dumps(data)) is None


@given(base=valid_review_st, extra_findings=st.lists(finding_st, min_size=51, max_size=70))
@settings(max_examples=100)
def test_findings_capped_at_50(base, extra_findings):
    """For any valid review with > 50 findings, output has exactly 50 findings.

    **Validates: Requirements 3.6**
    """
    data = dict(base)
    data["findings"] = extra_findings
    result = validate_review(json.dumps(data))
    assert result is not None
    assert len(result.findings) == 50


@given(data=st.fixed_dictionaries({
    "findings": st.lists(finding_st, min_size=2, max_size=20),
    "summary": st.text(min_size=0, max_size=100),
    "effort_score": st.integers(min_value=1, max_value=5),
    "security_concerns": st.text(min_size=0, max_size=50),
    "tests_assessment": st.text(min_size=0, max_size=50),
}))
@settings(max_examples=100)
def test_findings_sorted_by_severity(data):
    """For any valid review, findings are sorted by severity
    (critical < warning < info in position).

    **Validates: Requirements 3.6**
    """
    result = validate_review(json.dumps(data))
    assert result is not None
    for i in range(len(result.findings) - 1):
        assert (
            SEVERITY_ORDER[result.findings[i].severity]
            <= SEVERITY_ORDER[result.findings[i + 1].severity]
        )


@given(data=valid_review_st, long_title=st.text(min_size=121, max_size=300))
@settings(max_examples=100)
def test_title_truncated_to_120(data, long_title):
    """For any valid review, title is <= 120 chars.

    **Validates: Requirements 3.2**
    """
    data = dict(data)
    data["findings"] = [{
        "severity": "warning", "category": "bug", "file_path": "test.py",
        "start_line": 1, "end_line": 5, "title": long_title, "explanation": "x",
    }]
    result = validate_review(json.dumps(data))
    assert result is not None and len(result.findings) == 1
    assert len(result.findings[0].title) <= 120


@given(data=valid_review_st, long_explanation=st.text(min_size=501, max_size=1000))
@settings(max_examples=100)
def test_explanation_truncated_to_500(data, long_explanation):
    """For any valid review, explanation is <= 500 chars.

    **Validates: Requirements 3.2**
    """
    data = dict(data)
    data["findings"] = [{
        "severity": "info", "category": "style", "file_path": "app.py",
        "start_line": 1, "end_line": 2, "title": "Test",
        "explanation": long_explanation,
    }]
    result = validate_review(json.dumps(data))
    assert result is not None and len(result.findings) == 1
    assert len(result.findings[0].explanation) <= 500


@given(data=valid_review_st, long_summary=st.text(min_size=1001, max_size=3000))
@settings(max_examples=100)
def test_summary_truncated_to_1000(data, long_summary):
    """For any valid review, summary is <= 1000 chars.

    **Validates: Requirements 3.2**
    """
    data = dict(data)
    data["summary"] = long_summary
    result = validate_review(json.dumps(data))
    assert result is not None
    assert len(result.summary) <= 1000


@given(data=valid_review_st, score=st.integers().filter(lambda x: x < 1 or x > 5))
@settings(max_examples=100)
def test_effort_score_clamped_to_range(data, score):
    """For any valid review, effort_score is in [1, 5].

    **Validates: Requirements 3.2**
    """
    data = dict(data)
    data["effort_score"] = score
    result = validate_review(json.dumps(data))
    assert result is not None
    assert 1 <= result.effort_score <= 5


@given(text=st.text(min_size=1, max_size=500).filter(lambda t: not t.strip().startswith("{")))
@settings(max_examples=100)
def test_non_json_string_returns_none(text):
    """For any non-JSON string, validate_review returns None.

    **Validates: Requirements 3.1**
    """
    # Ensure this truly cannot parse as valid JSON object
    assume(not _is_valid_json_dict(text))
    assert validate_review(text) is None


# ─── Property 7: Fallback review always produces a valid schema ──────────────
# Feature: agentic-code-review, Property 7: Fallback review always produces a valid schema


@given(text=st.text(min_size=0, max_size=5000))
@settings(max_examples=100)
def test_fallback_review_produces_valid_schema(text):
    """For any arbitrary string (including empty, very long, unicode),
    build_fallback_review returns a valid ReviewSchema with correct defaults.

    **Validates: Requirements 3.5**
    """
    result = build_fallback_review(text)
    assert isinstance(result, ReviewSchema)
    assert result.findings == []
    assert len(result.summary) <= 1000
    assert result.summary == text[:1000]
    assert result.effort_score == 3
    assert result.security_concerns == ""
    assert result.tests_assessment == ""
    assert result.parsing_warning is True


@given(text=st.binary(min_size=0, max_size=2000).map(lambda b: b.decode("latin-1")))
@settings(max_examples=100)
def test_fallback_review_handles_binary_looking_text(text):
    """For any binary-looking text decoded as latin-1, build_fallback_review
    returns a valid ReviewSchema.

    **Validates: Requirements 3.5**
    """
    result = build_fallback_review(text)
    assert isinstance(result, ReviewSchema)
    assert result.findings == []
    assert len(result.summary) <= 1000
    assert result.summary == text[:1000]
    assert result.effort_score == 3
    assert result.security_concerns == ""
    assert result.tests_assessment == ""
    assert result.parsing_warning is True


@given(text=st.just(""))
@settings(max_examples=1)
def test_fallback_review_empty_string(text):
    """For empty string input, build_fallback_review returns valid schema.

    **Validates: Requirements 3.5**
    """
    result = build_fallback_review(text)
    assert isinstance(result, ReviewSchema)
    assert result.findings == []
    assert result.summary == ""
    assert result.effort_score == 3
    assert result.parsing_warning is True


@given(text=st.text(min_size=2000, max_size=5000))
@settings(max_examples=100)
def test_fallback_review_very_long_text_truncated(text):
    """For very long text, fallback summary is truncated to 1000 chars.

    **Validates: Requirements 3.5**
    """
    result = build_fallback_review(text)
    assert isinstance(result, ReviewSchema)
    assert len(result.summary) == 1000
    assert result.summary == text[:1000]
    assert result.effort_score == 3
    assert result.parsing_warning is True


# ─── Example-based unit tests ────────────────────────────────────────────────


def test_validate_valid_review():
    """A full valid JSON review parses correctly."""
    data = {
        "findings": [{
            "severity": "critical", "category": "security",
            "file_path": "src/auth.py", "start_line": 10, "end_line": 15,
            "title": "SQL injection vulnerability",
            "explanation": "User input is interpolated directly into query string.",
        }],
        "summary": "Found one critical security issue.",
        "effort_score": 4,
        "security_concerns": "SQL injection in auth module",
        "tests_assessment": "No tests for input sanitization",
    }
    result = validate_review(json.dumps(data))
    assert result is not None
    assert len(result.findings) == 1
    assert result.findings[0].severity == "critical"
    assert result.findings[0].category == "security"
    assert result.findings[0].file_path == "src/auth.py"
    assert result.findings[0].start_line == 10
    assert result.findings[0].end_line == 15
    assert result.findings[0].title == "SQL injection vulnerability"
    assert result.effort_score == 4
    assert result.security_concerns == "SQL injection in auth module"
    assert result.tests_assessment == "No tests for input sanitization"
    assert result.parsing_warning is False


def test_validate_empty_findings():
    """A review with no findings is valid."""
    data = {
        "findings": [],
        "summary": "Clean code, no issues found.",
        "effort_score": 1,
        "security_concerns": "",
        "tests_assessment": "All tests passing",
    }
    result = validate_review(json.dumps(data))
    assert result is not None
    assert isinstance(result, ReviewSchema)
    assert result.findings == []
    assert result.summary == "Clean code, no issues found."
    assert result.effort_score == 1


def test_validate_invalid_json():
    """Garbage string returns None."""
    assert validate_review("not valid json {{{") is None
    assert validate_review("") is None
    assert validate_review("12345") is None
    assert validate_review("null") is None
    assert validate_review("[1, 2, 3]") is None
    assert validate_review("true") is None


def test_validate_missing_field():
    """JSON missing 'summary' returns None."""
    data = {
        "findings": [],
        "effort_score": 3,
        "security_concerns": "",
        "tests_assessment": "",
    }
    # Missing "summary"
    result = validate_review(json.dumps(data))
    assert result is None


def test_validate_invalid_severity_skipped():
    """Finding with bad severity is filtered out."""
    data = {
        "findings": [
            {
                "severity": "extreme",  # invalid
                "category": "bug",
                "file_path": "foo.py",
                "start_line": 1, "end_line": 5,
                "title": "Bad finding", "explanation": "Should be dropped",
            },
            {
                "severity": "warning",  # valid
                "category": "style",
                "file_path": "bar.py",
                "start_line": 10, "end_line": 12,
                "title": "Good finding", "explanation": "Should stay",
            },
        ],
        "summary": "Mixed findings.",
        "effort_score": 2,
        "security_concerns": "",
        "tests_assessment": "",
    }
    result = validate_review(json.dumps(data))
    assert result is not None
    assert len(result.findings) == 1
    assert result.findings[0].severity == "warning"
    assert result.findings[0].title == "Good finding"


def test_validate_invalid_category_skipped():
    """Finding with bad category is filtered out."""
    data = {
        "findings": [
            {
                "severity": "info",
                "category": "unknown_category",  # invalid
                "file_path": "foo.py",
                "start_line": 1, "end_line": 5,
                "title": "Bad category", "explanation": "Should be dropped",
            },
            {
                "severity": "info",
                "category": "documentation",  # valid
                "file_path": "bar.py",
                "start_line": 1, "end_line": 2,
                "title": "Good category", "explanation": "Should stay",
            },
        ],
        "summary": "Category filtering.",
        "effort_score": 3,
        "security_concerns": "",
        "tests_assessment": "",
    }
    result = validate_review(json.dumps(data))
    assert result is not None
    assert len(result.findings) == 1
    assert result.findings[0].category == "documentation"


def test_review_to_dict_roundtrip():
    """validate_review then review_to_dict produces dict matching input structure."""
    data = {
        "findings": [
            {"severity": "warning", "category": "performance", "file_path": "app.py",
             "start_line": 1, "end_line": 3, "title": "Inefficient loop",
             "explanation": "O(n^2) nested iteration."},
            {"severity": "info", "category": "style", "file_path": "utils.py",
             "start_line": 20, "end_line": 20, "title": "Unused import",
             "explanation": "os module is imported but never used."},
        ],
        "summary": "Minor issues found.",
        "effort_score": 2,
        "security_concerns": "",
        "tests_assessment": "Good coverage",
    }
    first = validate_review(json.dumps(data))
    assert first is not None

    output_dict = review_to_dict(first)
    second = validate_review(json.dumps(output_dict))
    assert second is not None

    # Findings should be preserved (both valid)
    assert len(first.findings) == len(second.findings)
    assert first.summary == second.summary
    assert first.effort_score == second.effort_score
    assert first.security_concerns == second.security_concerns
    assert first.tests_assessment == second.tests_assessment

    for f1, f2 in zip(first.findings, second.findings):
        assert f1.severity == f2.severity
        assert f1.category == f2.category
        assert f1.file_path == f2.file_path
        assert f1.start_line == f2.start_line
        assert f1.end_line == f2.end_line
        assert f1.title == f2.title
        assert f1.explanation == f2.explanation


def test_review_to_dict_structure():
    """review_to_dict produces correct top-level keys."""
    schema = ReviewSchema(
        findings=[],
        summary="Test summary",
        effort_score=3,
        security_concerns="",
        tests_assessment="",
        parsing_warning=False,
    )
    d = review_to_dict(schema)
    assert set(d.keys()) == {
        "findings", "summary", "effort_score",
        "security_concerns", "tests_assessment", "parsing_warning",
    }
    assert d["findings"] == []
    assert d["summary"] == "Test summary"
    assert d["effort_score"] == 3
    assert d["parsing_warning"] is False


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _is_valid_json_dict(text: str) -> bool:
    """Check if text parses as a JSON dict."""
    try:
        result = json.loads(text)
        return isinstance(result, dict)
    except (json.JSONDecodeError, ValueError):
        return False
