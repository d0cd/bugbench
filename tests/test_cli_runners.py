"""Tests for CLI runner dispatch (claude, gemini, codex)."""

from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path
from unittest.mock import MagicMock, patch

from bugeval.agent_runner import (
    _estimate_claude_cli_cost,
    _run_claude_cli,
    _run_codex_cli,
    _run_gemini_cli,
    _save_cli_transcript,
    build_system_prompt,
    run_agent_cli,
)
from bugeval.models import CaseKind, TestCase
from bugeval.result_models import ToolResult


def _make_case(**overrides: object) -> TestCase:
    defaults: dict[str, object] = {
        "id": "leo-001",
        "repo": "AleoNet/leo",
        "kind": CaseKind.bug,
        "base_commit": "abc123",
        "fix_commit": "def456",
        "introducing_pr_title": "Add new parser",
        "introducing_pr_body": "Implements expression parsing.",
        "introducing_pr_commit_messages": ["feat: add parser"],
    }
    defaults.update(overrides)
    return TestCase(**defaults)  # type: ignore[arg-type]


SAMPLE_DIFF = "--- a/foo.rs\n+++ b/foo.rs\n@@ -1,3 +1,3 @@\n-old\n+new\n"


class TestEstimateClaudeCliCost:
    def test_basic_cost(self) -> None:
        cost_info = {"input_tokens": 1000, "output_tokens": 500}
        cost = _estimate_claude_cli_cost(cost_info)
        # 1000 * 3/1e6 + 500 * 15/1e6 = 0.003 + 0.0075 = 0.0105
        assert abs(cost - 0.0105) < 1e-6

    def test_with_cache_tokens(self) -> None:
        cost_info = {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 2000,
            "cache_creation_input_tokens": 500,
        }
        cost = _estimate_claude_cli_cost(cost_info)
        expected = 1000 * 3.0 / 1e6 + 500 * 15.0 / 1e6 + 2000 * 0.30 / 1e6 + 500 * 3.75 / 1e6
        assert abs(cost - round(expected, 6)) < 1e-6

    def test_empty_cost_info(self) -> None:
        assert _estimate_claude_cli_cost({}) == 0.0

    def test_none_values_treated_as_zero(self) -> None:
        cost_info = {
            "input_tokens": None,
            "output_tokens": None,
        }
        assert _estimate_claude_cli_cost(cost_info) == 0.0


class TestRunClaudeCliJsonOutput:
    @patch("bugeval._cli_runners.subprocess.run")
    def test_parses_json_output(self, mock_run: MagicMock) -> None:
        output = {
            "result": '[{"file":"f.rs","line":1,"description":"bug here"}]',
            "cost": {"input_tokens": 100, "output_tokens": 50},
            "session_id": "sess-1",
            "is_error": False,
            "duration_ms": 5000,
            "num_turns": 3,
        }
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps(output),
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        result = _run_claude_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
        )
        assert result.case_id == "leo-001"
        assert result.tool == "agent-cli-claude"
        assert len(result.comments) == 1
        assert result.comments[0].file == "f.rs"
        assert result.cost_usd > 0
        assert result.error == ""

    @patch("bugeval._cli_runners.subprocess.run")
    def test_diff_only_disallows_tools(self, mock_run: MagicMock) -> None:
        output = {"result": "[]", "cost": {}}
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps(output),
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        _run_claude_cli(case, SAMPLE_DIFF, None, "diff-only", 300, system)
        cmd = mock_run.call_args[0][0]
        assert "--disallowedTools" in cmd

    @patch("bugeval._cli_runners.subprocess.run")
    def test_repo_context_allows_tools(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        output = {"result": "[]", "cost": {}}
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps(output),
            stderr="",
        )
        case = _make_case()
        repo = tmp_path / "repo"
        repo.mkdir()
        system = build_system_prompt("diff+repo")
        _run_claude_cli(
            case,
            SAMPLE_DIFF,
            repo,
            "diff+repo",
            300,
            system,
        )
        cmd = mock_run.call_args[0][0]
        assert "--allowedTools" in cmd
        assert "--disallowedTools" not in cmd

    @patch("bugeval._cli_runners.subprocess.run")
    def test_json_decode_error_fallback(self, mock_run: MagicMock) -> None:
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout='[{"file":"f.rs","line":1,"description":"plain text bug"}]',
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        result = _run_claude_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
        )
        # Should fall back to parsing stdout as plain text
        assert result.error == ""
        assert len(result.comments) == 1

    @patch("bugeval._cli_runners.subprocess.run")
    def test_timeout_returns_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = sp.TimeoutExpired(cmd="claude", timeout=300)
        case = _make_case()
        system = build_system_prompt("diff-only")
        result = _run_claude_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
        )
        assert "timed out" in result.error.lower()

    @patch("bugeval._cli_runners.subprocess.run")
    def test_not_found_returns_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("claude not found")
        case = _make_case()
        system = build_system_prompt("diff-only")
        result = _run_claude_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
        )
        assert "not found" in result.error.lower()


