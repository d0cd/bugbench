"""Tests for agent_api_runner."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bugeval.agent_api_runner import (
    _parse_api_findings,
    execute_tool,
    run_agent_api,
)

# ---------------------------------------------------------------------------
# execute_tool tests
# ---------------------------------------------------------------------------


def test_execute_tool_read_file(tmp_path: Path) -> None:
    test_file = tmp_path / "src" / "main.rs"
    test_file.parent.mkdir()
    test_file.write_text("fn main() {}")
    result = execute_tool("read_file", {"path": "src/main.rs"}, tmp_path)
    assert "fn main()" in result


def test_execute_tool_list_directory(tmp_path: Path) -> None:
    (tmp_path / "alpha.rs").write_text("")
    (tmp_path / "beta.rs").write_text("")
    result = execute_tool("list_directory", {"path": "."}, tmp_path)
    assert "alpha.rs" in result
    assert "beta.rs" in result


def test_execute_tool_search_code(tmp_path: Path) -> None:
    test_file = tmp_path / "foo.rs"
    test_file.write_text("let x = panic!();\n")
    result = execute_tool("search_code", {"pattern": "panic", "path": "."}, tmp_path)
    assert "panic" in result


def test_execute_tool_path_traversal_blocked(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Path traversal"):
        execute_tool("read_file", {"path": "../../etc/passwd"}, tmp_path)


def test_execute_tool_unknown_tool(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown tool"):
        execute_tool("rm_rf", {"path": "."}, tmp_path)


# ---------------------------------------------------------------------------
# run_agent_api tests
# ---------------------------------------------------------------------------


def _make_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    block.model_dump.return_value = {"type": "text", "text": text}
    return block


def _make_tool_use_block(name: str, input_data: dict, block_id: str = "tu_1") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = input_data
    block.id = block_id
    block.model_dump.return_value = {"type": "tool_use", "name": name, "input": input_data}
    return block


def _make_response(
    content: list, stop_reason: str, input_tokens: int = 100, output_tokens: int = 50
) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


def test_run_agent_api_single_turn(tmp_path: Path) -> None:
    findings_text = '[{"file": "a.rs", "line": 1, "summary": "bug"}]'
    response = _make_response(
        content=[_make_text_block(findings_text)],
        stop_reason="end_turn",
    )

    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("bugeval.agent_api_runner.Anthropic", return_value=mock_client):
        result = run_agent_api(tmp_path, "system", "user prompt")

    assert result.turns == 1
    assert len(result.findings) == 1
    assert result.findings[0]["file"] == "a.rs"
    assert result.token_count == 150
    assert result.error is None


def test_run_agent_api_multi_turn(tmp_path: Path) -> None:
    # First response: tool_use (list_directory)
    tool_block = _make_tool_use_block("list_directory", {"path": "."}, "tu_1")
    first_response = _make_response(content=[tool_block], stop_reason="tool_use")

    # Second response: end_turn with findings
    findings_text = '[{"file": "b.rs", "line": 5, "summary": "x"}]'
    second_response = _make_response(
        content=[_make_text_block(findings_text)],
        stop_reason="end_turn",
    )

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [first_response, second_response]

    with patch("bugeval.agent_api_runner.Anthropic", return_value=mock_client):
        result = run_agent_api(tmp_path, "system", "user prompt")

    assert result.turns == 2
    assert len(result.findings) == 1
    assert result.findings[0]["file"] == "b.rs"


def test_run_agent_api_max_turns_cutoff(tmp_path: Path) -> None:
    # Always return tool_use → loop should stop at max_turns
    tool_block = _make_tool_use_block("list_directory", {"path": "."})
    tool_response = _make_response(content=[tool_block], stop_reason="tool_use")

    mock_client = MagicMock()
    mock_client.messages.create.return_value = tool_response

    with patch("bugeval.agent_api_runner.Anthropic", return_value=mock_client):
        result = run_agent_api(tmp_path, "system", "user prompt", max_turns=3)

    assert result.turns == 3
    assert mock_client.messages.create.call_count == 3


def test_parse_api_findings_with_json() -> None:
    text = 'Here are findings:\n```json\n[{"file": "x.rs", "line": 2, "summary": "y"}]\n```'
    findings = _parse_api_findings(text)
    assert len(findings) == 1
    assert findings[0]["file"] == "x.rs"


def test_parse_api_findings_empty() -> None:
    assert _parse_api_findings("No bugs found.") == []
