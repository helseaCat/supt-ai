"""Utilities for parsing PR-Agent output."""

import re

import yaml

# Regex to strip ANSI escape sequences
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

# Regex to extract YAML from PR-Agent's verbose output
_YAML_BLOCK = re.compile(r"AI response:\s*```yaml\s*\n(.*?)```", re.DOTALL)


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from a string."""
    return _ANSI_ESCAPE.sub("", text)


def extract_review(output: str) -> dict | None:
    """Extract the YAML review block from PR-Agent's verbose output.

    Returns the parsed review dict, or None if extraction fails.
    """
    match = _YAML_BLOCK.search(output)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
