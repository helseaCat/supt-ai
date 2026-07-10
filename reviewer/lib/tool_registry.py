"""Tool definitions and dispatch for the agentic review loop.

Exposes six tools (get_file_contents, search_code, list_directory, get_pr_diff,
get_commit_info, get_file_at_line) in OpenAI function-calling format and handles
input validation, execution, content truncation, and error wrapping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from lib.github_client import GitHubClient

logger = logging.getLogger(__name__)

# Truncation at 100K characters for large content (Requirements 2.9).
MAX_CONTENT_LENGTH = 100_000
_TRUNCATION_INDICATOR = "\n\n[Content truncated — exceeded 100,000 character limit]"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    """Result of executing a tool call."""

    content: str
    is_error: bool = False


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_file_contents",
            "description": "Fetch the full content of a file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git ref (branch, tag, SHA). Defaults to PR head.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for code in the repository. Returns up to 10 matching snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (max 256 chars)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and subdirectories at a given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to repo root",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git ref. Defaults to PR head.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pr_diff",
            "description": "Get the full unified diff for the pull request being reviewed.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_commit_info",
            "description": "Get details about a specific commit: message, author, timestamp, changed files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sha": {
                        "type": "string",
                        "description": "Full or abbreviated commit SHA",
                    },
                },
                "required": ["sha"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_at_line",
            "description": "Get a specific line range from a file (max 200 lines per request).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line number (1-indexed)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line number (inclusive)",
                    },
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Defines and dispatches tools available to the LLM during the review loop."""

    def __init__(
        self,
        github_client: GitHubClient,
        owner: str,
        repo: str,
        pr_number: int,
        head_ref: str | None = None,
    ) -> None:
        """Initialize the tool registry.

        Args:
            github_client: Authenticated GitHub API client.
            owner: Repository owner.
            repo: Repository name.
            pr_number: Pull request number being reviewed.
            head_ref: Default git ref for file lookups (PR head branch).
        """
        self._github = github_client
        self._owner = owner
        self._repo = repo
        self._pr_number = pr_number
        self._head_ref = head_ref

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict]:
        """Return OpenAI-format function definitions for all available tools."""
        return _TOOL_DEFINITIONS

    def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        """Dispatch a tool call by name. Returns ToolResult (never raises).

        Validates inputs before dispatching. Unknown tool names and invalid
        inputs produce ToolResult with is_error=True.
        """
        dispatch = {
            "get_file_contents": self._execute_get_file_contents,
            "search_code": self._execute_search_code,
            "list_directory": self._execute_list_directory,
            "get_pr_diff": self._execute_get_pr_diff,
            "get_commit_info": self._execute_get_commit_info,
            "get_file_at_line": self._execute_get_file_at_line,
        }

        handler = dispatch.get(tool_name)
        if handler is None:
            return ToolResult(
                content=f"Unknown tool: '{tool_name}'. Available tools: {', '.join(dispatch.keys())}",
                is_error=True,
            )

        try:
            return handler(arguments)
        except Exception as exc:
            logger.exception("Unexpected error executing tool '%s'", tool_name)
            return ToolResult(
                content=f"Internal error executing '{tool_name}': {type(exc).__name__}: {exc}",
                is_error=True,
            )

    # ------------------------------------------------------------------
    # Tool implementations (private)
    # ------------------------------------------------------------------

    def _execute_get_file_contents(self, arguments: dict) -> ToolResult:
        """Fetch file content, truncate at MAX_CONTENT_LENGTH chars."""
        path = arguments.get("path", "")
        if not path or not path.strip():
            return ToolResult(
                content="Invalid input: 'path' is required and cannot be empty.",
                is_error=True,
            )

        ref = arguments.get("ref") or self._head_ref

        try:
            content = self._github.get_file_contents(self._owner, self._repo, path.strip(), ref)
        except Exception as exc:
            return ToolResult(
                content=f"Error fetching file '{path}': {type(exc).__name__}: {exc}",
                is_error=True,
            )

        return ToolResult(content=_truncate(content))

    def _execute_search_code(self, arguments: dict) -> ToolResult:
        """Search code in the repository, return up to 10 matches."""
        query = arguments.get("query", "")
        if not query or not query.strip():
            return ToolResult(
                content="Invalid input: 'query' is required and cannot be empty.",
                is_error=True,
            )

        query = query.strip()
        if len(query) > 256:
            return ToolResult(
                content=f"Invalid input: 'query' exceeds maximum length of 256 characters (got {len(query)}).",
                is_error=True,
            )

        try:
            results = self._github.search_code(self._owner, self._repo, query)
        except Exception as exc:
            return ToolResult(
                content=f"Error searching code: {type(exc).__name__}: {exc}",
                is_error=True,
            )

        # Format up to 10 results with path and snippets.
        results = results[:10]
        if not results:
            return ToolResult(content="No results found.")

        formatted_parts: list[str] = []
        for item in results:
            file_path = item.get("path", "<unknown>")
            text_matches = item.get("text_matches", [])
            snippets = []
            for match in text_matches:
                fragment = match.get("fragment", "")
                if fragment:
                    snippets.append(fragment)
            snippet_text = "\n".join(snippets) if snippets else "(no snippet available)"
            formatted_parts.append(f"--- {file_path} ---\n{snippet_text}")

        return ToolResult(content="\n\n".join(formatted_parts))

    def _execute_list_directory(self, arguments: dict) -> ToolResult:
        """List directory contents formatted as a name list."""
        path = arguments.get("path", "")
        if not path or not path.strip():
            return ToolResult(
                content="Invalid input: 'path' is required and cannot be empty.",
                is_error=True,
            )

        ref = arguments.get("ref") or self._head_ref

        try:
            items = self._github.list_directory(self._owner, self._repo, path.strip(), ref)
        except Exception as exc:
            return ToolResult(
                content=f"Error listing directory '{path}': {type(exc).__name__}: {exc}",
                is_error=True,
            )

        if not items:
            return ToolResult(content=f"Directory '{path}' is empty or does not exist.")

        names: list[str] = []
        for item in items:
            name = item.get("name", "<unknown>")
            item_type = item.get("type", "file")
            suffix = "/" if item_type == "dir" else ""
            names.append(f"{name}{suffix}")

        return ToolResult(content="\n".join(names))

    def _execute_get_pr_diff(self, arguments: dict) -> ToolResult:
        """Get the full unified diff for the PR, truncate at MAX_CONTENT_LENGTH."""
        try:
            diff = self._github.get_pr_diff(self._owner, self._repo, self._pr_number)
        except Exception as exc:
            return ToolResult(
                content=f"Error fetching PR diff: {type(exc).__name__}: {exc}",
                is_error=True,
            )

        return ToolResult(content=_truncate(diff))

    def _execute_get_commit_info(self, arguments: dict) -> ToolResult:
        """Get commit message, author, timestamp, and changed files."""
        sha = arguments.get("sha", "")
        if not sha or not sha.strip():
            return ToolResult(
                content="Invalid input: 'sha' is required and cannot be empty.",
                is_error=True,
            )

        try:
            commit_data = self._github.get_commit(self._owner, self._repo, sha.strip())
        except Exception as exc:
            return ToolResult(
                content=f"Error fetching commit '{sha}': {type(exc).__name__}: {exc}",
                is_error=True,
            )

        # Format commit information.
        commit_obj = commit_data.get("commit", {})
        message = commit_obj.get("message", "(no message)")
        author_info = commit_obj.get("author", {})
        author_name = author_info.get("name", "Unknown")
        author_date = author_info.get("date", "Unknown")

        files = commit_data.get("files", [])
        file_list: list[str] = []
        for f in files:
            filename = f.get("filename", "")
            status = f.get("status", "")
            file_list.append(f"  {status}: {filename}")

        parts = [
            f"Commit: {sha.strip()}",
            f"Author: {author_name}",
            f"Date: {author_date}",
            f"Message: {message}",
            f"Files changed ({len(files)}):",
        ]
        if file_list:
            parts.append("\n".join(file_list))
        else:
            parts.append("  (none)")

        return ToolResult(content="\n".join(parts))

    def _execute_get_file_at_line(self, arguments: dict) -> ToolResult:
        """Get a specific line range from a file (max 200 lines)."""
        path = arguments.get("path", "")
        if not path or not path.strip():
            return ToolResult(
                content="Invalid input: 'path' is required and cannot be empty.",
                is_error=True,
            )

        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")

        if start_line is None or end_line is None:
            return ToolResult(
                content="Invalid input: 'start_line' and 'end_line' are required.",
                is_error=True,
            )

        # Coerce to int for safety.
        try:
            start_line = int(start_line)
            end_line = int(end_line)
        except (TypeError, ValueError):
            return ToolResult(
                content="Invalid input: 'start_line' and 'end_line' must be integers.",
                is_error=True,
            )

        if start_line < 1:
            return ToolResult(
                content=f"Invalid input: 'start_line' must be >= 1 (got {start_line}).",
                is_error=True,
            )

        if end_line < start_line:
            return ToolResult(
                content=f"Invalid input: 'end_line' ({end_line}) must be >= 'start_line' ({start_line}).",
                is_error=True,
            )

        line_count = end_line - start_line + 1
        if line_count > 200:
            return ToolResult(
                content=f"Invalid input: requested range of {line_count} lines exceeds maximum of 200 lines.",
                is_error=True,
            )

        # Fetch the full file and extract the requested range.
        ref = self._head_ref
        try:
            content = self._github.get_file_contents(self._owner, self._repo, path.strip(), ref)
        except Exception as exc:
            return ToolResult(
                content=f"Error fetching file '{path}': {type(exc).__name__}: {exc}",
                is_error=True,
            )

        lines = content.split("\n")
        total_lines = len(lines)

        if start_line > total_lines:
            return ToolResult(
                content=f"Invalid input: 'start_line' ({start_line}) exceeds file length ({total_lines} lines).",
                is_error=True,
            )

        # Clamp end_line to file length.
        actual_end = min(end_line, total_lines)
        selected = lines[start_line - 1 : actual_end]

        # Format with line numbers.
        numbered_lines: list[str] = []
        for i, line in enumerate(selected, start=start_line):
            numbered_lines.append(f"{i:>4} | {line}")

        return ToolResult(content="\n".join(numbered_lines))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(content: str) -> str:
    """Truncate content at MAX_CONTENT_LENGTH and append indicator if needed."""
    if len(content) > MAX_CONTENT_LENGTH:
        return content[:MAX_CONTENT_LENGTH] + _TRUNCATION_INDICATOR
    return content
