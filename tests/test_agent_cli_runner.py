"""Tests for agent_cli_runner."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from bugeval.agent_cli_runner import _parse_cli_findings, run_claude_cli


def test_run_claude_cli_success(tmp_path: Path) -> None:
    findings_json = '[{"file": "src/main.rs", "line": 10, "summary": "bug"}]'
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = f"Some output\n```json\n{findings_json}\n```\n"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(tmp_path, "review this patch")

    assert result.error is None
    assert len(result.findings) == 1
    assert result.findings[0]["file"] == "src/main.rs"
    assert result.model == "claude-sonnet-4-6"
    assert result.wall_time_seconds >= 0


def test_run_claude_cli_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5)):
        result = run_claude_cli(tmp_path, "prompt", timeout_seconds=5)

    assert result.error == "timeout"
    assert result.findings == []


def test_run_claude_cli_nonzero_exit(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "claude: command not found"

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(tmp_path, "prompt")

    assert result.error is not None
    assert "code 1" in result.error
    assert result.findings == []


def test_parse_cli_findings_with_json_array() -> None:
    stdout = 'Here are the findings:\n```json\n[{"file": "a.rs", "line": 5, "summary": "x"}]\n```'
    findings = _parse_cli_findings(stdout)
    assert len(findings) == 1
    assert findings[0]["file"] == "a.rs"


def test_parse_cli_findings_empty_output() -> None:
    findings = _parse_cli_findings("")
    assert findings == []


def test_run_claude_cli_passes_max_turns(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_claude_cli(tmp_path, "prompt", max_turns=5)

    call_args = mock_run.call_args[0][0]
    assert "--max-turns" in call_args
    assert "5" in call_args