class TestRunClaudeCliTranscript:
    @patch("bugeval._cli_runners.subprocess.run")
    def test_transcript_saved(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        output = {"result": "[]", "cost": {"input_tokens": 10}}
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps(output),
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        transcript_dir = tmp_path / "transcripts"
        result = _run_claude_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
            transcript_dir=transcript_dir,
        )
        assert result.transcript_path != ""
        path = Path(result.transcript_path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["tool"] == "claude"
        assert "prompt" in data
        assert "output" in data


class TestRunGeminiCli:
    @patch("bugeval._cli_runners.subprocess.run")
    def test_correct_flags(self, mock_run: MagicMock) -> None:
        output = '[{"file":"g.rs","line":5,"description":"issue"}]'
        mock_run.return_value = sp.CompletedProcess(
            args=["gemini"],
            returncode=0,
            stdout=output,
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        result = _run_gemini_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
        )
        assert result.tool == "agent-cli-gemini"
        assert result.error == ""
        assert len(result.comments) == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "gemini"
        assert "-p" in cmd

    @patch("bugeval._cli_runners.subprocess.run")
    def test_uses_stdin(self, mock_run: MagicMock) -> None:
        """Verify Gemini CLI pipes prompt via stdin, not as CLI argument."""
        mock_run.return_value = sp.CompletedProcess(
            args=["gemini"],
            returncode=0,
            stdout="[]",
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        _run_gemini_cli(case, SAMPLE_DIFF, None, "diff-only", 300, system)
        call_kwargs = mock_run.call_args
        cmd = call_kwargs[0][0]
        # Prompt must NOT be in the command args
        for arg in cmd:
            assert "```diff" not in arg, "Prompt leaked into CLI args"
        # Prompt must be piped via input=
        assert call_kwargs.kwargs.get("input") is not None

    @patch("bugeval._cli_runners.subprocess.run")
    def test_repo_context_uses_yolo(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_run.return_value = sp.CompletedProcess(
            args=["gemini"],
            returncode=0,
            stdout="[]",
            stderr="",
        )
        case = _make_case()
        repo = tmp_path / "repo"
        repo.mkdir()
        system = build_system_prompt("diff+repo")
        _run_gemini_cli(
            case,
            SAMPLE_DIFF,
            repo,
            "diff+repo",
            300,
            system,
        )
        cmd = mock_run.call_args[0][0]
        assert "--yolo" in cmd

    @patch("bugeval._cli_runners.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = sp.TimeoutExpired(cmd="gemini", timeout=300)
        case = _make_case()
        system = build_system_prompt("diff-only")
        result = _run_gemini_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
        )
        assert "timed out" in result.error.lower()


class TestRunCodexCli:
    @patch("bugeval._cli_runners.subprocess.run")
    def test_correct_flags(self, mock_run: MagicMock) -> None:
        output = '[{"file":"c.rs","line":3,"description":"codex issue"}]'
        mock_run.return_value = sp.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout=output,
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        result = _run_codex_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
        )
        assert result.tool == "agent-cli-codex"
        assert result.error == ""
        assert len(result.comments) == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "--sandbox" in cmd
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "read-only"

    @patch("bugeval._cli_runners.subprocess.run")
    def test_uses_stdin(self, mock_run: MagicMock) -> None:
        """Verify Codex CLI pipes prompt via stdin, not as CLI argument."""
        mock_run.return_value = sp.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="[]",
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        _run_codex_cli(case, SAMPLE_DIFF, None, "diff-only", 300, system)
        call_kwargs = mock_run.call_args
        cmd = call_kwargs[0][0]
        # Prompt must NOT be in the command args
        for arg in cmd:
            assert "```diff" not in arg, "Prompt leaked into CLI args"
        # Prompt must be piped via input=
        assert call_kwargs.kwargs.get("input") is not None

    @patch("bugeval._cli_runners.subprocess.run")
    def test_repo_context_uses_workspace_write(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_run.return_value = sp.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="[]",
            stderr="",
        )
        case = _make_case()
        repo = tmp_path / "repo"
        repo.mkdir()
        system = build_system_prompt("diff+repo")
        _run_codex_cli(
            case,
            SAMPLE_DIFF,
            repo,
            "diff+repo",
            300,
            system,
        )
        cmd = mock_run.call_args[0][0]
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "workspace-write"

    @patch("bugeval._cli_runners.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = sp.TimeoutExpired(cmd="codex", timeout=300)
        case = _make_case()
        system = build_system_prompt("diff-only")
        result = _run_codex_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
        )
        assert "timed out" in result.error.lower()


class TestCliDispatchAllTools:
    @patch("bugeval._cli_runners._run_claude_cli")
    def test_dispatch_claude(self, mock_claude: MagicMock) -> None:
        mock_claude.return_value = ToolResult(
            case_id="leo-001",
            tool="agent-cli-claude",
        )
        case = _make_case()
        result = run_agent_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            cli_tool="claude",
        )
        assert result.tool == "agent-cli-claude"
        mock_claude.assert_called_once()

    @patch("bugeval._cli_runners._run_gemini_cli")
    def test_dispatch_gemini(self, mock_gemini: MagicMock) -> None:
        mock_gemini.return_value = ToolResult(
            case_id="leo-001",
            tool="agent-cli-gemini",
        )
        case = _make_case()
        result = run_agent_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            cli_tool="gemini",
        )
        assert result.tool == "agent-cli-gemini"
        mock_gemini.assert_called_once()

    @patch("bugeval._cli_runners._run_codex_cli")
    def test_dispatch_codex(self, mock_codex: MagicMock) -> None:
        mock_codex.return_value = ToolResult(
            case_id="leo-001",
            tool="agent-cli-codex",
        )
        case = _make_case()
        result = run_agent_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            cli_tool="codex",
        )
        assert result.tool == "agent-cli-codex"
        mock_codex.assert_called_once()

    def test_dispatch_unknown(self) -> None:
        case = _make_case()
        result = run_agent_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            cli_tool="unknown",
        )
        assert "Unknown CLI tool" in result.error


