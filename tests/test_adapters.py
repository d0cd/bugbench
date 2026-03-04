"""Tests for the adapter protocol and factory."""

import pytest

from bugeval.adapters import ToolAdapter, get_adapter
from bugeval.greptile_adapter import GreptileAdapter


def test_get_adapter_returns_greptile_class() -> None:
    cls = get_adapter("greptile")
    assert cls is GreptileAdapter


def test_get_adapter_unknown_tool_raises() -> None:
    with pytest.raises(ValueError, match="No adapter for tool: unknown-tool"):
        get_adapter("unknown-tool")


def test_get_adapter_returns_class_not_instance() -> None:
    cls = get_adapter("greptile")
    assert isinstance(cls, type)


def test_greptile_adapter_satisfies_protocol() -> None:
    # Runtime check: GreptileAdapter has a submit method
    adapter = GreptileAdapter(
        api_endpoint="https://api.greptile.com/v2/review",
        api_key="test-key",
    )
    assert hasattr(adapter, "submit")
    assert callable(adapter.submit)


def test_tool_adapter_is_protocol() -> None:
    import inspect

    assert inspect.isclass(ToolAdapter)
