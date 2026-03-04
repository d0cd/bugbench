"""Greptile API adapter for diff-based code review."""

from __future__ import annotations

from typing import Any

import aiohttp

from bugeval.models import TestCase


class GreptileApiError(Exception):
    """Raised when the Greptile API returns a non-200 response."""


def _build_request_body(
    case: TestCase,
    patch_content: str,
    context_level: str,
) -> dict[str, Any]:
    """Build the JSON body for a Greptile review request."""
    body: dict[str, Any] = {
        "diff": patch_content,
        "context_level": context_level,
    }
    if context_level in ("diff+repo", "diff+repo+domain"):
        body["repository"] = case.repo
    if context_level == "diff+repo+domain":
        body["domain_context"] = (
            f"This is a bug introduced in {case.repo}. "
            f"Category: {case.category}. Severity: {case.severity}."
        )
    return body


def _normalize_response(raw: Any) -> list[dict[str, Any]]:
    """Normalize a Greptile API response into a list of standard finding dicts."""
    if not raw:
        return []

    findings = raw if isinstance(raw, list) else raw.get("findings", [])
    if not findings:
        return []

    result = []
    for item in findings:
        result.append(
            {
                "source": "greptile",
                "body": item.get("summary") or item.get("body") or "",
                "path": item.get("file") or item.get("path") or "",
                "line": item.get("line") or item.get("lineNumber") or 0,
            }
        )
    return result


class GreptileAdapter:
    """Adapter that submits diffs to the Greptile review API."""

    def __init__(self, api_endpoint: str, api_key: str) -> None:
        self.api_endpoint = api_endpoint
        self.api_key = api_key

    async def submit(
        self,
        case: TestCase,
        patch_content: str,
        context_level: str,
    ) -> list[dict[str, Any]]:
        """Submit a case diff to Greptile for review. Returns normalized findings."""
        body = _build_request_body(case, patch_content, context_level)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_endpoint,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "X-GitHub-Repository": case.repo,
                },
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise GreptileApiError(f"HTTP {resp.status}: {text}")
                data = await resp.json()
                return _normalize_response(data)
