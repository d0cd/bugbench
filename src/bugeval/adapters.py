"""Adapter protocol and factory for API-mode evaluation tools."""

from __future__ import annotations

from typing import Any, Protocol

from bugeval.models import TestCase


class ToolAdapter(Protocol):
    async def submit(
        self,
        case: TestCase,
        patch_content: str,
        context_level: str,
    ) -> list[dict[str, Any]]:
        """Submit a case for review. Returns normalized findings."""
        ...


def get_adapter(tool_name: str) -> type[ToolAdapter]:
    """Return the adapter class for a given tool name."""
    from bugeval.greptile_adapter import GreptileAdapter

    adapters: dict[str, type[ToolAdapter]] = {
        "greptile": GreptileAdapter,
    }
    if tool_name not in adapters:
        raise ValueError(f"No adapter for tool: {tool_name}")
    return adapters[tool_name]