class TestSaveCliTranscript:
    def test_writes_json_file(self, tmp_path: Path) -> None:
        td = tmp_path / "transcripts"
        path = _save_cli_transcript(
            td,
            "leo-001",
            "claude",
            "my prompt",
            {"result": "ok"},
        )
        assert Path(path).exists()
        data = json.loads(Path(path).read_text())
        assert data["tool"] == "claude"
        assert data["output"] == {"result": "ok"}

    def test_truncates_long_prompt(self, tmp_path: Path) -> None:
        td = tmp_path / "transcripts"
        long_prompt = "x" * 10000
        path = _save_cli_transcript(
            td,
            "leo-001",
            "claude",
            long_prompt,
            "out",
        )
        data = json.loads(Path(path).read_text())
        assert len(data["prompt"]) == 5000


class TestCliModelOverride:
    @patch("bugeval._cli_runners.subprocess.run")
    def test_claude_includes_model_flag(self, mock_run: MagicMock) -> None:
        output = {"result": "[]", "cost": {}}
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps(output),
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        _run_claude_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
            model="claude-opus-4-6",
        )
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"

    @patch("bugeval._cli_runners.subprocess.run")
    def test_claude_omits_model_flag_when_empty(
        self,
        mock_run: MagicMock,
    ) -> None:
        output = {"result": "[]", "cost": {}}
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps(output),
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        _run_claude_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
            model="",
        )
        cmd = mock_run.call_args[0][0]
        assert "--model" not in cmd

    @patch("bugeval._cli_runners.subprocess.run")
    def test_gemini_includes_model_flag(self, mock_run: MagicMock) -> None:
        mock_run.return_value = sp.CompletedProcess(
            args=["gemini"],
            returncode=0,
            stdout="[]",
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        _run_gemini_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
            model="gemini-2.5-pro",
        )
        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "gemini-2.5-pro"

    @patch("bugeval._cli_runners.subprocess.run")
    def test_codex_includes_model_flag(self, mock_run: MagicMock) -> None:
        mock_run.return_value = sp.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="[]",
            stderr="",
        )
        case = _make_case()
        system = build_system_prompt("diff-only")
        _run_codex_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            300,
            system,
            model="o3",
        )
        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "o3"

    @patch("bugeval._cli_runners.subprocess.run")
    def test_run_agent_cli_passes_model(self, mock_run: MagicMock) -> None:
        """Verify run_agent_cli threads model to the underlying CLI runner."""
        output = {"result": "[]", "cost": {}}
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps(output),
            stderr="",
        )
        case = _make_case()
        run_agent_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            cli_tool="claude",
            timeout=60,
            model="claude-opus-4-6",
        )
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"


