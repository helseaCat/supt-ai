"""GitHub REST API client with GitHub App installation token auth.

Authenticates using a GitHub App private key (JWT → installation token exchange)
and provides typed methods for all GitHub API calls needed by the reviewer tools
and output adapters.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import jwt
import requests

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

# Token is considered expired if within this many seconds of expiry.
_TOKEN_EXPIRY_BUFFER_SECONDS = 5 * 60  # 5 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_private_key(key: str) -> str:
    """Ensure the PEM key has real newlines.

    Handles the case where the key is stored with literal '\\n' strings
    (common when pasting into AWS console or JSON editors).
    """
    if "\\n" in key and "\n" not in key.strip():
        key = key.replace("\\n", "\n")
    return key.strip()


# ---------------------------------------------------------------------------
# GitHubClient
# ---------------------------------------------------------------------------


class GitHubClient:
    """HTTP client for GitHub API with installation token auth.

    Handles JWT signing, installation token exchange, automatic token refresh,
    and provides high-level methods for the repository operations needed by the
    reviewer's tool registry and output adapters.
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
        installation_id: str,
        request_timeout: int = 30,
    ) -> None:
        """Initialize the GitHub client.

        Args:
            app_id: The GitHub App ID.
            private_key: PEM-encoded private key for JWT signing.
            installation_id: The installation ID for the target org/repo.
            request_timeout: Default timeout in seconds for API requests.
        """
        self._app_id = app_id
        self._private_key = _normalize_private_key(private_key)
        self._installation_id = installation_id
        self._request_timeout = request_timeout

        # Token state — never persisted outside process memory.
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _generate_token(self) -> None:
        """Sign a JWT and exchange it for an installation access token.

        Sets self._token and self._token_expires_at on success.

        Raises:
            RuntimeError: If JWT signing or token exchange fails.
        """
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60s in the past for clock drift
            "exp": now + (10 * 60),  # JWT valid for 10 minutes
            "iss": self._app_id,
        }

        try:
            token_jwt = jwt.encode(payload, self._private_key, algorithm="RS256")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to sign JWT — check private key format: {type(exc).__name__}: {exc}"
            ) from exc

        url = f"{GITHUB_API_BASE}/app/installations/{self._installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {token_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            resp = requests.post(url, headers=headers, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to create installation token: {exc}"
            ) from exc

        data = resp.json()
        self._token = data["token"]

        # Parse the expiry time from the response.
        expires_at_str = data.get("expires_at", "")
        if expires_at_str:
            self._token_expires_at = datetime.fromisoformat(
                expires_at_str.replace("Z", "+00:00")
            )
        else:
            # Fallback: assume 1 hour from now (GitHub default).
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        logger.info("Generated installation token (expires %s)", expires_at_str)

    def _is_token_expired(self) -> bool:
        """Return True if the token is missing or within 5 minutes of expiry."""
        if self._token is None or self._token_expires_at is None:
            return True
        remaining = (self._token_expires_at - datetime.now(timezone.utc)).total_seconds()
        return remaining < _TOKEN_EXPIRY_BUFFER_SECONDS

    def ensure_token(self) -> None:
        """Generate or refresh the installation token if expired or missing."""
        if self._is_token_expired():
            self._generate_token()

    # ------------------------------------------------------------------
    # HTTP primitives
    # ------------------------------------------------------------------

    def _auth_headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        """Build standard headers with Bearer auth."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get(self, path: str, accept: str = "application/vnd.github+json") -> dict | str | list:
        """Perform an authenticated GET request to the GitHub API.

        Automatically refreshes the token on 401 and retries once.

        Args:
            path: API path (e.g., "/repos/owner/repo/contents/file.py").
            accept: Accept header value.

        Returns:
            Parsed JSON (dict or list) or raw text depending on content type.

        Raises:
            requests.HTTPError: On non-2xx response after potential retry.
        """
        self.ensure_token()
        url = f"{GITHUB_API_BASE}{path}"

        resp = requests.get(
            url, headers=self._auth_headers(accept), timeout=self._request_timeout
        )

        # On 401, refresh token once and retry.
        if resp.status_code == 401:
            logger.warning("Got 401 from GitHub API, refreshing token and retrying.")
            self._generate_token()
            resp = requests.get(
                url, headers=self._auth_headers(accept), timeout=self._request_timeout
            )

        resp.raise_for_status()

        # Return raw text for diff/raw content types, otherwise JSON.
        content_type = resp.headers.get("Content-Type", "")
        if "application/vnd.github.diff" in content_type or "text/plain" in content_type:
            return resp.text
        if "application/vnd.github.raw" in content_type:
            return resp.text

        try:
            return resp.json()
        except ValueError:
            return resp.text

    def post(self, path: str, body: dict) -> dict:
        """Perform an authenticated POST request to the GitHub API.

        Automatically refreshes the token on 401 and retries once.

        Args:
            path: API path.
            body: JSON-serializable request body.

        Returns:
            Parsed JSON response.

        Raises:
            requests.HTTPError: On non-2xx response after potential retry.
        """
        self.ensure_token()
        url = f"{GITHUB_API_BASE}{path}"

        resp = requests.post(
            url,
            json=body,
            headers=self._auth_headers(),
            timeout=self._request_timeout,
        )

        # On 401, refresh token once and retry.
        if resp.status_code == 401:
            logger.warning("Got 401 from GitHub API, refreshing token and retrying.")
            self._generate_token()
            resp = requests.post(
                url,
                json=body,
                headers=self._auth_headers(),
                timeout=self._request_timeout,
            )

        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Repository API methods
    # ------------------------------------------------------------------

    def get_file_contents(self, owner: str, repo: str, path: str, ref: str | None = None) -> str:
        """Fetch raw file content from a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            path: File path relative to repo root.
            ref: Git ref (branch, tag, SHA). Uses default branch if None.

        Returns:
            The raw file content as a string.
        """
        api_path = f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}"
        if ref:
            api_path += f"?ref={quote(ref, safe='')}"

        result = self.get(api_path, accept="application/vnd.github.raw+json")
        return result if isinstance(result, str) else str(result)

    def search_code(self, owner: str, repo: str, query: str) -> list[dict]:
        """Search code within a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            query: Search query string.

        Returns:
            A list of dicts with 'path' and 'text_matches' keys.
        """
        params = urlencode({"q": f"{query}+repo:{owner}/{repo}"})
        api_path = f"/search/code?{params}"

        result = self.get(api_path, accept="application/vnd.github.text-match+json")

        items = []
        if isinstance(result, dict):
            for item in result.get("items", []):
                items.append({
                    "path": item.get("path", ""),
                    "text_matches": item.get("text_matches", []),
                })
        return items

    def list_directory(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> list[dict]:
        """List directory contents from a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            path: Directory path relative to repo root.
            ref: Git ref (branch, tag, SHA). Uses default branch if None.

        Returns:
            A list of dicts representing directory items.
        """
        api_path = f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}"
        if ref:
            api_path += f"?ref={quote(ref, safe='')}"

        result = self.get(api_path)

        if isinstance(result, list):
            return result
        # If we got a single file object instead of a directory listing, wrap it.
        if isinstance(result, dict):
            return [result]
        return []

    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Get the unified diff for a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: Pull request number.

        Returns:
            The unified diff as a string.
        """
        api_path = f"/repos/{owner}/{repo}/pulls/{pr_number}"
        result = self.get(api_path, accept="application/vnd.github.diff")
        return result if isinstance(result, str) else str(result)

    def get_commit(self, owner: str, repo: str, sha: str) -> dict:
        """Get commit details.

        Args:
            owner: Repository owner.
            repo: Repository name.
            sha: Full or abbreviated commit SHA.

        Returns:
            Commit details dict (message, author, timestamp, files).
        """
        api_path = f"/repos/{owner}/{repo}/commits/{quote(sha, safe='')}"
        result = self.get(api_path)
        return result if isinstance(result, dict) else {}

    # ------------------------------------------------------------------
    # Review posting
    # ------------------------------------------------------------------

    def post_review(self, owner: str, repo: str, pr_number: int, review: dict) -> dict:
        """Submit a PR review with inline comments.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: Pull request number.
            review: Review payload dict (event, body, comments).

        Returns:
            The GitHub API response for the created review.
        """
        api_path = f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        return self.post(api_path, body=review)

    def dismiss_review(self, owner: str, repo: str, pr_number: int, review_id: int) -> None:
        """Dismiss a PR review.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: Pull request number.
            review_id: The ID of the review to dismiss.
        """
        api_path = f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews/{review_id}/dismissals"
        self.post(api_path, body={"message": "Superseded by new review."})

    def list_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """List all reviews on a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: Pull request number.

        Returns:
            List of review dicts from the GitHub API.
        """
        api_path = f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        result = self.get(api_path)
        return result if isinstance(result, list) else []
