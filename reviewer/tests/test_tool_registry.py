"""Property-based and unit tests for ToolRegistry.

Validates: Requirements 2.6, 2.8, 2.9
- Property 3: Line range requests capped at 200 lines
- Property 4: Invalid tool inputs produce descriptive error results
- Property 5: Content truncation at 100K characters
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lib.tool_registry import ToolRegistry, ToolResult, _truncate, MAX_CONTENT_LENGTH


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_github():
    """Create a mock GitHubClient."""
    return MagicMock()


@pytest.fixture()
def registry(mock_github) -> ToolRegistry:
    """Create a ToolRegistry with mocked GitHub client."""
    return ToolRegistry(
        github_client=mock_github,
        owner="test-owner",
        repo="test-repo",
        pr_number=42,
        head_ref="feature-branch",
    )


# ─── Example-based unit tests ────────────────────────────────────────────────


def test_get_tool_definitions_returns_four_tools(registry):
    """Tool definitions expose 4 tools (get_pr_diff and search_code excluded)."""
    defs = registry.get_tool_definitions()
    assert len(defs) == 4
    names = {d["function"]["name"] for d in defs}
    assert names == {
        "get_file_contents",
        "list_directory",
        "get_commit_info",
        "get_file_at_line",
    }
    assert "get_pr_diff" not in names
    assert "search_code" not in names


def test_execute_unknown_tool(registry):
    """Unknown tool name returns an error ToolResult."""
    result = registry.execute("nonexistent_tool", {})
    assert result.is_error is True
    assert "Unknown tool" in result.content
    assert "nonexistent_tool" in result.content


def test_get_pr_diff_success(mock_github, registry):
    """get_pr_diff returns diff content from GitHub client."""
    mock_github.get_pr_diff.return_value = "diff --git a/f.py b/f.py\n+hello"
    result = registry.execute("get_pr_diff", {})
    assert result.is_error is False
    assert "diff --git" in result.content
    mock_github.get_pr_diff.assert_called_once_with("test-owner", "test-repo", 42)


# ─── Property 3: Line range capped at 200 lines ─────────────────────────────
# Feature: agentic-code-review, Property 3: Line range requests capped at 200 lines


@given(
    start=st.integers(min_value=1, max_value=10000),
    extra=st.integers(min_value=201, max_value=5000),
)
@settings(max_examples=100)
def test_line_range_exceeding_200_returns_error(start, extra):
    """**Validates: Requirements 2.6**

    For any range > 200 lines, execute returns is_error=True mentioning 200.
    """
    end = start + extra - 1  # ensures (end - start + 1) > 200
    mock_gh = MagicMock()
    reg = ToolRegistry(mock_gh, "o", "r", 1, head_ref="main")
    result = reg.execute("get_file_at_line", {"path": "f.py", "start_line": start, "end_line": end})
    assert result.is_error is True
    assert "200" in result.content


@given(
    start=st.integers(min_value=1, max_value=500),
    size=st.integers(min_value=1, max_value=200),
)
@settings(max_examples=100)
def test_line_range_within_200_dispatches_to_client(start, size):
    """**Validates: Requirements 2.6**

    For any valid range <= 200 lines with a file that has enough lines, no error.
    """
    end = start + size - 1
    # Build a file with enough lines
    file_content = "\n".join(f"line {i}" for i in range(1, end + 10))
    mock_gh = MagicMock()
    mock_gh.get_file_contents.return_value = file_content
    reg = ToolRegistry(mock_gh, "o", "r", 1, head_ref="main")
    result = reg.execute("get_file_at_line", {"path": "f.py", "start_line": start, "end_line": end})
    assert result.is_error is False
    mock_gh.get_file_contents.assert_called_once()


# ─── Property 4: Invalid tool inputs produce descriptive errors ──────────────
# Feature: agentic-code-review, Property 4: Invalid tool inputs produce descriptive error results


@given(path=st.from_regex(r"^[\s]*$", fullmatch=True).filter(lambda s: len(s) <= 10))
@settings(max_examples=100)
def test_empty_path_in_get_file_contents_returns_error(path):
    """**Validates: Requirements 2.8**

    Empty/whitespace-only path in get_file_contents produces is_error=True.
    """
    mock_gh = MagicMock()
    reg = ToolRegistry(mock_gh, "o", "r", 1)
    result = reg.execute("get_file_contents", {"path": path})
    assert result.is_error is True
    assert "path" in result.content.lower()


@given(query=st.text(min_size=257, max_size=500))
@settings(max_examples=100)
def test_long_query_in_search_code_returns_error(query):
    """**Validates: Requirements 2.8**

    Query > 256 chars in search_code produces is_error=True mentioning 256.
    """
    mock_gh = MagicMock()
    reg = ToolRegistry(mock_gh, "o", "r", 1)
    result = reg.execute("search_code", {"query": query})
    assert result.is_error is True
    assert "256" in result.content


@given(start_line=st.integers(max_value=0))
@settings(max_examples=100)
def test_start_line_below_1_returns_error(start_line):
    """**Validates: Requirements 2.8**

    start_line < 1 produces is_error=True.
    """
    mock_gh = MagicMock()
    reg = ToolRegistry(mock_gh, "o", "r", 1)
    result = reg.execute("get_file_at_line", {"path": "f.py", "start_line": start_line, "end_line": 10})
    assert result.is_error is True


@given(
    start=st.integers(min_value=2, max_value=1000),
    offset=st.integers(min_value=1, max_value=500),
)
@settings(max_examples=100)
def test_end_line_less_than_start_returns_error(start, offset):
    """**Validates: Requirements 2.8**

    end_line < start_line produces is_error=True.
    """
    end = start - offset  # guaranteed end < start
    mock_gh = MagicMock()
    reg = ToolRegistry(mock_gh, "o", "r", 1)
    result = reg.execute("get_file_at_line", {"path": "f.py", "start_line": start, "end_line": end})
    assert result.is_error is True


@given(name=st.text(min_size=1, max_size=50).filter(
    lambda s: s not in ("get_file_contents", "search_code", "list_directory",
                        "get_pr_diff", "get_commit_info", "get_file_at_line")
))
@settings(max_examples=100)
def test_unknown_tool_name_returns_error(name):
    """**Validates: Requirements 2.8**

    Unknown tool name produces is_error=True.
    """
    mock_gh = MagicMock()
    reg = ToolRegistry(mock_gh, "o", "r", 1)
    result = reg.execute(name, {})
    assert result.is_error is True


# ─── Property 5: Content truncation at 100K characters ───────────────────────
# Feature: agentic-code-review, Property 5: Content truncation at 100K characters


@given(extra=st.integers(min_value=1, max_value=50000))
@settings(max_examples=100)
def test_truncate_long_content(extra):
    """**Validates: Requirements 2.9**

    Strings > 100K chars are truncated to exactly 100K + truncation indicator.
    """
    content = "x" * (MAX_CONTENT_LENGTH + extra)
    result = _truncate(content)
    assert result.startswith("x" * MAX_CONTENT_LENGTH)
    assert len(result) > MAX_CONTENT_LENGTH
    assert "[Content truncated" in result
    # First 100K chars preserved exactly
    assert result[:MAX_CONTENT_LENGTH] == "x" * MAX_CONTENT_LENGTH


@given(size=st.integers(min_value=0, max_value=MAX_CONTENT_LENGTH))
@settings(max_examples=100)
def test_truncate_short_content_unchanged(size):
    """**Validates: Requirements 2.9**

    Strings <= 100K chars are returned unchanged.
    """
    content = "a" * size
    result = _truncate(content)
    assert result == content