class TestOpenPrsHelp:
    def test_open_prs_help(self) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["open-prs", "--help"])
        assert result.exit_code == 0
        assert "--tool" in result.output
        assert "--org" in result.output
        assert "--concurrency" in result.output


class TestOpenPrsSkipLogic:
    def test_open_prs_skips_pending_review(self, tmp_path: Path) -> None:
        """Cases with pr_state='pending-review' must NOT be retried."""
        from click.testing import CliRunner

        from bugeval.cli import cli
        from bugeval.io import save_case, save_result
        from bugeval.models import TestCase
        from bugeval.result_models import ToolResult

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        run_dir = tmp_path / "run"
        results_dir = run_dir / "results"
        results_dir.mkdir(parents=True)

        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        pending = ToolResult(
            case_id="leo-001",
            tool="copilot",
            pr_state="pending-review",
            pr_number=42,
        )
        save_result(pending, results_dir / "leo-001--copilot.yaml")

        with (
            patch(
                "bugeval.copilot_runner.open_pr_for_case",
            ) as mock_open,
            patch(
                "bugeval.evaluate.ensure_per_tool_clone",
                return_value=tmp_path / "repo",
            ),
            patch(
                "bugeval.cli._preflight_open_prs",
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "open-prs",
                    "--tool",
                    "copilot",
                    "--cases-dir",
                    str(tmp_path / "cases"),
                    "--run-dir",
                    str(run_dir),
                    "--repo-dir",
                    str(tmp_path / "repo"),
                    "--org",
                    "TestOrg",
                ],
            )

        assert result.exit_code == 0
        mock_open.assert_not_called()
        assert "All cases already have PRs open" in result.output

    def test_open_prs_retries_errors(self, tmp_path: Path) -> None:
        """Cases with an error result should be retried (file deleted)."""
        from click.testing import CliRunner

        from bugeval.cli import cli
        from bugeval.io import save_case, save_result
        from bugeval.models import TestCase
        from bugeval.result_models import ToolResult

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        run_dir = tmp_path / "run"
        results_dir = run_dir / "results"
        results_dir.mkdir(parents=True)
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        error_result = ToolResult(
            case_id="leo-001",
            tool="copilot",
            error="git apply failed",
        )
        save_result(error_result, results_dir / "leo-001--copilot.yaml")

        success_result = ToolResult(
            case_id="leo-001",
            tool="copilot",
            pr_state="pending-review",
            pr_number=99,
        )

        with (
            patch(
                "bugeval.copilot_runner.open_pr_for_case",
                return_value=success_result,
            ) as mock_open,
            patch(
                "bugeval.evaluate.ensure_per_tool_clone",
                return_value=repo_dir,
            ),
            patch(
                "bugeval.cli._preflight_open_prs",
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "open-prs",
                    "--tool",
                    "copilot",
                    "--cases-dir",
                    str(tmp_path / "cases"),
                    "--run-dir",
                    str(run_dir),
                    "--repo-dir",
                    str(repo_dir),
                    "--org",
                    "TestOrg",
                ],
            )

        assert result.exit_code == 0
        mock_open.assert_called_once()
        assert "Opened PR #99" in result.output

    def test_open_prs_no_overwrite_pending_on_save(
        self,
        tmp_path: Path,
    ) -> None:
        """Race protection: don't overwrite pending-review on save."""
        from click.testing import CliRunner

        from bugeval.cli import cli
        from bugeval.io import save_case, save_result
        from bugeval.models import TestCase
        from bugeval.result_models import ToolResult

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        run_dir = tmp_path / "run"
        results_dir = run_dir / "results"
        results_dir.mkdir(parents=True)
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        # No result file initially -> case is pending

        # Simulate race: another worker writes pending-review
        # before our save happens
        def fake_open(c: TestCase, r: Path, t: str, o: str) -> ToolResult:
            # Simulate another process writing the file first
            racing = ToolResult(
                case_id="leo-001",
                tool="copilot",
                pr_state="pending-review",
                pr_number=42,
            )
            save_result(racing, results_dir / "leo-001--copilot.yaml")
            # Our result is an error
            return ToolResult(
                case_id="leo-001",
                tool="copilot",
                error="something failed",
            )

        with (
            patch(
                "bugeval.copilot_runner.open_pr_for_case",
                side_effect=fake_open,
            ),
            patch(
                "bugeval.evaluate.ensure_per_tool_clone",
                return_value=repo_dir,
            ),
            patch(
                "bugeval.cli._preflight_open_prs",
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "open-prs",
                    "--tool",
                    "copilot",
                    "--cases-dir",
                    str(tmp_path / "cases"),
                    "--run-dir",
                    str(run_dir),
                    "--repo-dir",
                    str(repo_dir),
                    "--org",
                    "TestOrg",
                ],
            )

        assert result.exit_code == 0
        # The pending-review result should NOT be overwritten
        from bugeval.io import load_result

        saved = load_result(results_dir / "leo-001--copilot.yaml")
        assert saved.pr_state == "pending-review"
        assert saved.pr_number == 42


