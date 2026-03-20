"""Tests for the Claude Agent SDK runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from claude_agent_sdk import ResultMessage, SystemMessage

from bugeval.agent_models import AgentResult


def _make_result_message(text: str) -> ResultMessage:
    """Build a mock ResultMessage with the given text."""
    msg = MagicMock(spec=ResultMessage)
    msg.result = text
    return msg  # type: ignore[return-value]


def _make_system_message(session_id: str = "sess-123") -> SystemMessage:
    """Build a mock SystemMessage(init)."""
    msg = MagicMock(spec=SystemMessage)
    msg.subtype = "init"
    msg.session_id = session_id
    return msg  # type: ignore[return-value]


async def _async_iter(*items: Any):
    """Yield items as an async iterable."""
    for item in items:
        yield item


class TestRunAgentSdkSuccess:
    async def test_returns_agent_result_on_success(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        findings = [{"file": "foo.rs", "line": 10, "summary": "bug"}]
        result_text = f"Found issues:\n```json\n{json.dumps(findings)}\n```"

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.return_value = _async_iter(
                _make_system_message(), _make_result_message(result_text)
            )
            result = await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="You are a reviewer.",
                user_prompt="Review this patch.",
            )

        assert isinstance(result, AgentResult)
        assert result.error is None
        assert result.findings == findings

    async def test_wall_time_is_positive(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.return_value = _async_iter(
                _make_result_message("No issues found.\n```json\n[]\n```")
            )
            result = await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
            )

        assert result.wall_time_seconds >= 0.0

    async def test_stdout_contains_result_text(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.return_value = _async_iter(
                _make_result_message("Analysis complete. No bugs.")
            )
            result = await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
            )

        assert "Analysis complete" in result.stdout

    async def test_empty_findings_when_no_json(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.return_value = _async_iter(_make_result_message("Looks clean, no issues."))
            result = await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
            )

        assert result.findings == []
        assert result.error is None

    async def test_model_stored_in_result(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.return_value = _async_iter(_make_result_message("[]"))
            result = await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
                model="claude-haiku-4-5",
            )

        assert result.model == "claude-haiku-4-5"


def test_agent_sdk_accepts_context_level() -> None:
    """run_agent_sdk must accept a context_level parameter for tool gating."""
    import inspect

    from bugeval.agent_sdk_runner import run_agent_sdk

    sig = inspect.signature(run_agent_sdk)
    assert "context_level" in sig.parameters, "run_agent_sdk must accept context_level"


class TestRunAgentSdkOptions:
    async def test_passes_cwd_to_query(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.return_value = _async_iter(_make_result_message("[]"))
            await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
            )

        call_kwargs = mock_query.call_args
        options = call_kwargs.kwargs.get("options") or call_kwargs.args[1]
        assert str(options.cwd) == str(tmp_path)

    async def test_passes_system_prompt_to_query(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.return_value = _async_iter(_make_result_message("[]"))
            await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="my-system-prompt",
                user_prompt="p",
            )

        call_kwargs = mock_query.call_args
        options = call_kwargs.kwargs.get("options") or call_kwargs.args[1]
        assert options.system_prompt == "my-system-prompt"

    async def test_max_turns_forwarded(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.return_value = _async_iter(_make_result_message("[]"))
            await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
                max_turns=5,
            )

        call_kwargs = mock_query.call_args
        options = call_kwargs.kwargs.get("options") or call_kwargs.args[1]
        assert options.max_turns == 5


class TestRunAgentSdkErrors:
    async def test_cli_not_found_returns_error(self, tmp_path: Path) -> None:
        from claude_agent_sdk import CLINotFoundError

        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.side_effect = CLINotFoundError("claude not found")
            result = await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
            )

        assert result.error is not None
        assert "not found" in result.error.lower()

    async def test_connection_error_returns_error(self, tmp_path: Path) -> None:
        from claude_agent_sdk import CLIConnectionError

        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.side_effect = CLIConnectionError("connection failed")
            result = await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
            )

        assert result.error is not None
        assert "connection" in result.error.lower()

    async def test_unexpected_exception_returns_error(self, tmp_path: Path) -> None:
        from bugeval.agent_sdk_runner import run_agent_sdk

        with patch("bugeval.agent_sdk_runner.query") as mock_query:
            mock_query.side_effect = RuntimeError("unexpected failure")
            result = await run_agent_sdk(
                repo_dir=tmp_path,
                system_prompt="s",
                user_prompt="p",
            )

        assert result.error is not None
        assert "unexpected failure" in result.error
