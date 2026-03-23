"""Tests for evaluate module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml

from bugeval.evaluate import (
    ensure_per_tool_clone,
    evaluate_tool,
    get_diff_for_case,
    load_tool_timeouts,
    process_case,
    resolve_timeout,
    result_filename,
)
from bugeval.models import CaseKind, GroundTruth, TestCase
from bugeval.result_models import Comment, ToolResult


def _make_case(**overrides: object) -> TestCase:
    defaults: dict[str, object] = {
        "id": "leo-001",
        "repo": "AleoNet/leo",
        "kind": CaseKind.bug,
        "base_commit": "abc123",
        "fix_commit": "def456",
    }
    defaults.update(overrides)
    return TestCase(**defaults)  # type: ignore[arg-type]


class TestResultFilename:
    def test_with_context(self) -> None:
        assert result_filename("leo-001", "agent", "diff-only") == (
            "leo-001--agent--diff-only.yaml"
        )

    def test_without_context(self) -> None:
        assert result_filename("leo-001", "greptile", "") == ("leo-001--greptile.yaml")

    def test_complex_context(self) -> None:
        assert result_filename("snarkVM-042", "agent", "diff+repo+domain") == (
            "snarkVM-042--agent--diff+repo+domain.yaml"
        )


class TestProcessCase:
    def test_dispatches_agent(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent",
            context_level="diff-only",
            comments=[Comment(file="f.rs", line=1, body="bug")],
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="some diff"),
            patch(
                "bugeval.agent_runner.run_anthropic_api",
                return_value=fake_result,
            ),
        ):
            result = process_case(case, "agent", "diff-only", repo_dir, run_dir, 300)

        assert result.tool == "agent"
        assert result.case_id == "leo-001"

    def test_dispatches_greptile(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="greptile",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.greptile_runner.run_greptile",
                return_value=fake_result,
            ) as mock_greptile,
        ):
            result = process_case(
                case,
                "greptile",
                "",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "greptile"
        mock_greptile.assert_called_once()
        call_kwargs = mock_greptile.call_args
        assert call_kwargs.kwargs.get("transcript_dir") is not None

    def test_dispatches_copilot(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="copilot",
            comments=[Comment(file="f.rs", line=1, body="bug")],
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="some diff"),
            patch(
                "bugeval.copilot_runner.run_copilot",
                return_value=fake_result,
            ) as mock_copilot,
        ):
            result = process_case(
                case,
                "copilot",
                "",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "copilot"
        assert result.case_id == "leo-001"
        mock_copilot.assert_called_once()
        call_kwargs = mock_copilot.call_args
        assert call_kwargs.kwargs.get("transcript_dir") is not None

    def test_dispatches_agent_cli(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-cli-claude",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.run_agent_cli",
                return_value=fake_result,
            ) as mock_cli,
        ):
            result = process_case(
                case,
                "agent-cli",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "agent-cli-claude"
        mock_cli.assert_called_once()

    def test_dispatches_agent_sdk(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-sdk",
            error="agent-sdk runner not yet implemented",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.run_agent_sdk",
                return_value=fake_result,
            ) as mock_sdk,
        ):
            result = process_case(
                case,
                "agent-sdk",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "agent-sdk"
        mock_sdk.assert_called_once()

    def test_dispatches_agent_sdk_docker(self, tmp_path: Path) -> None:
        """When docker=True, agent-sdk routes to Docker wrapper."""
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-sdk",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.evaluate._run_sdk_in_docker",
                return_value=fake_result,
            ) as mock_docker,
        ):
            result = process_case(
                case,
                "agent-sdk",
                "diff-only",
                repo_dir,
                run_dir,
                300,
                docker=True,
                docker_image="my-img",
            )

        assert result.tool == "agent-sdk"
        mock_docker.assert_called_once()
        kw = mock_docker.call_args.kwargs
        assert kw["docker_image"] == "my-img"
        assert kw["tool_name"] == "agent-sdk"

    def test_dispatches_coderabbit(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="coderabbit",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.coderabbit_runner.run_coderabbit",
                return_value=fake_result,
            ) as mock_coderabbit,
        ):
            result = process_case(
                case,
                "coderabbit",
                "",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "coderabbit"
        mock_coderabbit.assert_called_once()
        call_kwargs = mock_coderabbit.call_args
        assert call_kwargs.kwargs.get("transcript_dir") is not None

    def test_dispatches_agent_gemini(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-gemini",
            context_level="diff-only",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.run_google_api",
                return_value=fake_result,
            ) as mock_google,
        ):
            result = process_case(
                case,
                "agent-gemini",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "agent-gemini"
        mock_google.assert_called_once()

    def test_dispatches_agent_openai(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-openai",
            context_level="diff-only",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.run_openai_api",
                return_value=fake_result,
            ) as mock_openai,
        ):
            result = process_case(
                case,
                "agent-openai",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        assert result.tool == "agent-openai"
        mock_openai.assert_called_once()

    def test_dispatches_agent_sdk_2pass(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-sdk-2pass",
            context_level="diff-only",
        )

        with (
            patch(
                "bugeval.evaluate.get_diff_for_case",
                return_value="diff",
            ),
            patch(
                "bugeval.agent_runner.run_agent_sdk_2pass",
                return_value=fake_result,
            ) as mock_2pass,
        ):
            result = process_case(
                case,
                "agent-sdk-2pass",
                "diff-only",
                repo_dir,
                run_dir,
                600,
            )

        assert result.tool == "agent-sdk-2pass"
        mock_2pass.assert_called_once()
        # Runners no longer receive docker params
        kw = mock_2pass.call_args.kwargs
        assert "docker" not in kw

    def test_dispatches_agent_sdk_2pass_with_docker(
        self,
        tmp_path: Path,
    ) -> None:
        """When docker=True, SDK tools are dispatched via Docker wrapper."""
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-sdk-2pass",
            context_level="diff-only",
        )

        with (
            patch(
                "bugeval.evaluate.get_diff_for_case",
                return_value="diff",
            ),
            patch(
                "bugeval.evaluate._run_sdk_in_docker",
                return_value=fake_result,
            ) as mock_docker,
        ):
            result = process_case(
                case,
                "agent-sdk-2pass",
                "diff-only",
                repo_dir,
                run_dir,
                600,
                docker=True,
                docker_image="custom-img",
            )

        assert result.tool == "agent-sdk-2pass"
        mock_docker.assert_called_once()
        kw = mock_docker.call_args.kwargs
        assert kw["docker_image"] == "custom-img"
        assert kw["tool_name"] == "agent-sdk-2pass"

    def test_docker_agent_sdk_2pass_is_unsupported(
        self,
        tmp_path: Path,
    ) -> None:
        """Old tool name docker-agent-sdk-2pass should be unsupported."""
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with patch(
            "bugeval.evaluate.get_diff_for_case",
            return_value="diff",
        ):
            result = process_case(
                case,
                "docker-agent-sdk-2pass",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        assert "Unsupported tool" in result.error

    def test_unsupported_tool(self, tmp_path: Path) -> None:
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with patch("bugeval.evaluate.get_diff_for_case", return_value="diff"):
            result = process_case(
                case,
                "unknown_tool",
                "",
                repo_dir,
                run_dir,
                300,
            )

        assert "Unsupported tool" in result.error


class TestEvaluateTool:
    def test_checkpoint_skips_done(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        # Write a case file
        case = _make_case()
        import yaml

        with open(cases_dir / "leo-001.yaml", "w") as f:
            yaml.safe_dump(case.model_dump(mode="json"), f)

        # Pre-populate checkpoint with this case done
        ckpt = run_dir / "checkpoint.json"
        ckpt.write_text(json.dumps(["leo-001::agent::diff-only"]))

        with patch("bugeval.evaluate.process_case") as mock_process:
            evaluate_tool(
                "agent",
                cases_dir,
                run_dir,
                "diff-only",
                Path("."),
                1,
                300,
                False,
            )

        # process_case should NOT have been called
        mock_process.assert_not_called()

    def test_dry_run_skips_processing(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        run_dir = tmp_path / "run"

        case = _make_case()
        import yaml

        with open(cases_dir / "leo-001.yaml", "w") as f:
            yaml.safe_dump(case.model_dump(mode="json"), f)

        with patch("bugeval.evaluate.process_case") as mock_process:
            evaluate_tool(
                "agent",
                cases_dir,
                run_dir,
                "diff-only",
                Path("."),
                1,
                300,
                True,
            )

        mock_process.assert_not_called()
        # run_dir should have been created
        assert run_dir.exists()

    def test_no_cases_returns_early(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "empty_cases"
        cases_dir.mkdir()
        run_dir = tmp_path / "run"

        with patch("bugeval.evaluate.process_case") as mock_process:
            evaluate_tool(
                "agent",
                cases_dir,
                run_dir,
                "",
                Path("."),
                1,
                300,
                False,
            )

        mock_process.assert_not_called()


class TestGetDiffForCase:
    def test_uses_introducing_commit(self) -> None:
        """When truth.introducing_commit is set, diff that commit vs its parent."""
        case = _make_case(
            truth=GroundTruth(
                introducing_commit="intro999",
                fix_pr_numbers=[1],
            ),
        )

        with patch("bugeval.evaluate.get_diff", return_value="intro diff") as mock_diff:
            result = get_diff_for_case(case, Path("/repo"))

        assert result == "intro diff"
        mock_diff.assert_called_once_with(
            "intro999~1",
            "intro999",
            cwd=Path("/repo"),
        )

    def test_no_introducing_returns_empty(self) -> None:
        """When no introducing_commit, return empty string (no fallback to fix diff)."""
        case = _make_case()
        result = get_diff_for_case(case, Path("/repo"))
        assert result == ""

    def test_no_commits_returns_empty(self) -> None:
        """When no fix or base commit and no introducing, return empty string."""
        case = _make_case(fix_commit="", base_commit="")
        result = get_diff_for_case(case, Path("/repo"))
        assert result == ""

    def test_truth_none_returns_empty(self) -> None:
        """When truth is None, return empty (no fallback to fix diff)."""
        case = _make_case(truth=None)
        result = get_diff_for_case(case, Path("/repo"))
        assert result == ""


class TestEvaluatePassesTranscriptDir:
    def test_agent_receives_transcript_dir(self, tmp_path: Path) -> None:
        """Verify process_case passes transcript_dir to run_anthropic_api."""
        case = _make_case(
            truth=GroundTruth(introducing_commit="intro999"),
        )
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent",
            context_level="diff-only",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.run_anthropic_api",
                return_value=fake_result,
            ) as mock_api,
        ):
            process_case(
                case,
                "agent",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        # run_anthropic_api should have been called with transcript_dir set
        call_kwargs = mock_api.call_args
        assert call_kwargs.kwargs.get("transcript_dir") is not None
        transcript_dir = call_kwargs.kwargs["transcript_dir"]
        assert transcript_dir == run_dir / "transcripts"
        assert transcript_dir.exists()


class TestEvaluateAgentWorkspaceSetup:
    def test_agent_calls_setup_workspace_for_repo_context(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify that agent with diff+repo context sets up workspace."""
        case = _make_case(
            truth=GroundTruth(introducing_commit="intro999"),
        )
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        ws_path = tmp_path / "workspace"

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent",
            context_level="diff+repo",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.setup_workspace",
                return_value=ws_path,
            ) as mock_setup,
            patch(
                "bugeval.agent_runner.run_anthropic_api",
                return_value=fake_result,
            ) as mock_api,
        ):
            process_case(
                case,
                "agent",
                "diff+repo",
                repo_dir,
                run_dir,
                300,
            )

        # setup_workspace should have been called
        mock_setup.assert_called_once()
        # run_anthropic_api should receive the workspace path
        call_args = mock_api.call_args
        assert call_args[0][2] == ws_path  # 3rd positional arg is repo_dir

    def test_agent_diff_only_creates_minimal_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify diff-only creates a workspace with diff.patch."""
        case = _make_case(
            truth=GroundTruth(introducing_commit="intro999"),
        )
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent",
            context_level="diff-only",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.setup_workspace",
            ) as mock_setup,
            patch(
                "bugeval.agent_runner.run_anthropic_api",
                return_value=fake_result,
            ),
        ):
            process_case(
                case,
                "agent",
                "diff-only",
                repo_dir,
                run_dir,
                300,
            )

        # diff-only skips setup_workspace (no repo clone needed)
        mock_setup.assert_not_called()
        # But a minimal workspace dir should have been created
        ws_dir = run_dir / "workspaces"
        assert ws_dir.exists()


class TestEvaluateModelOverride:
    def test_model_passed_to_agent_api(self, tmp_path: Path) -> None:
        """Verify process_case passes model to run_anthropic_api."""
        case = _make_case(
            truth=GroundTruth(introducing_commit="intro999"),
        )
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent",
            context_level="diff-only",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.run_anthropic_api",
                return_value=fake_result,
            ) as mock_api,
        ):
            process_case(
                case,
                "agent",
                "diff-only",
                repo_dir,
                run_dir,
                300,
                model="claude-opus-4-6",
            )

        call_kwargs = mock_api.call_args
        assert call_kwargs.kwargs.get("model") == "claude-opus-4-6"

    def test_model_passed_to_agent_cli(self, tmp_path: Path) -> None:
        """Verify process_case passes model to run_agent_cli."""
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-cli-claude",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.run_agent_cli",
                return_value=fake_result,
            ) as mock_cli,
        ):
            process_case(
                case,
                "agent-cli-claude",
                "diff-only",
                repo_dir,
                run_dir,
                300,
                model="claude-opus-4-6",
            )

        call_kwargs = mock_cli.call_args
        assert call_kwargs.kwargs.get("model") == "claude-opus-4-6"

    def test_model_passed_to_agent_sdk(self, tmp_path: Path) -> None:
        """Verify process_case passes model to run_agent_sdk."""
        case = _make_case()
        run_dir = tmp_path / "run"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        fake_result = ToolResult(
            case_id="leo-001",
            tool="agent-sdk",
        )

        with (
            patch("bugeval.evaluate.get_diff_for_case", return_value="diff"),
            patch(
                "bugeval.agent_runner.run_agent_sdk",
                return_value=fake_result,
            ) as mock_sdk,
        ):
            process_case(
                case,
                "agent-sdk",
                "diff-only",
                repo_dir,
                run_dir,
                300,
                model="claude-opus-4-6",
            )

        call_kwargs = mock_sdk.call_args
        assert call_kwargs.kwargs.get("model") == "claude-opus-4-6"


class TestPerToolClone:
    def test_creates_clone_for_pr_tool(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "leo"
        repo_dir.mkdir()

        with patch("subprocess.run") as mock_run:
            result = ensure_per_tool_clone("copilot", repo_dir)

        assert result == tmp_path / "leo-copilot"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "git",
            "clone",
            "--local",
            str(repo_dir),
            str(tmp_path / "leo-copilot"),
        ]

    def test_returns_existing_clone(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "leo"
        repo_dir.mkdir()
        clone_dir = tmp_path / "leo-greptile"
        clone_dir.mkdir()
        (clone_dir / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            result = ensure_per_tool_clone("greptile", repo_dir)

        assert result == clone_dir
        # Should call fetch, checkout -f, clean -fd, git status
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["git", "fetch", "origin"] in cmds

    def test_existing_clone_resets_dirty_state(
        self,
        tmp_path: Path,
    ) -> None:
        repo_dir = tmp_path / "leo"
        repo_dir.mkdir()
        clone_dir = tmp_path / "leo-copilot"
        clone_dir.mkdir()
        (clone_dir / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            result = ensure_per_tool_clone("copilot", repo_dir)

        assert result == clone_dir
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["git", "checkout", "-f", "HEAD"] in cmds
        assert ["git", "clean", "-fd"] in cmds

    def test_existing_clone_validates_health(
        self,
        tmp_path: Path,
    ) -> None:
        """Clone without .git dir raises RuntimeError."""
        repo_dir = tmp_path / "leo"
        repo_dir.mkdir()
        clone_dir = tmp_path / "leo-copilot"
        clone_dir.mkdir()
        # No .git dir — clone is broken

        import pytest

        with pytest.raises(RuntimeError, match="not a valid git clone"):
            ensure_per_tool_clone("copilot", repo_dir)

    def test_incomplete_clone_cleaned_up(
        self,
        tmp_path: Path,
    ) -> None:
        """If clone fails midway, incomplete directory is deleted."""
        import subprocess as sp

        repo_dir = tmp_path / "leo"
        repo_dir.mkdir()
        clone_dir = tmp_path / "leo-copilot"

        def fake_run(cmd: list[str], **kwargs: object) -> None:
            # Simulate clone creating the dir then failing
            clone_dir.mkdir(exist_ok=True)
            raise sp.CalledProcessError(1, cmd)

        with (
            patch("subprocess.run", side_effect=fake_run),
            __import__("pytest").raises(sp.CalledProcessError),
        ):
            ensure_per_tool_clone("copilot", repo_dir)

        assert not clone_dir.exists(), "incomplete clone dir should be deleted"

    def test_skips_non_pr_tools(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "leo"
        repo_dir.mkdir()

        result = ensure_per_tool_clone("agent", repo_dir)
        assert result == repo_dir

    def test_skips_agent_sdk(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "leo"
        repo_dir.mkdir()

        result = ensure_per_tool_clone("agent-sdk", repo_dir)
        assert result == repo_dir


class TestToolTimeouts:
    def test_load_tool_timeouts_from_config(self, tmp_path: Path) -> None:
        """Verify per-tool timeouts are loaded from config YAML."""
        config = {
            "tools": {
                "greptile": {"timeout_seconds": 120},
                "agent": {"timeout_seconds": 900},
                "copilot": {"display_name": "Copilot"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        with open(cfg_file, "w") as f:
            yaml.safe_dump(config, f)

        timeouts = load_tool_timeouts(cfg_file)
        assert timeouts == {"greptile": 120, "agent": 900}

    def test_load_tool_timeouts_missing_file(self, tmp_path: Path) -> None:
        """Missing config returns empty dict."""
        timeouts = load_tool_timeouts(tmp_path / "nonexistent.yaml")
        assert timeouts == {}

    def test_resolve_timeout_uses_tool_specific(self) -> None:
        """Tool-specific timeout overrides CLI default."""
        assert resolve_timeout("greptile", 300, {"greptile": 120}) == 120

    def test_resolve_timeout_falls_back_to_cli(self) -> None:
        """Unknown tool falls back to CLI default."""
        assert resolve_timeout("unknown", 300, {"greptile": 120}) == 300

    def test_evaluate_tool_uses_config_timeout(
        self,
        tmp_path: Path,
    ) -> None:
        """evaluate_tool resolves per-tool timeout from config."""
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        run_dir = tmp_path / "run"

        case = _make_case()
        with open(cases_dir / "leo-001.yaml", "w") as f:
            yaml.safe_dump(case.model_dump(mode="json"), f)

        tool_timeouts = {"agent": 900}

        with (
            patch(
                "bugeval.evaluate.load_tool_timeouts",
                return_value=tool_timeouts,
            ),
            patch("bugeval.evaluate.process_case") as mock_proc,
        ):
            evaluate_tool(
                "agent",
                cases_dir,
                run_dir,
                "diff-only",
                Path("."),
                1,
                300,  # CLI default
                False,
            )

        # process_case should have been called with 900, not 300
        assert mock_proc.call_count == 1
        call_args = mock_proc.call_args
        # timeout is the 6th positional arg (index 5)
        assert call_args[0][5] == 900


class TestV3ToolConfig:
    def test_v3_in_sdk_tools(self) -> None:
        from bugeval.evaluate import _SDK_TOOLS

        assert "agent-sdk-v3" in _SDK_TOOLS