class TestScrapePrsHelp:
    def test_scrape_prs_help(self) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["scrape-prs", "--help"])
        assert result.exit_code == 0
        assert "--run-dir" in result.output
        assert "--close" in result.output
        assert "--org" in result.output
        assert "--cases-dir" in result.output

    def test_scrape_prs_no_pending(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        run_dir = tmp_path / "run"
        results_dir = run_dir / "results"
        results_dir.mkdir(parents=True)

        # Create a case YAML
        from bugeval.io import save_case
        from bugeval.models import TestCase

        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "scrape-prs",
                "--run-dir",
                str(run_dir),
                "--cases-dir",
                str(tmp_path / "cases"),
                "--org",
                "TestOrg",
            ],
        )
        assert result.exit_code == 0
        assert "0 reviewed" in result.output
        assert "0 still pending" in result.output

    def test_scrape_prs_processes_pending(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli
        from bugeval.io import save_case, save_result
        from bugeval.models import TestCase
        from bugeval.result_models import ToolResult

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        run_dir = tmp_path / "run"
        results_dir = run_dir / "results"
        results_dir.mkdir(parents=True)

        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        pending_result = ToolResult(
            case_id="leo-001",
            tool="copilot",
            pr_state="pending-review",
            pr_number=42,
        )
        save_result(pending_result, results_dir / "leo-001--copilot.yaml")

        scraped = ToolResult(
            case_id="leo-001",
            tool="copilot",
            pr_state="reviewed",
            pr_number=42,
        )

        with patch(
            "bugeval.copilot_runner.scrape_pr_for_case",
            return_value=scraped,
        ) as mock_scrape:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "scrape-prs",
                    "--run-dir",
                    str(run_dir),
                    "--cases-dir",
                    str(tmp_path / "cases"),
                    "--org",
                    "TestOrg",
                ],
            )

        assert result.exit_code == 0
        assert "1 reviewed" in result.output
        assert "0 still pending" in result.output
        mock_scrape.assert_called_once()
        call_kwargs = mock_scrape.call_args
        assert call_kwargs[1]["close"] is True
        assert call_kwargs[0][1] == "TestOrg/leo-copilot"


