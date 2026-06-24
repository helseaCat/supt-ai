"""GitHub App authentication helper.

Generates short-lived installation access tokens by:
1. Building a JWT signed with the App's private key
2. POSTing to /app/installations/{installation_id}/access_tokens
3. Returning the token for use with the GitHub API
"""

import logging
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json

import jwt

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


def _build_jwt(app_id: str, private_key: str) -> str:
    """Create a signed JWT for GitHub App authentication.

    The JWT is valid for up to 10 minutes (GitHub maximum).
    We use 9 minutes to allow for clock drift.
    """
    now = int(time.time())
    payload = {
        "iat": now - 60,  # Issued 60s in the past to account for clock drift
        "exp": now + (9 * 60),  # Expires in 9 minutes
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(
    app_id: str,
    private_key: str,
    installation_id: str,
) -> str:
    """Generate a short-lived installation access token.

    Args:
        app_id: The GitHub App ID.
        private_key: The PEM-encoded private key for the App.
        installation_id: The installation ID for the target org/repo.

    Returns:
        A short-lived token string suitable for GitHub API calls.

    Raises:
        RuntimeError: If token generation fails.
    """
    try:
        token_jwt = _build_jwt(app_id, private_key)
    except Exception as e:
        raise RuntimeError(
            f"Failed to build JWT — check GITHUB_APP_PRIVATE_KEY format: {type(e).__name__}"
        ) from e

    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    req = Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {token_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            token = data["token"]
            expires_at = data.get("expires_at", "unknown")
            logger.info(
                "Generated installation token (expires %s)", expires_at
            )
            return token
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise RuntimeError(
            f"Failed to create installation token: {e.code} {e.reason} — {body}"
        ) from e
    except (KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(
            f"Unexpected response when creating installation token: {e}"
        ) from e
