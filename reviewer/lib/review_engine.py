"""Agentic review loop orchestration.

Manages the conversation between the LLM (xAI Grok via the OpenAI SDK) and
the Tool Registry, enforcing iteration budgets, timeout constraints, and
retry policies. Produces a validated ReviewSchema output.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable

import openai
from openai import OpenAI
from openai.types.chat import ChatCompletion

from lib.config import Settings
from lib.github_client import GitHubClient
from lib.schema import build_fallback_review, review_to_dict, validate_review
from lib.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class PRContext:
    """Metadata about the pull request being reviewed."""

    pr_url: str
    owner: str
    repo: str
    pr_number: int
    title: str
    author: str
    branch: str


@dataclass
class ReviewResult:
    """Output of a completed review."""

    review: dict  # Validated Review Schema dict
    iterations: int  # Number of agent loop iterations
    tool_calls: int  # Total tool calls made
    tokens_prompt: int  # Total prompt tokens consumed
    tokens_completion: int  # Total completion tokens consumed
    duration_ms: int  # Wall-clock time in milliseconds


# ---------------------------------------------------------------------------
# ReviewEngine
# ---------------------------------------------------------------------------


class ReviewEngine:
    """Orchestrates the agentic review loop.

    The engine maintains a conversation with the LLM, dispatching tool calls
    via the ToolRegistry until the LLM produces a final structured review or
    the iteration/time budget is exhausted.
    """

    def __init__(
        self,
        settings: Settings,
        github_client: GitHubClient,
        tool_registry: ToolRegistry,
        remaining_time_ms: Callable[[], int],
    ) -> None:
        """Initialize the review engine.

        Args:
            settings: Application configuration.
            github_client: Authenticated GitHub API client.
            tool_registry: Registry of tools available to the LLM.
            remaining_time_ms: Callable returning the remaining Lambda execution
                time in milliseconds. Used for timeout management.
        """
        self._settings = settings
        self._github_client = github_client
        self._tool_registry = tool_registry
        self._remaining_time_ms = remaining_time_ms

        # Create the OpenAI client once (reused for all calls in this review).
        self._client = OpenAI(
            base_url=settings.base_url,
            api_key=settings.xai_api_key,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, pr_context: PRContext, diff: str) -> ReviewResult:
        """Execute the agent loop until final review or budget exhaustion.

        1. Build initial messages (system prompt + PR context + diff)
        2. Loop: call LLM -> handle tool calls or final response
        3. Validate output against Review Schema
        4. Return ReviewResult

        Args:
            pr_context: Metadata about the PR being reviewed.
            diff: The unified diff content of the PR.

        Returns:
            A ReviewResult containing the validated review and metrics.
        """
        start_time = time.perf_counter()
        total_tool_calls = 0
        total_tokens_prompt = 0
        total_tokens_completion = 0

        # Build initial conversation
        system_prompt = self._build_system_prompt(pr_context)
        user_message = self._build_user_message(pr_context, diff)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        tools = self._tool_registry.get_tool_definitions()
        iteration = 0
        iteration_budget = self._settings.iteration_budget

        while iteration < iteration_budget:
            iteration += 1

            # --- Timeout check before LLM call ---
            if self._remaining_time_ms() < 10_000:
                logger.warning(
                    "Timeout approaching (%d ms remaining), forcing final turn.",
                    self._remaining_time_ms(),
                )
                review_dict = self._handle_timeout(messages, total_tokens_prompt, total_tokens_completion)
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                return ReviewResult(
                    review=review_dict,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                    tokens_prompt=total_tokens_prompt,
                    tokens_completion=total_tokens_completion,
                    duration_ms=duration_ms,
                )

            # --- Call LLM ---
            try:
                response = self._call_llm(messages, tools)
            except Exception as exc:
                logger.error("LLM call failed on iteration %d: %s", iteration, exc)
                # If we have enough time, try a final turn without tools
                if self._remaining_time_ms() >= 10_000:
                    review_dict = self._handle_timeout(messages, total_tokens_prompt, total_tokens_completion)
                else:
                    review_dict = review_to_dict(build_fallback_review(f"Review failed: {exc}"))
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                return ReviewResult(
                    review=review_dict,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                    tokens_prompt=total_tokens_prompt,
                    tokens_completion=total_tokens_completion,
                    duration_ms=duration_ms,
                )

            # --- Track token usage ---
            if response.usage:
                total_tokens_prompt += response.usage.prompt_tokens
                total_tokens_completion += response.usage.completion_tokens

            # --- Process response ---
            choice = response.choices[0]
            message = choice.message

            # Case 1: LLM requested tool calls
            if message.tool_calls:
                # Append assistant message (with tool_calls) to conversation
                messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                })

                # Execute tool calls (capped at max_tool_calls_per_turn)
                tool_results = self._execute_tool_calls(message.tool_calls)
                total_tool_calls += len(tool_results)

                # Append tool results to conversation
                for result in tool_results:
                    messages.append(result)

                continue

            # Case 2: LLM produced a final response (no tool calls)
            content = message.content or ""

            # Try to validate as a review
            validated = self._validate_review(content)
            if validated is not None:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                return ReviewResult(
                    review=validated,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                    tokens_prompt=total_tokens_prompt,
                    tokens_completion=total_tokens_completion,
                    duration_ms=duration_ms,
                )

            # Case 3: Response is not a valid review — try correction
            logger.warning("LLM response is not valid JSON review, requesting correction.")
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "system",
                "content": (
                    "Your response was not valid JSON conforming to the Review Schema. "
                    "Please reformat your response as a single JSON object with these "
                    "required fields: findings (array), summary (string), effort_score "
                    "(integer 1-5), security_concerns (string), tests_assessment (string). "
                    "Output ONLY the JSON object, no markdown fencing or explanation."
                ),
            })

            # --- Timeout check before correction call ---
            if self._remaining_time_ms() < 10_000:
                review_dict = review_to_dict(build_fallback_review(content))
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                return ReviewResult(
                    review=review_dict,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                    tokens_prompt=total_tokens_prompt,
                    tokens_completion=total_tokens_completion,
                    duration_ms=duration_ms,
                )

            # Make correction call (no tools)
            try:
                correction_response = self._call_llm(messages, tools=None)
            except Exception as exc:
                logger.error("Correction LLM call failed: %s", exc)
                review_dict = review_to_dict(build_fallback_review(content))
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                return ReviewResult(
                    review=review_dict,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                    tokens_prompt=total_tokens_prompt,
                    tokens_completion=total_tokens_completion,
                    duration_ms=duration_ms,
                )

            if correction_response.usage:
                total_tokens_prompt += correction_response.usage.prompt_tokens
                total_tokens_completion += correction_response.usage.completion_tokens

            correction_content = correction_response.choices[0].message.content or ""
            validated = self._validate_review(correction_content)
            if validated is not None:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                return ReviewResult(
                    review=validated,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                    tokens_prompt=total_tokens_prompt,
                    tokens_completion=total_tokens_completion,
                    duration_ms=duration_ms,
                )

            # Correction also failed — use fallback
            logger.warning("Correction turn also produced invalid output, using fallback.")
            review_dict = review_to_dict(build_fallback_review(correction_content or content))
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ReviewResult(
                review=review_dict,
                iterations=iteration,
                tool_calls=total_tool_calls,
                tokens_prompt=total_tokens_prompt,
                tokens_completion=total_tokens_completion,
                duration_ms=duration_ms,
            )

        # --- Iteration budget exhausted ---
        logger.warning(
            "Iteration budget exhausted (%d iterations), forcing final turn.", iteration_budget
        )

        # Timeout check before forced final turn
        if self._remaining_time_ms() < 10_000:
            review_dict = self._handle_timeout(messages, total_tokens_prompt, total_tokens_completion)
        else:
            review_dict = self._force_final_turn(messages)
            if review_dict is None:
                review_dict = review_to_dict(
                    build_fallback_review("Review incomplete: iteration budget exhausted.")
                )

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return ReviewResult(
            review=review_dict,
            iterations=iteration,
            tool_calls=total_tool_calls,
            tokens_prompt=total_tokens_prompt,
            tokens_completion=total_tokens_completion,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # LLM communication
    # ------------------------------------------------------------------

    def _call_llm(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> ChatCompletion:
        """Call xAI via the OpenAI SDK with timeout and retry logic.

        Implements:
        - Per-call timeout = remaining_time_ms() / 1000 - 10, minimum 5s
        - Exponential backoff retry (1s, 2s, 4s) for 429/5xx
        - Immediate failure on 401/403

        Args:
            messages: The conversation history.
            tools: Tool definitions (None to disable tool calling).

        Returns:
            The ChatCompletion response.

        Raises:
            RuntimeError: On auth errors (401/403) or after exhausting retries.
        """
        # Calculate per-call timeout
        remaining_s = self._remaining_time_ms() / 1000.0
        timeout_s = max(5.0, remaining_s - 10.0)

        # Build call kwargs
        kwargs: dict = {
            "model": self._settings.model,
            "messages": messages,
            "timeout": timeout_s,
        }
        if tools:
            kwargs["tools"] = tools

        # Retry with exponential backoff for transient errors
        backoff_delays = [1.0, 2.0, 4.0]
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(**kwargs)
                return response

            except openai.AuthenticationError as exc:
                raise RuntimeError(
                    f"invalid credentials: {exc}"
                ) from exc

            except openai.PermissionDeniedError as exc:
                raise RuntimeError(
                    f"invalid credentials: {exc}"
                ) from exc

            except openai.RateLimitError as exc:
                last_error = exc
                if attempt < 2:
                    delay = backoff_delays[attempt]
                    logger.warning(
                        "Rate limited (attempt %d/3), retrying in %.1fs: %s",
                        attempt + 1, delay, exc,
                    )
                    time.sleep(delay)
                    continue

            except openai.APIStatusError as exc:
                # 5xx errors are retryable
                if exc.status_code >= 500:
                    last_error = exc
                    if attempt < 2:
                        delay = backoff_delays[attempt]
                        logger.warning(
                            "Server error %d (attempt %d/3), retrying in %.1fs: %s",
                            exc.status_code, attempt + 1, delay, exc,
                        )
                        time.sleep(delay)
                        continue
                else:
                    # Non-retryable client errors
                    raise RuntimeError(
                        f"LLM API error (HTTP {exc.status_code}): {exc}"
                    ) from exc

            except openai.APITimeoutError as exc:
                last_error = exc
                if attempt < 2:
                    delay = backoff_delays[attempt]
                    logger.warning(
                        "Timeout (attempt %d/3), retrying in %.1fs: %s",
                        attempt + 1, delay, exc,
                    )
                    time.sleep(delay)
                    continue

            except openai.APIError as exc:
                last_error = exc
                if attempt < 2:
                    delay = backoff_delays[attempt]
                    logger.warning(
                        "API error (attempt %d/3), retrying in %.1fs: %s",
                        attempt + 1, delay, exc,
                    )
                    time.sleep(delay)
                    continue

        # All retries exhausted
        raise RuntimeError(
            f"LLM call failed after 3 attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _execute_tool_calls(self, tool_calls: list) -> list[dict]:
        """Execute tool calls from a single LLM turn.

        Caps execution at max_tool_calls_per_turn. For each tool call:
        - Parses the function name and JSON arguments
        - Dispatches via the tool registry
        - On transient failure (5xx or timeout): retries once
        - On permanent failure (4xx): appends error message to context

        Args:
            tool_calls: List of tool call objects from the LLM response.

        Returns:
            List of tool result messages in OpenAI format.
        """
        max_calls = self._settings.max_tool_calls_per_turn
        calls_to_execute = tool_calls[:max_calls]

        if len(tool_calls) > max_calls:
            logger.info(
                "LLM requested %d tool calls, executing first %d (cap).",
                len(tool_calls), max_calls,
            )

        results: list[dict] = []

        for tc in calls_to_execute:
            tool_name = tc.function.name
            tool_call_id = tc.id

            # Parse arguments
            try:
                arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse arguments for tool '%s': %s",
                    tool_name, tc.function.arguments,
                )
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"Error: failed to parse arguments as JSON.",
                })
                continue

            # Execute with retry for transient failures
            tool_result = self._execute_single_tool(tool_name, arguments)

            results.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_result,
            })

        return results

    def _execute_single_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a single tool call with retry logic for transient failures.

        Args:
            tool_name: The name of the tool to execute.
            arguments: The parsed arguments dict.

        Returns:
            The tool result content string.
        """
        import requests

        try:
            result = self._tool_registry.execute(tool_name, arguments)
            return result.content
        except requests.HTTPError as exc:
            # Check if transient (5xx) — retry once
            if exc.response is not None and exc.response.status_code >= 500:
                logger.warning(
                    "Transient error executing tool '%s' (HTTP %d), retrying once.",
                    tool_name, exc.response.status_code,
                )
                try:
                    result = self._tool_registry.execute(tool_name, arguments)
                    return result.content
                except Exception as retry_exc:
                    return f"Error executing '{tool_name}' after retry: {retry_exc}"
            else:
                # Permanent failure (4xx) — return error
                return f"Error executing '{tool_name}': {exc}"
        except requests.Timeout:
            # Timeout is transient — retry once
            logger.warning("Timeout executing tool '%s', retrying once.", tool_name)
            try:
                result = self._tool_registry.execute(tool_name, arguments)
                return result.content
            except Exception as retry_exc:
                return f"Error executing '{tool_name}' after retry: {retry_exc}"
        except Exception as exc:
            return f"Error executing '{tool_name}': {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # System prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self, pr_context: PRContext) -> str:
        """Construct the system prompt with review instructions.

        The prompt instructs the LLM to:
        - Use tools to verify before claiming
        - Focus on changed files/lines, use unchanged files as context only
        - Never use hedging phrases
        - Categorize findings by type
        - Include file path + line range for each finding
        - Provide concrete suggestions/fixes
        - Omit unverifiable findings
        - Assign severity levels
        - Output as JSON conforming to ReviewSchema
        """
        return f"""You are an expert code reviewer analyzing a pull request.

## Repository Context
- Repository: {pr_context.owner}/{pr_context.repo}
- PR #{pr_context.pr_number}: {pr_context.title}
- Author: {pr_context.author}
- Branch: {pr_context.branch}

## Instructions

### Tool Usage
You have tools available to fetch file contents, search code, list directories, get commit info, and view specific line ranges. USE THESE TOOLS to verify your understanding before making any claims. Do not guess or assume — verify via tools first.

IMPORTANT: The full PR diff is already provided in the user message below. Do NOT call the get_pr_diff tool — you already have the diff. Use tools only to fetch additional context (file contents, directory structure, related code) that helps you understand the changes.

Focus your review on files and lines CHANGED in the PR diff. Use unchanged files only as supporting context to understand the impact of changes.

### No Hedging
NEVER use hedging language. The following phrases are BANNED:
- "it appears that"
- "if this is"
- "it seems like"
- "this might be"
- "possibly"
- "probably"
- "I think"
- Any other language expressing uncertainty about a claim you have not verified

If you cannot verify a potential finding using the available tools, OMIT the finding entirely rather than reporting it with uncertainty.

### Finding Categories
Categorize each finding as one of:
- bug: Functional defects, logic errors, incorrect behavior
- security: Vulnerabilities, unsafe patterns, missing validation
- performance: Inefficiencies, unnecessary allocations, O(n) where O(1) is possible
- maintainability: Code complexity, poor naming, missing abstractions
- style: Formatting, naming conventions, consistency issues
- documentation: Missing or incorrect comments, docstrings, READMEs

### Finding Details
For each finding, provide:
- The specific file path and line range (start_line and end_line)
- A concise title (max 120 characters)
- A clear explanation (max 500 characters) of what is wrong and why
- A concrete suggestion or fix. If multiple equally valid fixes exist, describe the alternatives.

### Severity Levels
Assign one severity to each finding:
- critical: Bugs that cause incorrect behavior, security vulnerabilities, data loss risks
- warning: Issues that degrade quality but don't break functionality (performance, maintainability)
- info: Style issues, minor suggestions, documentation improvements

### Output Format
When you have completed your review, produce your final output as a single JSON object with this exact structure:

```json
{{
  "findings": [
    {{
      "severity": "critical|warning|info",
      "category": "bug|security|performance|maintainability|style|documentation",
      "file_path": "path/to/file.py",
      "start_line": 42,
      "end_line": 45,
      "title": "Brief description of the issue (max 120 chars)",
      "explanation": "Detailed explanation of what is wrong and how to fix it (max 500 chars)"
    }}
  ],
  "summary": "Overall review summary (max 1000 chars)",
  "effort_score": 3,
  "security_concerns": "Description of security issues, or empty string if none",
  "tests_assessment": "Assessment of test coverage, or empty string if none"
}}
```

- Order findings by severity (critical first, then warning, then info)
- Maximum 50 findings
- effort_score: integer 1-5 (1 = trivial change, 5 = complex/risky change)
- Output ONLY the JSON object as your final response, no markdown fencing or extra text"""

    def _build_user_message(self, pr_context: PRContext, diff: str) -> str:
        """Build the initial user message containing PR metadata and diff."""
        return f"""Please review the following pull request.

## Pull Request Details
- URL: {pr_context.pr_url}
- Title: {pr_context.title}
- Author: {pr_context.author}
- Branch: {pr_context.branch}

## Diff

```diff
{diff}
```

The complete PR diff is provided above — do not re-fetch it. Use the available tools to fetch additional context (file contents, related code, directory structure) needed to verify your findings. When done, produce your final review as a JSON object conforming to the Review Schema."""

    # ------------------------------------------------------------------
    # Response handling and validation
    # ------------------------------------------------------------------

    def _validate_review(self, raw: str) -> dict | None:
        """Parse and validate the LLM response against the Review Schema.

        Args:
            raw: The raw text content from the LLM response.

        Returns:
            A dict representation of the validated ReviewSchema, or None if invalid.
        """
        # Strip potential markdown code fencing
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        review_schema = validate_review(cleaned)
        if review_schema is None:
            return None

        return review_to_dict(review_schema)

    def _force_final_turn(self, messages: list[dict]) -> dict | None:
        """Send a final-turn prompt forcing the LLM to produce output without tools.

        Used when the iteration budget is exhausted or time is running out.

        Args:
            messages: The current conversation history.

        Returns:
            A validated review dict, or None if the LLM fails to produce valid output.
        """
        final_messages = messages + [
            {
                "role": "system",
                "content": (
                    "You have reached the end of your review budget. Produce your final "
                    "review NOW as a single JSON object conforming to the Review Schema. "
                    "Do not request any more tool calls. Base your review on the information "
                    "you have already gathered. Output ONLY the JSON object."
                ),
            }
        ]

        try:
            response = self._call_llm(final_messages, tools=None)
        except Exception as exc:
            logger.error("Force final turn LLM call failed: %s", exc)
            return None

        content = response.choices[0].message.content or ""
        validated = self._validate_review(content)
        if validated is not None:
            return validated

        # Final turn produced invalid output — use fallback
        return review_to_dict(build_fallback_review(content))

    # ------------------------------------------------------------------
    # Timeout handling
    # ------------------------------------------------------------------

    def _handle_timeout(
        self,
        messages: list[dict],
        tokens_prompt: int,
        tokens_completion: int,
    ) -> dict:
        """Handle timeout scenario when Lambda execution time is running low.

        Attempts a forced final turn with an 8-second timeout. If that also
        fails, wraps any partial results in a fallback review.

        Args:
            messages: The current conversation history.
            tokens_prompt: Accumulated prompt tokens so far.
            tokens_completion: Accumulated completion tokens so far.

        Returns:
            A review dict (validated or fallback).
        """
        # Try a forced final turn with tight timeout
        final_messages = messages + [
            {
                "role": "system",
                "content": (
                    "URGENT: Time is almost up. Produce your final review NOW as a single "
                    "JSON object. Use only the information you have already gathered. "
                    "Output ONLY the JSON — no tool calls, no explanation."
                ),
            }
        ]

        try:
            # Override timeout to 8 seconds for the emergency call
            kwargs: dict = {
                "model": self._settings.model,
                "messages": final_messages,
                "timeout": 8.0,
            }
            response = self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            validated = self._validate_review(content)
            if validated is not None:
                return validated
            # Got a response but couldn't parse it — fallback
            return review_to_dict(build_fallback_review(content))

        except Exception as exc:
            logger.error("Timeout forced final turn also failed: %s", exc)
            # Extract any useful partial content from conversation
            partial_content = self._extract_partial_review(messages)
            fallback = build_fallback_review(
                partial_content or "Review incomplete due to timeout."
            )
            return review_to_dict(fallback)

    def _extract_partial_review(self, messages: list[dict]) -> str:
        """Extract any partial review content from the conversation history.

        Looks for the last assistant message that might contain review-like content.

        Args:
            messages: The conversation history.

        Returns:
            The last assistant message content, or empty string if none found.
        """
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if content and len(content) > 50:
                    return content
        return ""