class TestEvaluateDispatchGeminiCodex:
    def test_dispatch_agent_cli_gemini(self, tmp_path: Path) -> None:
        from bugeval.evaluate import process_case

        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-cli-gemini",
        )

        with (
            patch(
                "bugeval.evaluate.get_diff_for_case",
                return_value="diff",
            ),
            patch(
                "bugeval.agent_runner.run_agent_cli",
                return_value=fake_result,
            ) as mock_cli,
        ):
            result = process_case(
                case,
                "agent-cli-gemini",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "agent-cli-gemini"
        call_kwargs = mock_cli.call_args
        assert call_kwargs.kwargs.get("cli_tool") == "gemini"

    def test_dispatch_agent_cli_codex(self, tmp_path: Path) -> None:
        from bugeval.evaluate import process_case

        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-cli-codex",
        )

        with (
            patch(
                "bugeval.evaluate.get_diff_for_case",
                return_value="diff",
            ),
            patch(
                "bugeval.agent_runner.run_agent_cli",
                return_value=fake_result,
            ) as mock_cli,
        ):
            result = process_case(
                case,
                "agent-cli-codex",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "agent-cli-codex"
        call_kwargs = mock_cli.call_args
        assert call_kwargs.kwargs.get("cli_tool") == "codex"


class TestCleanupPrsHelp:
    def test_cleanup_prs_help(self) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["cleanup-prs", "--help"])
        assert result.exit_code == 0
        assert "--org" in result.output
        assert "--repo" in result.output
        assert "--tool" in result.output
        assert "--dry-run" in result.output

    def test_cleanup_prs_all_tools(self) -> None:
        """Cleanup across all PR tools when --tool is not specified."""
        from click.testing import CliRunner

        from bugeval.cli import cli

        pr_list_json = json.dumps(
            [
                {
                    "number": 10,
                    "headRefName": "review-abc",
                    "baseRefName": "base-abc",
                },
            ]
        )
        refs_json = json.dumps(
            [
                {"ref": "refs/heads/base-orphan"},
                {"ref": "refs/heads/review-orphan"},
                {"ref": "refs/heads/main"},
            ]
        )

        with patch(
            "bugeval.mine.run_gh",
        ) as mock_gh:
            # gh pr list returns PRs; gh api returns refs
            def side_effect(*args: str) -> str:
                joined = " ".join(args)
                if "pr list" in joined:
                    return pr_list_json
                if "git/refs/heads" in joined and "DELETE" not in joined:
                    return refs_json
                return ""

            mock_gh.side_effect = side_effect

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "cleanup-prs",
                    "--org",
                    "TestOrg",
                    "--repo",
                    "ProvableHQ/leo",
                ],
            )

        assert result.exit_code == 0
        # Should process all 3 PR tools
        assert "TestOrg/leo-copilot" in result.output
        assert "TestOrg/leo-greptile" in result.output
        assert "TestOrg/leo-coderabbit" in result.output

    def test_cleanup_prs_specific_tool(self) -> None:
        """Cleanup only a specific tool."""
        from click.testing import CliRunner

        from bugeval.cli import cli

        pr_list_json = json.dumps([])
        refs_json = json.dumps(
            [
                {"ref": "refs/heads/base-stale"},
            ]
        )

        with patch(
            "bugeval.mine.run_gh",
        ) as mock_gh:

            def side_effect(*args: str) -> str:
                joined = " ".join(args)
                if "pr list" in joined:
                    return pr_list_json
                if "git/refs/heads" in joined and "DELETE" not in joined:
                    return refs_json
                return ""

            mock_gh.side_effect = side_effect

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "cleanup-prs",
                    "--org",
                    "TestOrg",
                    "--repo",
                    "ProvableHQ/leo",
                    "--tool",
                    "copilot",
                ],
            )

        assert result.exit_code == 0
        assert "TestOrg/leo-copilot" in result.output
        assert "greptile" not in result.output

    def test_cleanup_prs_dry_run(self) -> None:
        """Dry run should not close PRs or delete branches."""
        from click.testing import CliRunner

        from bugeval.cli import cli

        pr_list_json = json.dumps(
            [
                {
                    "number": 5,
                    "headRefName": "review-xyz",
                    "baseRefName": "base-xyz",
                },
            ]
        )
        refs_json = json.dumps(
            [
                {"ref": "refs/heads/base-orphan"},
            ]
        )

        with patch(
            "bugeval.mine.run_gh",
        ) as mock_gh:

            def side_effect(*args: str) -> str:
                joined = " ".join(args)
                if "pr list" in joined:
                    return pr_list_json
                if "git/refs/heads" in joined and "DELETE" not in joined:
                    return refs_json
                return ""

            mock_gh.side_effect = side_effect

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "cleanup-prs",
                    "--org",
                    "TestOrg",
                    "--repo",
                    "ProvableHQ/leo",
                    "--tool",
                    "copilot",
                    "--dry-run",
                ],
            )

        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        # close_eval_pr and _delete_remote_branch should NOT
        # have been called (no DELETE calls)
        for call in mock_gh.call_args_list:
            args_joined = " ".join(call[0])
            assert "DELETE" not in args_joined
            assert "pr close" not in args_joined


