"""Tests for GreptileAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bugeval.greptile_adapter import (
    GreptileAdapter,
    GreptileApiError,
    _build_request_body,
    _normalize_response,
)
from tests.conftest import make_case

# --- _build_request_body ---


def test_build_request_body_diff_only() -> None:
    case = make_case()
    body = _build_request_body(case, "--- a/foo\n+++ b/foo", "diff-only")
    assert body["diff"] == "--- a/foo\n+++ b/foo"
    assert body["context_level"] == "diff-only"
    assert "repository" not in body
    assert "domain_context" not in body


def test_build_request_body_diff_plus_repo() -> None:
    case = make_case()
    body = _build_request_body(case, "patch", "diff+repo")
    assert body["repository"] == "provable-org/aleo-lang"
    assert "domain_context" not in body


def test_build_request_body_diff_plus_repo_plus_domain() -> None:
    case = make_case()
    body = _build_request_body(case, "patch", "diff+repo+domain")
    assert body["repository"] == "provable-org/aleo-lang"
    assert "domain_context" in body
    assert "provable-org/aleo-lang" in body["domain_context"]


# --- _normalize_response ---


def test_normalize_response_empty() -> None:
    assert _normalize_response(None) == []
    assert _normalize_response([]) == []
    assert _normalize_response({}) == []


def test_normalize_response_list_format() -> None:
    raw = [
        {"summary": "null pointer", "file": "src/lib.rs", "line": 10},
        {"body": "type error", "path": "src/main.rs", "lineNumber": 20},
    ]
    result = _normalize_response(raw)
    assert len(result) == 2
    assert result[0] == {
        "source": "greptile",
        "body": "null pointer",
        "path": "src/lib.rs",
        "line": 10,
    }
    assert result[1] == {
        "source": "greptile",
        "body": "type error",
        "path": "src/main.rs",
        "line": 20,
    }


def test_normalize_response_dict_format() -> None:
    raw = {"findings": [{"summary": "bug", "file": "a.rs", "line": 5}]}
    result = _normalize_response(raw)
    assert len(result) == 1
    assert result[0]["body"] == "bug"
    assert result[0]["path"] == "a.rs"
    assert result[0]["line"] == 5


def test_normalize_response_missing_fields() -> None:
    raw = [{}]
    result = _normalize_response(raw)
    assert result == [{"source": "greptile", "body": "", "path": "", "line": 0}]


# --- GreptileAdapter.submit ---


async def test_submit_success() -> None:
    case = make_case()
    adapter = GreptileAdapter(
        api_endpoint="https://api.greptile.com/v2/review",
        api_key="test-key",
    )
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(
        return_value=[{"summary": "bug found", "file": "src/lib.rs", "line": 42}]
    )
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await adapter.submit(case, "patch content", "diff-only")

    assert len(result) == 1
    assert result[0]["body"] == "bug found"
    assert result[0]["source"] == "greptile"


async def test_submit_error_raises() -> None:
    case = make_case()
    adapter = GreptileAdapter(
        api_endpoint="https://api.greptile.com/v2/review",
        api_key="test-key",
    )
    mock_response = MagicMock()
    mock_response.status = 401
    mock_response.text = AsyncMock(return_value="Unauthorized")
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(GreptileApiError, match="HTTP 401"):
            await adapter.submit(case, "patch", "diff-only")


async def test_submit_passes_auth_header() -> None:
    case = make_case()
    adapter = GreptileAdapter(
        api_endpoint="https://api.greptile.com/v2/review",
        api_key="my-secret-key",
    )
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=[])
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await adapter.submit(case, "patch", "diff-only")

    call_kwargs = mock_session.post.call_args
    headers = call_kwargs.kwargs["headers"]
    assert headers["Authorization"] == "Bearer my-secret-key"
    assert headers["X-GitHub-Repository"] == "provable-org/aleo-lang"


def test_greptile_api_error_message() -> None:
    err = GreptileApiError("HTTP 500: Internal Server Error")
    assert "HTTP 500" in str(err)
