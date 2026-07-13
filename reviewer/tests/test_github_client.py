"""Unit tests for GitHubClient.

Validates: Requirements 5.3, 5.4, 5.7
- JWT generation and installation token exchange
- Token refresh logic and expiry detection
- HTTP methods with automatic 401 retry
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from lib.github_client import GitHubClient


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def rsa_private_key() -> str:
    """Generate a real RSA private key PEM for JWT signing tests."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


@pytest.fixture()
def client(rsa_private_key) -> GitHubClient:
    """Create a GitHubClient with a valid RSA key."""
    return GitHubClient(
        app_id="123456",
        private_key=rsa_private_key,
        installation_id="789",
    )


# ─── Token Generation ────────────────────────────────────────────────────────


@patch("lib.github_client.requests")
def test_generate_token_success(mock_requests, client):
    """Successful token exchange sets _token and _token_expires_at."""
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"token": "ghs_abc123", "expires_at": expires}
    mock_requests.post.return_value = mock_resp

    client._generate_token()

    assert client._token == "ghs_abc123"
    assert client._token_expires_at is not None
    mock_requests.post.assert_called_once()


def test_generate_token_invalid_key():
    """Invalid private key raises RuntimeError during JWT signing."""
    bad_client = GitHubClient(
        app_id="123", private_key="not-a-valid-key", installation_id="456"
    )
    with pytest.raises(RuntimeError, match="Failed to sign JWT"):
        bad_client._generate_token()


@patch("lib.github_client.requests")
def test_generate_token_http_failure(mock_requests, client):
    """Non-2xx response from GitHub raises RuntimeError."""
    import requests as real_requests

    mock_requests.RequestException = real_requests.RequestException
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.raise_for_status.side_effect = real_requests.HTTPError("401 Unauthorized")
    mock_requests.post.return_value = mock_resp

    with pytest.raises(RuntimeError, match="Failed to create installation token"):
        client._generate_token()


# ─── Token Expiry Detection ─────────────────────────────────────────────────


def test_is_token_expired_when_none(client):
    """Token is expired when never generated."""
    assert client._is_token_expired() is True


def test_is_token_expired_when_fresh(client):
    """Token is not expired when expiry is well in the future."""
    client._token = "ghs_fresh"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    assert client._is_token_expired() is False


def test_is_token_expired_when_near_expiry(client):
    """Token is expired when within 5-minute buffer of expiry."""
    client._token = "ghs_expiring"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
    assert client._is_token_expired() is True


# ─── ensure_token ────────────────────────────────────────────────────────────


@patch("lib.github_client.requests")
def test_ensure_token_generates_when_expired(mock_requests, client):
    """ensure_token calls _generate_token when token is expired."""
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"token": "ghs_new", "expires_at": expires}
    mock_requests.post.return_value = mock_resp

    client.ensure_token()

    assert client._token == "ghs_new"
    mock_requests.post.assert_called_once()


# ─── GET Method ──────────────────────────────────────────────────────────────


@patch("lib.github_client.requests")
def test_get_success(mock_requests, client):
    """GET returns parsed JSON on 200."""
    client._token = "ghs_valid"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp.json.return_value = {"id": 1, "name": "test"}
    mock_requests.get.return_value = mock_resp

    result = client.get("/repos/owner/repo")
    assert result == {"id": 1, "name": "test"}


@patch("lib.github_client.requests")
def test_get_auto_refresh_on_401(mock_requests, client):
    """GET refreshes token and retries on 401."""
    client._token = "ghs_expired"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    # First GET returns 401, second returns 200
    resp_401 = MagicMock()
    resp_401.status_code = 401

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.raise_for_status = MagicMock()
    resp_200.headers = {"Content-Type": "application/json"}
    resp_200.json.return_value = {"retried": True}

    mock_requests.get.side_effect = [resp_401, resp_200]

    # Mock the token refresh POST
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    token_resp = MagicMock()
    token_resp.raise_for_status = MagicMock()
    token_resp.json.return_value = {"token": "ghs_refreshed", "expires_at": expires}
    mock_requests.post.return_value = token_resp

    result = client.get("/repos/owner/repo")

    assert result == {"retried": True}
    assert client._token == "ghs_refreshed"
    assert mock_requests.get.call_count == 2


# ─── POST Method ─────────────────────────────────────────────────────────────


@patch("lib.github_client.requests")
def test_post_success(mock_requests, client):
    """POST returns parsed JSON on 201."""
    client._token = "ghs_valid"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": 42, "state": "created"}
    mock_requests.post.return_value = mock_resp

    result = client.post("/repos/owner/repo/issues", body={"title": "Bug"})
    assert result == {"id": 42, "state": "created"}


# ─── Repository API Methods ──────────────────────────────────────────────────


@patch("lib.github_client.requests")
def test_get_file_contents(mock_requests, client):
    """get_file_contents uses correct path and raw accept header."""
    client._token = "ghs_valid"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {"Content-Type": "application/vnd.github.raw+json"}
    mock_resp.text = "file content here"
    mock_requests.get.return_value = mock_resp

    result = client.get_file_contents("owner", "repo", "src/main.py", ref="main")

    call_args = mock_requests.get.call_args
    url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    assert "/repos/owner/repo/contents/src/main.py" in url
    assert "ref=main" in url
    assert result == "file content here"


@patch("lib.github_client.requests")
def test_get_pr_diff(mock_requests, client):
    """get_pr_diff uses correct path and diff accept header."""
    client._token = "ghs_valid"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {"Content-Type": "application/vnd.github.diff"}
    mock_resp.text = "diff --git a/file.py b/file.py\n+new line"
    mock_requests.get.return_value = mock_resp

    result = client.get_pr_diff("owner", "repo", 42)

    call_args = mock_requests.get.call_args
    url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    assert "/repos/owner/repo/pulls/42" in url
    headers = call_args[1].get("headers", {}) if len(call_args) > 1 else call_args[0][1] if len(call_args[0]) > 1 else {}
    assert "application/vnd.github.diff" in str(headers)
    assert "diff --git" in result


@patch("lib.github_client.requests")
def test_post_review(mock_requests, client):
    """post_review sends to correct API path with body."""
    client._token = "ghs_valid"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": 99}
    mock_requests.post.return_value = mock_resp

    review_body = {"event": "COMMENT", "body": "LGTM", "comments": []}
    result = client.post_review("owner", "repo", 7, review_body)

    call_args = mock_requests.post.call_args
    url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    assert "/repos/owner/repo/pulls/7/reviews" in url
    assert result == {"id": 99}