class TestOpenPrsPreflight:
    def test_open_prs_fails_no_cases(self, tmp_path: Path) -> None:
        """Empty cases dir -> error message, exit code 1."""
        from click.testing import CliRunner

        from bugeval.cli import cli

        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "open-prs",
                "--tool",
                "copilot",
                "--cases-dir",
                str(cases_dir),
                "--run-dir",
                str(tmp_path / "run"),
                "--repo-dir",
                str(repo_dir),
                "--org",
                "TestOrg",
            ],
        )
        assert result.exit_code == 1
        assert "No active cases found" in result.output

    def test_open_prs_fails_bad_repo_dir(
        self,
        tmp_path: Path,
    ) -> None:
        """Non-existent repo dir -> error message, exit code 1."""
        from click.testing import CliRunner

        from bugeval.cli import cli
        from bugeval.io import save_case
        from bugeval.models import TestCase

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "open-prs",
                "--tool",
                "copilot",
                "--cases-dir",
                str(tmp_path / "cases"),
                "--run-dir",
                str(tmp_path / "run"),
                "--repo-dir",
                str(tmp_path / "nonexistent"),
                "--org",
                "TestOrg",
            ],
        )
        assert result.exit_code == 1
        assert "not a git repository" in result.output

    def test_open_prs_fails_repo_dir_no_git(
        self,
        tmp_path: Path,
    ) -> None:
        """Repo dir exists but has no .git -> error."""
        from click.testing import CliRunner

        from bugeval.cli import cli
        from bugeval.io import save_case
        from bugeval.models import TestCase

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        # No .git dir

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "open-prs",
                "--tool",
                "copilot",
                "--cases-dir",
                str(tmp_path / "cases"),
                "--run-dir",
                str(tmp_path / "run"),
                "--repo-dir",
                str(repo_dir),
                "--org",
                "TestOrg",
            ],
        )
        assert result.exit_code == 1
        assert "not a git repository" in result.output

    def test_open_prs_fails_gh_auth(
        self,
        tmp_path: Path,
    ) -> None:
        """gh auth failure -> error message, exit code 1."""
        from click.testing import CliRunner

        from bugeval.cli import cli
        from bugeval.io import save_case
        from bugeval.models import TestCase

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        with patch(
            "bugeval.cli.subprocess.run",
            side_effect=sp.CalledProcessError(1, "gh auth status"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "open-prs",
                    "--tool",
                    "copilot",
                    "--cases-dir",
                    str(tmp_path / "cases"),
                    "--run-dir",
                    str(tmp_path / "run"),
                    "--repo-dir",
                    str(repo_dir),
                    "--org",
                    "TestOrg",
                ],
            )
        assert result.exit_code == 1
        assert "GitHub auth failed" in result.output

    def test_open_prs_warns_missing_tool_repo(
        self,
        tmp_path: Path,
    ) -> None:
        """Missing tool repo -> warning but continues."""
        from click.testing import CliRunner

        from bugeval.cli import cli
        from bugeval.io import save_case
        from bugeval.models import TestCase

        cases_dir = tmp_path / "cases" / "leo"
        cases_dir.mkdir(parents=True)
        case = TestCase(
            id="leo-001",
            repo="AleoNet/leo",
            kind="bug",
            base_commit="abc123",
            fix_commit="def456",
        )
        save_case(case, cases_dir / "leo-001.yaml")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        call_count = 0

        def mock_sp_run(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            cmd = args[0] if args else kwargs.get("args", [])
            joined = " ".join(str(c) for c in cmd)  # type: ignore[union-attr]
            if "auth status" in joined:
                return sp.CompletedProcess(
                    args=cmd,
                    returncode=0,
                )
            if "repo view" in joined:
                raise sp.CalledProcessError(1, joined)
            return sp.CompletedProcess(args=cmd, returncode=0)

        with (
            patch(
                "bugeval.cli.subprocess.run",
                side_effect=mock_sp_run,
            ),
            patch(
                "bugeval.copilot_runner.open_pr_for_case",
            ) as mock_open,
            patch(
                "bugeval.evaluate.ensure_per_tool_clone",
                return_value=repo_dir,
            ),
        ):
            mock_open.return_value = MagicMock(
                pr_number=1,
                pr_state="pending-review",
                case_id="leo-001",
                tool="copilot",
                error="",
            )
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "open-prs",
                    "--tool",
                    "copilot",
                    "--cases-dir",
                    str(tmp_path / "cases"),
                    "--run-dir",
                    str(tmp_path / "run"),
                    "--repo-dir",
                    str(repo_dir),
                    "--org",
                    "TestOrg",
                ],
            )
        assert result.exit_code == 0
        assert "not found" in result.output
        assert "created automatically" in result.output
