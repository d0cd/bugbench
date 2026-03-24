"""Tests for agent_runner module."""

from __future__ import annotations

import asyncio
import json as json_mod
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bugeval.agent_runner import (
    _execute_tool,
    _run_single_pass_sdk,
    _save_transcript,
    _scrub_fix_references,
    annotate_diff,
    build_system_prompt,
    build_user_prompt,
    materialize_workspace,
    parse_agent_findings,
    run_agent_cli,
    run_agent_sdk,
    run_agent_sdk_2pass,
    run_anthropic_api,
    run_google_api,
    run_openai_api,
    sanitize_diff,
    setup_workspace,
)
from bugeval.models import CaseKind, TestCase


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


class TestBuildSystemPrompt:
    def test_diff_only_mentions_workspace_files(self) -> None:
        prompt = build_system_prompt("diff-only")
        assert "diff.patch" in prompt
        assert ".pr/description.md" in prompt
        assert "JSON" in prompt
        # diff-only should NOT mention repo tools
        assert "full repository" not in prompt

    def test_diff_repo_mentions_methodology(self) -> None:
        prompt = build_system_prompt("diff+repo")
        assert "full repository" in prompt
        assert "callers" in prompt.lower()
        assert "diff.patch" in prompt

    def test_diff_repo_domain_has_zk_context(self) -> None:
        prompt = build_system_prompt("diff+repo+domain")
        assert "zero-knowledge" in prompt.lower()
        assert ".pr/domain.md" in prompt
        assert "full repository" in prompt

    def test_docker_v3_has_cargo_check(self) -> None:
        prompt = build_system_prompt("diff+repo", bash_enabled=True)
        assert "cargo check" in prompt
        assert "cargo clippy" in prompt
        assert "rg " in prompt  # ripgrep
        assert "Bash" in prompt

    def test_docker_v3_has_workflow(self) -> None:
        prompt = build_system_prompt("diff+repo", bash_enabled=True)
        assert "Read the diff" in prompt
        assert "Check callers" in prompt
        assert "Report findings" in prompt

    def test_docker_v3_has_tool_guidance(self) -> None:
        prompt = build_system_prompt("diff+repo", bash_enabled=True)
        assert "rg" in prompt.lower() or "bash" in prompt.lower()

    def test_docker_v3_has_anti_cheat(self) -> None:
        prompt = build_system_prompt("diff+repo", bash_enabled=True)
        assert "Do NOT search for" in prompt
        assert "github.com" in prompt

    def test_docker_v3_has_output_format(self) -> None:
        prompt = build_system_prompt("diff+repo", bash_enabled=True)
        assert '"file"' in prompt
        assert '"line"' in prompt
        assert "JSON" in prompt

    def test_docker_false_uses_standard_prompt(self) -> None:
        docker_prompt = build_system_prompt("diff+repo", bash_enabled=True)
        standard_prompt = build_system_prompt("diff+repo", bash_enabled=False)
        assert "cargo check" in docker_prompt
        assert "cargo check" not in standard_prompt

    def test_docker_v3_mentions_already_merged(self) -> None:
        prompt = build_system_prompt("diff+repo", bash_enabled=True)
        assert "already been merged" in prompt.lower() or "already applied" in prompt.lower()


class TestBuildUserPrompt:
    def test_workspace_references(self) -> None:
        case = _make_case()
        prompt = build_user_prompt(case, SAMPLE_DIFF, "diff-only")
        assert "diff.patch" in prompt
        assert ".pr/description.md" in prompt
        # No inline diff by default
        assert "```diff" not in prompt

    def test_inline_diff_when_requested(self) -> None:
        case = _make_case()
        prompt = build_user_prompt(
            case,
            SAMPLE_DIFF,
            "diff-only",
            inline_diff=True,
        )
        assert "```diff" in prompt
        assert "foo.rs" in prompt

    def test_repo_context_mentions_tools(self) -> None:
        case = _make_case()
        prompt = build_user_prompt(case, SAMPLE_DIFF, "diff+repo")
        assert "repository tools" in prompt.lower()

    def test_domain_context_mentions_domain_md(self) -> None:
        case = _make_case()
        prompt = build_user_prompt(case, SAMPLE_DIFF, "diff+repo+domain")
        assert ".pr/domain.md" in prompt


class TestSanitizeDiff:
    def test_strips_index_lines(self) -> None:
        diff = (
            "diff --git a/f.rs b/f.rs\n"
            "index abc1234..def5678 100644\n"
            "--- a/f.rs\n"
            "+++ b/f.rs\n"
            "@@ -1,3 +1,3 @@\n"
            "-old\n"
            "+new\n"
        )
        result = sanitize_diff(diff)
        assert "index " not in result
        assert "--- a/f.rs" in result
        assert "+new" in result

    def test_strips_author_date(self) -> None:
        diff = "Author: alice\nDate: 2024-01-01\n--- a/f.rs\n+++ b/f.rs\n"
        result = sanitize_diff(diff)
        assert "Author:" not in result
        assert "Date:" not in result
        assert "--- a/f.rs" in result

    def test_strips_from_header(self) -> None:
        diff = "From: alice@example.com\n--- a/f.rs\n+++ b/f.rs\n"
        result = sanitize_diff(diff)
        assert "From:" not in result
        assert "--- a/f.rs" in result

    def test_strips_from_sha_line(self) -> None:
        diff = "From abc1234def5678901234567890abcdef12345678 Mon Sep 17\n--- a/f.rs\n+++ b/f.rs\n"
        result = sanitize_diff(diff)
        assert "From abc1234" not in result
        assert "--- a/f.rs" in result


class TestParseAgentFindings:
    def test_json_array(self) -> None:
        response = '[{"file":"f.rs","line":10,"description":"bug here"}]'
        comments = parse_agent_findings(response)
        assert len(comments) == 1
        assert comments[0].file == "f.rs"
        assert comments[0].line == 10
        assert comments[0].body == "bug here"

    def test_json_with_surrounding_text(self) -> None:
        response = (
            'Here are my findings:\n[{"file":"a.rs","line":5,"description":"issue"}]\nThat is all.'
        )
        comments = parse_agent_findings(response)
        assert len(comments) == 1
        assert comments[0].file == "a.rs"

    def test_malformed_returns_empty(self) -> None:
        assert parse_agent_findings("no json here") == []
        assert parse_agent_findings("{not an array}") == []
        assert parse_agent_findings("") == []

    def test_empty_array(self) -> None:
        assert parse_agent_findings("[]") == []

    def test_with_suggested_fix(self) -> None:
        response = '[{"file":"x.rs","line":1,"description":"d","suggested_fix":"f"}]'
        comments = parse_agent_findings(response)
        assert comments[0].suggested_fix == "f"


class TestRunAgentApiDiffOnly:
    def test_mocked_anthropic_returns_result(self) -> None:
        case = _make_case()

        # Build mock response
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = '[{"file":"f.rs","line":1,"description":"bug"}]'

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [text_block]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("bugeval._anthropic_runner.anthropic.Anthropic", return_value=mock_client):
            result = run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        assert result.case_id == "leo-001"
        assert result.tool == "agent"
        assert result.context_level == "diff-only"
        assert len(result.comments) == 1
        assert result.comments[0].file == "f.rs"
        assert result.error == ""
        assert result.cost_usd > 0

    def test_api_error_captured(self) -> None:
        case = _make_case()

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch("bugeval._anthropic_runner.anthropic.Anthropic", return_value=mock_client):
            result = run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        assert result.case_id == "leo-001"
        assert "API down" in result.error


class TestScrubFixReferences:
    def test_removes_fix_lines(self) -> None:
        body = "Add parser\nThis fixes the crash\nGood change"
        result = _scrub_fix_references(body)
        assert "fixes" not in result.lower()
        assert "Add parser" in result
        assert "Good change" in result

    def test_removes_issue_references(self) -> None:
        body = "Implement feature\nCloses #123\nDetails here"
        result = _scrub_fix_references(body)
        assert "#123" not in result
        assert "Implement feature" in result

    def test_removes_bug_lines(self) -> None:
        body = "Update parser\nThis is a bug fix\nEnd"
        result = _scrub_fix_references(body)
        assert "bug" not in result.lower()

    def test_preserves_clean_body(self) -> None:
        body = "Add new feature\nImprove performance"
        result = _scrub_fix_references(body)
        assert "Add new feature" in result
        assert "Improve performance" in result


class TestMaterializeWorkspaceScrubsTitle:
    def test_title_with_fix_keyword_scrubbed(self, tmp_path: Path) -> None:
        case = _make_case(
            introducing_pr_title="Fix crash in parser",
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_workspace(case, "diff", ws, "diff+repo")
        desc = (ws / ".pr" / "description.md").read_text()
        assert "Fix crash" not in desc

    def test_title_without_fix_keyword_preserved(self, tmp_path: Path) -> None:
        case = _make_case(
            introducing_pr_title="Add new parser",
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_workspace(case, "diff", ws, "diff+repo")
        desc = (ws / ".pr" / "description.md").read_text()
        assert "Add new parser" in desc


class TestMaterializeWorkspaceScrubsCommitMessages:
    def test_commit_messages_with_fix_keyword_scrubbed(
        self,
        tmp_path: Path,
    ) -> None:
        case = _make_case(
            introducing_pr_commit_messages=[
                "fix: handle edge case",
                "feat: add parser",
            ],
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_workspace(case, "diff", ws, "diff+repo")
        commits = (ws / ".pr" / "commits.txt").read_text()
        assert "fix: handle" not in commits.lower()
        assert "feat: add parser" in commits

    def test_all_messages_scrubbed_uses_placeholder(
        self,
        tmp_path: Path,
    ) -> None:
        case = _make_case(
            introducing_pr_commit_messages=["fix: patch bug #42"],
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_workspace(case, "diff", ws, "diff+repo")
        commits = (ws / ".pr" / "commits.txt").read_text()
        assert commits == "(no commits)"


class TestMaterializeWorkspaceAntiContamination:
    def test_scrubs_fix_references_from_body(self, tmp_path: Path) -> None:
        case = _make_case(
            introducing_pr_body="Add parser\nThis fixes the crash\nGood code",
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_workspace(case, "diff", ws, "diff+repo")
        desc = (ws / ".pr" / "description.md").read_text()
        assert "fixes" not in desc.lower()
        assert "Add parser" in desc

    def test_body_entirely_fix_references_omitted(
        self,
        tmp_path: Path,
    ) -> None:
        case = _make_case(
            introducing_pr_title="",
            introducing_pr_body="Fixes #42",
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_workspace(case, "diff", ws, "diff+repo")
        desc = (ws / ".pr" / "description.md").read_text()
        assert desc == "(no description)"


class TestMaterializeWorkspace:
    def test_creates_all_files(self, tmp_path: Path) -> None:
        case = _make_case()
        ws = tmp_path / "ws"
        ws.mkdir()
        result = materialize_workspace(case, "the diff", ws, "diff+repo")
        assert result == ws
        assert (ws / "diff.patch").read_text() == "the diff"
        assert (ws / ".pr" / "description.md").exists()
        assert (ws / ".pr" / "commits.txt").exists()
        # No domain.md for diff+repo
        assert not (ws / ".pr" / "domain.md").exists()

    def test_diff_only_creates_temp_dir(self, tmp_path: Path) -> None:
        case = _make_case()
        ws = tmp_path / "ws"
        ws.mkdir()
        result = materialize_workspace(case, "diff", ws, "diff-only")
        # Returns a NEW temp dir, not the original ws
        assert result != ws
        assert (result / "diff.patch").read_text() == "diff"
        assert (result / ".pr" / "description.md").exists()

    def test_domain_context_creates_domain_md(self, tmp_path: Path) -> None:
        case = _make_case()
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_workspace(case, "diff", ws, "diff+repo+domain")
        domain = (ws / ".pr" / "domain.md").read_text()
        assert "zero-knowledge" in domain.lower()

    def test_diff_repo_no_domain_md(self, tmp_path: Path) -> None:
        case = _make_case()
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_workspace(case, "diff", ws, "diff+repo")
        assert not (ws / ".pr" / "domain.md").exists()


class TestSetupWorkspace:
    def test_diff_only_returns_none(self) -> None:
        case = _make_case()
        result = setup_workspace(case, Path("/repo"), "diff-only", Path("/work"))
        assert result is None

    def test_passes_local_path_to_clone(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        case = _make_case()
        local_repo = tmp_path / "repo"
        local_repo.mkdir()
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        calls: list[str] = []

        def mock_clone(source: str, dest: Path, sha: str, **kw: object) -> Path:
            calls.append(source)
            dest.mkdir(parents=True, exist_ok=True)
            return dest

        monkeypatch.setattr("bugeval.git_utils.clone_at_sha", mock_clone)
        result = setup_workspace(case, local_repo, "diff+repo", work_dir)
        assert result == work_dir / case.id
        # Should receive local path, not a URL
        assert calls[0] == str(local_repo)

    def test_passes_url_string_to_clone(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        case = _make_case()
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        calls: list[str] = []

        def mock_clone(source: str, dest: Path, sha: str, **kw: object) -> Path:
            calls.append(source)
            dest.mkdir(parents=True, exist_ok=True)
            return dest

        monkeypatch.setattr("bugeval.git_utils.clone_at_sha", mock_clone)
        url = "https://github.com/ProvableHQ/leo.git"
        setup_workspace(case, url, "diff+repo", work_dir)
        assert calls[0] == url

    def test_workspace_per_case_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        def mock_clone(source: str, dest: Path, sha: str, **kw: object) -> Path:
            dest.mkdir(parents=True, exist_ok=True)
            return dest

        monkeypatch.setattr("bugeval.git_utils.clone_at_sha", mock_clone)

        case1 = _make_case(id="leo-001")
        case2 = _make_case(id="leo-002")
        r1 = setup_workspace(case1, Path("/repo"), "diff+repo", work_dir)
        r2 = setup_workspace(case2, Path("/repo"), "diff+repo", work_dir)
        assert r1 != r2
        assert r1.name == "leo-001"
        assert r2.name == "leo-002"


class TestExecuteToolReadFile:
    def test_read_file_success(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "hello.txt").write_text("hello world")
        result = _execute_tool("read_file", {"path": "hello.txt"}, repo)
        assert result == "hello world"

    def test_read_file_path_traversal(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        # Create a file outside repo
        (tmp_path / "secret.txt").write_text("secret")
        result = _execute_tool("read_file", {"path": "../../etc/passwd"}, repo)
        assert "path outside workspace" in result.lower()

    def test_read_file_path_traversal_prefix_trick(self, tmp_path: Path) -> None:
        # This is the specific case the old string check missed:
        # /tmp/repo-evil starts with /tmp/repo
        repo = tmp_path / "repo"
        repo.mkdir()
        evil = tmp_path / "repo-evil"
        evil.mkdir()
        (evil / "data.txt").write_text("evil")
        # ../repo-evil/data.txt resolves outside repo
        result = _execute_tool("read_file", {"path": "../repo-evil/data.txt"}, repo)
        assert "path outside workspace" in result.lower()

    def test_read_file_not_found(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        result = _execute_tool("read_file", {"path": "nope.txt"}, repo)
        assert "file not found" in result.lower()

    def test_read_file_git_dir_blocked(self, tmp_path: Path) -> None:
        """Agents must not read .git internals (prevents history-based cheating)."""
        repo = tmp_path / "repo"
        git_dir = repo / ".git" / "logs"
        git_dir.mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main")
        result = _execute_tool("read_file", {"path": ".git/logs/HEAD"}, repo)
        assert "version control" in result.lower()

    def test_list_directory_git_dir_blocked(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / ".git" / "refs").mkdir(parents=True)
        result = _execute_tool("list_directory", {"path": ".git/refs"}, repo)
        assert "version control" in result.lower()

    def test_search_text_git_dir_blocked(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        result = _execute_tool("search_text", {"pattern": "HEAD", "path": ".git"}, repo)
        assert "version control" in result.lower()


class TestExecuteToolListDirectory:
    def test_list_directory_success(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.txt").write_text("a")
        (repo / "b.txt").write_text("b")
        result = _execute_tool("list_directory", {"path": "."}, repo)
        assert "a.txt" in result
        assert "b.txt" in result

    def test_list_directory_path_traversal(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        result = _execute_tool("list_directory", {"path": ".."}, repo)
        assert "path outside workspace" in result.lower()


class TestExecuteToolSearchText:
    def test_search_text_success(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="foo.rs:10:match here\n", returncode=0)
            result = _execute_tool("search_text", {"pattern": "match", "path": "."}, repo)
        assert "match here" in result

    def test_search_text_path_traversal(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        result = _execute_tool("search_text", {"pattern": "x", "path": "../.."}, repo)
        assert "path outside workspace" in result.lower()


class TestExecuteToolUnknown:
    def test_unknown_tool(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        result = _execute_tool("delete_everything", {}, repo)
        assert "unknown tool" in result.lower()


class TestRunAgentApiMultiTurn:
    def test_tool_use_then_text(self) -> None:
        case = _make_case()

        # First response: tool_use
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "read_file"
        tool_block.input = {"path": "foo.rs"}
        tool_block.id = "tool_1"

        resp1 = MagicMock()
        resp1.stop_reason = "tool_use"
        resp1.content = [tool_block]
        resp1.usage = MagicMock(input_tokens=50, output_tokens=20)

        # Second response: final text
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = '[{"file":"foo.rs","line":1,"description":"bug"}]'

        resp2 = MagicMock()
        resp2.stop_reason = "end_turn"
        resp2.content = [text_block]
        resp2.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [resp1, resp2]

        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            result = run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff+repo",
                max_turns=5,
                timeout=300,
            )

        assert len(result.comments) == 1
        assert result.comments[0].file == "foo.rs"
        assert result.error == ""
        # Should have been called twice
        assert mock_client.messages.create.call_count == 2

    def test_cost_ceiling_breached(self) -> None:
        case = _make_case()

        # Response with enormous usage to blow past ceiling
        text_block = MagicMock()
        text_block.type = "tool_use"
        text_block.name = "read_file"
        text_block.input = {"path": "x"}
        text_block.id = "t1"

        resp = MagicMock()
        resp.stop_reason = "tool_use"
        resp.content = [text_block]
        # $3/MTok * 1M = $3, which exceeds $2 ceiling
        resp.usage = MagicMock(input_tokens=1_000_000, output_tokens=0)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = resp

        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            result = run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff+repo",
                max_turns=10,
                timeout=300,
            )

        assert "cost ceiling" in result.error.lower()


class TestRunAgentApiTimeout:
    def test_timeout_exceeded(self) -> None:
        case = _make_case()

        mock_client = MagicMock()
        # Make monotonic return increasing values
        call_count = 0

        def fake_monotonic() -> float:
            nonlocal call_count
            call_count += 1
            # First call (start): 0, second call (check): 400
            return 0.0 if call_count <= 1 else 400.0

        with (
            patch(
                "bugeval._anthropic_runner.anthropic.Anthropic",
                return_value=mock_client,
            ),
            patch("bugeval._anthropic_runner.time.monotonic", side_effect=fake_monotonic),
        ):
            result = run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        assert "timeout" in result.error.lower()
        mock_client.messages.create.assert_not_called()


class TestRunAgentApiTranscript:
    def test_transcript_saved(self, tmp_path: Path) -> None:
        case = _make_case()

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "[]"

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [text_block]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        transcript_dir = tmp_path / "transcripts"
        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            result = run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
                transcript_dir=transcript_dir,
            )

        assert result.transcript_path != ""
        assert Path(result.transcript_path).exists()
        import json

        data = json.loads(Path(result.transcript_path).read_text())
        assert isinstance(data, list)
        assert data[0]["role"] == "user"


class TestCliRunnerUsesStdin:
    @patch("bugeval._cli_runners.subprocess.run")
    def test_prompt_piped_via_stdin(self, mock_run: MagicMock) -> None:
        """Verify the CLI runner passes prompt via stdin, not as an argument."""
        import subprocess as sp

        output = {
            "result": '[{"file":"f.rs","line":1,"description":"bug"}]',
            "cost": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_run.return_value = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json_mod.dumps(output),
            stderr="",
        )
        case = _make_case()
        result = run_agent_cli(
            case,
            SAMPLE_DIFF,
            None,
            "diff-only",
            cli_tool="claude",
            timeout=60,
        )
        assert result.error == ""
        assert len(result.comments) == 1
        # Check that subprocess.run was called with input= (stdin)
        call_kwargs = mock_run.call_args
        cmd_list = call_kwargs[0][0]
        assert cmd_list[0] == "claude"
        assert "-p" in cmd_list
        # Prompt should be piped via input keyword arg
        assert call_kwargs.kwargs.get("input") is not None


class TestRunAgentApiWithThinking:
    def test_run_anthropic_api_with_thinking(self, tmp_path: Path) -> None:
        """Thinking block appears in transcript but not in findings."""
        case = _make_case()

        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        thinking_block.thinking = "Let me analyze this diff carefully..."

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = '[{"file":"f.rs","line":1,"description":"bug"}]'

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [thinking_block, text_block]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=200)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        transcript_dir = tmp_path / "transcripts"
        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            result = run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
                transcript_dir=transcript_dir,
                thinking_budget=8000,
            )

        assert result.error == ""
        assert len(result.comments) == 1
        assert result.comments[0].file == "f.rs"
        # Verify thinking is in transcript
        import json

        data = json.loads(Path(result.transcript_path).read_text())
        # The assistant message has the response content
        assistant_msgs = [m for m in data if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        content = assistant_msgs[0]["content"]
        thinking_items = [c for c in content if isinstance(c, dict) and c.get("type") == "thinking"]
        assert len(thinking_items) == 1
        assert thinking_items[0]["thinking"] == "Let me analyze this diff carefully..."

    def test_thinking_budget_in_kwargs(self) -> None:
        """Verify thinking config is passed to the API when budget > 0."""
        case = _make_case()

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "[]"

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [text_block]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
                thinking_budget=8000,
            )

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs.get("thinking") == {
            "type": "enabled",
            "budget_tokens": 8000,
        }
        # max_tokens must be > budget_tokens
        assert call_kwargs.kwargs["max_tokens"] >= 8000 + 4096

    def test_thinking_not_enabled_when_zero(self) -> None:
        """Verify thinking config is NOT passed when budget is 0."""
        case = _make_case()

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "[]"

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [text_block]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
                thinking_budget=0,
            )

        call_kwargs = mock_client.messages.create.call_args
        assert "thinking" not in call_kwargs.kwargs

    def test_thinking_not_in_findings(self) -> None:
        """Thinking text should not be parsed as findings."""
        case = _make_case()

        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        thinking_block.thinking = '[{"file":"fake.rs","line":99,"description":"from thinking"}]'

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "[]"

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [thinking_block, text_block]
        mock_response.usage = MagicMock(input_tokens=50, output_tokens=100)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            result = run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
                thinking_budget=8000,
            )

        # Thinking block contains JSON that looks like findings,
        # but it should NOT be parsed — only text blocks are parsed
        assert len(result.comments) == 0


class TestSaveTranscriptThinkingBlocks:
    def test_thinking_blocks_serialized(self, tmp_path: Path) -> None:
        """Verify thinking blocks are serialized correctly in transcripts."""
        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        thinking_block.thinking = "Deep analysis of the code..."

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Here are my findings."

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "read_file"
        tool_block.input = {"path": "foo.rs"}
        tool_block.id = "tool_123"

        messages: list[dict[str, object]] = [
            {"role": "user", "content": "Review this diff"},
            {
                "role": "assistant",
                "content": [thinking_block, text_block, tool_block],
            },
        ]

        import json

        path = _save_transcript(
            messages,
            tmp_path,
            "test-001",  # type: ignore[arg-type]
        )
        data = json.loads(Path(path).read_text())

        assert data[0]["role"] == "user"
        assert data[0]["content"] == "Review this diff"

        assistant_content = data[1]["content"]
        assert len(assistant_content) == 3

        assert assistant_content[0] == {
            "type": "thinking",
            "thinking": "Deep analysis of the code...",
        }
        assert assistant_content[1] == {
            "type": "text",
            "text": "Here are my findings.",
        }
        assert assistant_content[2] == {
            "type": "tool_use",
            "name": "read_file",
            "input": {"path": "foo.rs"},
            "id": "tool_123",
        }


# ---------------------------------------------------------------------------
# Agent SDK tests
# ---------------------------------------------------------------------------


def _make_sdk_mocks() -> tuple[types.ModuleType, types.ModuleType]:
    """Create fake claude_agent_sdk + claude_agent_sdk.types modules."""
    mod = types.ModuleType("claude_agent_sdk")
    types_mod = types.ModuleType("claude_agent_sdk.types")

    class ClaudeAgentOptions:
        def __init__(self, **kwargs: object) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class AssistantMessage:
        def __init__(self, content: list[object]) -> None:
            self.content = content

    class ResultMessage:
        def __init__(
            self,
            total_cost_usd: float = 0.0,
            session_id: str = "",
            result: str = "",
        ) -> None:
            self.total_cost_usd = total_cost_usd
            self.session_id = session_id
            self.result = result

    class CLINotFoundError(Exception):
        pass

    class CLIConnectionError(Exception):
        pass

    class TextBlock:
        def __init__(self, text: str = "") -> None:
            self.text = text

    class ThinkingBlock:
        def __init__(self, thinking: str = "", signature: str = "") -> None:
            self.thinking = thinking
            self.signature = signature

    class ToolUseBlock:
        def __init__(
            self,
            id: str = "",
            name: str = "",
            input: dict[str, object] | None = None,
        ) -> None:
            self.id = id
            self.name = name
            self.input = input or {}

    class ClaudeSDKClient:
        """Fake ClaudeSDKClient that delegates to mod.query for testing."""

        def __init__(self, options: object = None) -> None:
            self._options = options
            self._query_fn = mod.query  # type: ignore[attr-defined]
            self._pending_prompt: str = ""

        async def __aenter__(self) -> ClaudeSDKClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def query(self, prompt: str) -> None:
            self._pending_prompt = prompt

        async def receive_response(self):  # type: ignore[no-untyped-def]
            if self._query_fn is not None:
                async for msg in self._query_fn(prompt=self._pending_prompt):
                    yield msg

    mod.ClaudeAgentOptions = ClaudeAgentOptions  # type: ignore[attr-defined]
    mod.AssistantMessage = AssistantMessage  # type: ignore[attr-defined]
    mod.ResultMessage = ResultMessage  # type: ignore[attr-defined]
    mod.CLINotFoundError = CLINotFoundError  # type: ignore[attr-defined]
    mod.CLIConnectionError = CLIConnectionError  # type: ignore[attr-defined]
    mod.ClaudeSDKClient = ClaudeSDKClient  # type: ignore[attr-defined]
    mod.query = None  # type: ignore[attr-defined]

    types_mod.TextBlock = TextBlock  # type: ignore[attr-defined]
    types_mod.ThinkingBlock = ThinkingBlock  # type: ignore[attr-defined]
    types_mod.ToolUseBlock = ToolUseBlock  # type: ignore[attr-defined]

    return mod, types_mod


def _sdk_text_block(text: str) -> object:
    """Create a TextBlock-like object for SDK tests."""
    _, types_mod = _make_sdk_mocks()
    return types_mod.TextBlock(text=text)  # type: ignore[attr-defined]


class TestRunAgentSdkSuccess:
    def test_mocked_sdk_returns_result(self, tmp_path: Path) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        AssistantMessage = sdk_mod.AssistantMessage  # type: ignore[attr-defined]
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        findings_json = '[{"file":"f.rs","line":1,"description":"bug"}]'
        text_block = _sdk_text_block(findings_json)

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield AssistantMessage(content=[text_block])
            yield ResultMessage(
                total_cost_usd=0.05,
                session_id="sess-123",
                result=findings_json,
            )

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            result = run_agent_sdk(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                timeout=300,
                transcript_dir=tmp_path / "transcripts",
            )

        assert result.case_id == "leo-001"
        assert result.tool == "agent-sdk"
        assert result.context_level == "diff-only"
        assert len(result.comments) == 1
        assert result.comments[0].file == "f.rs"
        assert result.error == ""
        assert result.cost_usd == 0.05
        assert result.time_seconds >= 0


class TestRunAgentSdkImportError:
    def test_import_error_returns_error_result(self) -> None:
        with patch.dict(sys.modules, {"claude_agent_sdk": None}):
            case = _make_case()
            result = run_agent_sdk(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                timeout=60,
            )

        assert result.tool == "agent-sdk"
        assert "claude-agent-sdk not installed" in result.error


class TestRunAgentSdkTimeout:
    def test_timeout_returns_error(self) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        AssistantMessage = sdk_mod.AssistantMessage  # type: ignore[attr-defined]

        async def slow_query(**kwargs: object):  # type: ignore[no-untyped-def]
            # Yield enough messages that the timeout check triggers
            for _ in range(20):
                yield AssistantMessage(content=[_sdk_text_block("partial")])

        sdk_mod.query = slow_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            # Use timeout=0 so the first check (monotonic - start > 0) triggers
            result = run_agent_sdk(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                timeout=0,
            )

        assert "timeout" in result.error.lower()
        assert result.tool == "agent-sdk"


class TestRunAgentSdkTranscriptSaved:
    def test_transcript_file_created(self, tmp_path: Path) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        AssistantMessage = sdk_mod.AssistantMessage  # type: ignore[attr-defined]
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield AssistantMessage(content=[_sdk_text_block("[]")])
            yield ResultMessage(total_cost_usd=0.01, session_id="sess-t")

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        transcript_dir = tmp_path / "transcripts"
        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            result = run_agent_sdk(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                timeout=300,
                transcript_dir=transcript_dir,
            )

        assert result.transcript_path != ""
        t_path = Path(result.transcript_path)
        assert t_path.exists()
        data = json_mod.loads(t_path.read_text())
        assert data["session_id"] == "sess-t"
        assert isinstance(data["messages"], list)
        assert data["cost_usd"] == 0.01


class TestRunAgentSdkCostTracking:
    def test_cost_from_result_message(self) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        AssistantMessage = sdk_mod.AssistantMessage  # type: ignore[attr-defined]
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield AssistantMessage(content=[_sdk_text_block("[]")])
            yield ResultMessage(total_cost_usd=1.23, session_id="s1")

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            result = run_agent_sdk(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                timeout=300,
            )

        assert result.cost_usd == 1.23


class TestRunAgentSdkDiffOnlyNoTools:
    def test_allowed_tools_websearch_only_for_diff_only(self) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        AssistantMessage = sdk_mod.AssistantMessage  # type: ignore[attr-defined]
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        captured_options: list[dict[str, object]] = []
        original_init = sdk_mod.ClaudeAgentOptions.__init__  # type: ignore[attr-defined]

        class TrackingOptions(sdk_mod.ClaudeAgentOptions):  # type: ignore[misc]
            def __init__(self, **kwargs: object) -> None:
                original_init(self, **kwargs)
                captured_options.append(kwargs)

        sdk_mod.ClaudeAgentOptions = TrackingOptions  # type: ignore[attr-defined]

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield AssistantMessage(content=[_sdk_text_block("[]")])
            yield ResultMessage(total_cost_usd=0.0)

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            run_agent_sdk(case, SAMPLE_DIFF, None, "diff-only", timeout=300)

        assert len(captured_options) == 1
        assert captured_options[0]["allowed_tools"] == ["Read", "Glob", "Grep", "WebSearch"]


class TestRunAgentSdkContextLevels:
    def test_tools_set_for_diff_repo(self) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        AssistantMessage = sdk_mod.AssistantMessage  # type: ignore[attr-defined]
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        captured_options: list[dict[str, object]] = []
        original_init = sdk_mod.ClaudeAgentOptions.__init__  # type: ignore[attr-defined]

        class TrackingOptions(sdk_mod.ClaudeAgentOptions):  # type: ignore[misc]
            def __init__(self, **kwargs: object) -> None:
                original_init(self, **kwargs)
                captured_options.append(kwargs)

        sdk_mod.ClaudeAgentOptions = TrackingOptions  # type: ignore[attr-defined]

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield AssistantMessage(content=[_sdk_text_block("[]")])
            yield ResultMessage(total_cost_usd=0.0)

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            run_agent_sdk(case, SAMPLE_DIFF, None, "diff+repo", timeout=300)

        assert len(captured_options) == 1
        assert captured_options[0]["allowed_tools"] == ["Read", "Glob", "Grep", "WebSearch"]

    def test_tools_set_for_diff_repo_domain(self) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        AssistantMessage = sdk_mod.AssistantMessage  # type: ignore[attr-defined]
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        captured_options: list[dict[str, object]] = []
        original_init = sdk_mod.ClaudeAgentOptions.__init__  # type: ignore[attr-defined]

        class TrackingOptions(sdk_mod.ClaudeAgentOptions):  # type: ignore[misc]
            def __init__(self, **kwargs: object) -> None:
                original_init(self, **kwargs)
                captured_options.append(kwargs)

        sdk_mod.ClaudeAgentOptions = TrackingOptions  # type: ignore[attr-defined]

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield AssistantMessage(content=[_sdk_text_block("[]")])
            yield ResultMessage(total_cost_usd=0.0)

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            run_agent_sdk(
                case,
                SAMPLE_DIFF,
                None,
                "diff+repo+domain",
                timeout=300,
            )

        assert len(captured_options) == 1
        assert captured_options[0]["allowed_tools"] == ["Read", "Glob", "Grep", "WebSearch"]


# ---------------------------------------------------------------------------
# Model override tests
# ---------------------------------------------------------------------------


class TestRunAgentApiModelOverride:
    def test_model_override_passed_to_api(self) -> None:
        """When model is set, it should override the default MODEL constant."""
        case = _make_case()

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "[]"

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [text_block]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
                model="claude-opus-4-6",
            )

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-opus-4-6"

    def test_empty_model_uses_default(self) -> None:
        """When model is empty, the default MODEL constant is used."""
        case = _make_case()

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "[]"

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [text_block]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch(
            "bugeval._anthropic_runner.anthropic.Anthropic",
            return_value=mock_client,
        ):
            run_anthropic_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
                model="",
            )

        from bugeval.agent_runner import MODEL

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == MODEL


class TestRunAgentSdkModelOverride:
    def test_model_override_passed_to_sdk(self) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        AssistantMessage = sdk_mod.AssistantMessage  # type: ignore[attr-defined]
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        captured_options: list[dict[str, object]] = []
        original_init = sdk_mod.ClaudeAgentOptions.__init__  # type: ignore[attr-defined]

        class TrackingOptions(sdk_mod.ClaudeAgentOptions):  # type: ignore[misc]
            def __init__(self, **kwargs: object) -> None:
                original_init(self, **kwargs)
                captured_options.append(kwargs)

        sdk_mod.ClaudeAgentOptions = TrackingOptions  # type: ignore[attr-defined]

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield AssistantMessage(content=[_sdk_text_block("[]")])
            yield ResultMessage(total_cost_usd=0.0)

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            run_agent_sdk(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                timeout=300,
                model="claude-opus-4-6",
            )

        assert len(captured_options) == 1
        assert captured_options[0]["model"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# Google Gemini API tests
# ---------------------------------------------------------------------------


def _make_google_mocks() -> tuple[
    types.ModuleType,
    types.ModuleType,
    types.ModuleType,
]:
    """Create fake google, google.genai, and google.genai.types modules."""
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")

    class FunctionDeclaration:
        def __init__(
            self,
            name: str = "",
            description: str = "",
            parameters: object = None,
        ) -> None:
            self.name = name
            self.description = description
            self.parameters = parameters

    class GoogleSearch:
        pass

    class Tool:
        def __init__(
            self,
            function_declarations: list[object] | None = None,
            google_search: object | None = None,
        ) -> None:
            self.function_declarations = function_declarations
            self.google_search = google_search

    class Content:
        def __init__(
            self,
            role: str = "",
            parts: list[object] | None = None,
        ) -> None:
            self.role = role
            self.parts = parts or []

    class Part:
        def __init__(self, text: str = "") -> None:
            self.text = text
            self.function_call = None

        @staticmethod
        def from_text(text: str = "") -> Part:
            return Part(text=text)

        @staticmethod
        def from_function_response(
            name: str = "",
            response: object = None,
        ) -> Part:
            p = Part()
            p.text = ""
            return p

    class FunctionCall:
        def __init__(
            self,
            name: str = "",
            args: dict[str, object] | None = None,
        ) -> None:
            self.name = name
            self.args = args or {}

    class GenerateContentConfig:
        def __init__(self, **kwargs: object) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    types_mod.FunctionDeclaration = FunctionDeclaration  # type: ignore[attr-defined]
    types_mod.GoogleSearch = GoogleSearch  # type: ignore[attr-defined]
    types_mod.Tool = Tool  # type: ignore[attr-defined]
    types_mod.Content = Content  # type: ignore[attr-defined]
    types_mod.Part = Part  # type: ignore[attr-defined]
    types_mod.GenerateContentConfig = GenerateContentConfig  # type: ignore[attr-defined]
    types_mod.FunctionCall = FunctionCall  # type: ignore[attr-defined]

    # Wire up the module hierarchy
    google_mod.genai = genai_mod  # type: ignore[attr-defined]
    genai_mod.types = types_mod  # type: ignore[attr-defined]

    return google_mod, genai_mod, types_mod


def _make_google_text_response(
    text: str,
    inp_tokens: int = 100,
    out_tokens: int = 50,
) -> MagicMock:
    """Build a mock Google generate_content response with text only."""
    part = MagicMock()
    part.text = text
    part.function_call = None

    content = MagicMock()
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    usage = MagicMock()
    usage.prompt_token_count = inp_tokens
    usage.candidates_token_count = out_tokens

    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = usage
    return response


def _make_google_tool_response(
    fn_name: str,
    fn_args: dict[str, object],
    inp_tokens: int = 50,
    out_tokens: int = 20,
) -> MagicMock:
    """Build a mock Google response with a function call."""
    fc = MagicMock()
    fc.name = fn_name
    fc.args = fn_args

    part = MagicMock()
    part.text = None
    part.function_call = fc

    content = MagicMock()
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    usage = MagicMock()
    usage.prompt_token_count = inp_tokens
    usage.candidates_token_count = out_tokens

    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = usage
    return response


class TestRunGoogleApiDiffOnly:
    def test_mocked_google_returns_result(self) -> None:
        case = _make_case()
        google_mod, genai_mod, types_mod = _make_google_mocks()

        text_resp = _make_google_text_response('[{"file":"f.rs","line":1,"description":"bug"}]')
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = text_resp

        genai_mod.Client = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "google": google_mod,
                "google.genai": genai_mod,
                "google.genai.types": types_mod,
            },
        ):
            result = run_google_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        assert result.case_id == "leo-001"
        assert result.tool == "agent-gemini"
        assert result.context_level == "diff-only"
        assert len(result.comments) == 1
        assert result.comments[0].file == "f.rs"
        assert result.error == ""
        assert result.cost_usd > 0

    def test_import_error_returns_error_result(self) -> None:
        with patch.dict(
            sys.modules,
            {
                "google": None,
                "google.genai": None,
            },
        ):
            case = _make_case()
            result = run_google_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                timeout=60,
            )

        assert result.tool == "agent-gemini"
        assert "google-genai not installed" in result.error


class TestRunGoogleApiMultiTurn:
    def test_tool_use_then_text(self) -> None:
        case = _make_case()
        google_mod, genai_mod, types_mod = _make_google_mocks()

        tool_resp = _make_google_tool_response(
            "read_file",
            {"path": "foo.rs"},
        )
        text_resp = _make_google_text_response('[{"file":"foo.rs","line":1,"description":"bug"}]')

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            tool_resp,
            text_resp,
        ]

        genai_mod.Client = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "google": google_mod,
                "google.genai": genai_mod,
                "google.genai.types": types_mod,
            },
        ):
            result = run_google_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff+repo",
                max_turns=5,
                timeout=300,
            )

        assert len(result.comments) == 1
        assert result.comments[0].file == "foo.rs"
        assert result.error == ""
        assert mock_client.models.generate_content.call_count == 2


class TestRunGoogleApiCost:
    def test_cost_tracked(self) -> None:
        case = _make_case()
        google_mod, genai_mod, types_mod = _make_google_mocks()

        text_resp = _make_google_text_response(
            "[]",
            inp_tokens=1000,
            out_tokens=500,
        )
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = text_resp

        genai_mod.Client = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "google": google_mod,
                "google.genai": genai_mod,
                "google.genai.types": types_mod,
            },
        ):
            result = run_google_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        # 1000 * 0.15/1M + 500 * 0.60/1M
        assert result.cost_usd > 0
        assert result.cost_usd < 0.01


# ---------------------------------------------------------------------------
# OpenAI API tests
# ---------------------------------------------------------------------------


def _make_openai_text_response(
    text: str,
    inp_tokens: int = 100,
    out_tokens: int = 50,
) -> MagicMock:
    """Build a mock OpenAI chat completion response with text only."""
    message = MagicMock()
    message.content = text
    message.tool_calls = None

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"

    usage = MagicMock()
    usage.prompt_tokens = inp_tokens
    usage.completion_tokens = out_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_openai_tool_response(
    fn_name: str,
    fn_args: str,
    tool_call_id: str = "call_1",
    inp_tokens: int = 50,
    out_tokens: int = 20,
) -> MagicMock:
    """Build a mock OpenAI response with tool calls."""
    fn = MagicMock()
    fn.name = fn_name
    fn.arguments = fn_args

    tc = MagicMock()
    tc.id = tool_call_id
    tc.function = fn

    message = MagicMock()
    message.content = None
    message.tool_calls = [tc]

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "tool_calls"

    usage = MagicMock()
    usage.prompt_tokens = inp_tokens
    usage.completion_tokens = out_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


class TestRunOpenaiApiDiffOnly:
    def test_mocked_openai_returns_result(self) -> None:
        case = _make_case()

        text_resp = _make_openai_text_response('[{"file":"f.rs","line":1,"description":"bug"}]')

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = text_resp

        openai_mod = types.ModuleType("openai")
        openai_mod.OpenAI = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"openai": openai_mod}):
            result = run_openai_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        assert result.case_id == "leo-001"
        assert result.tool == "agent-openai"
        assert result.context_level == "diff-only"
        assert len(result.comments) == 1
        assert result.comments[0].file == "f.rs"
        assert result.error == ""
        assert result.cost_usd > 0

    def test_import_error_returns_error_result(self) -> None:
        with patch.dict(sys.modules, {"openai": None}):
            case = _make_case()
            result = run_openai_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                timeout=60,
            )

        assert result.tool == "agent-openai"
        assert "openai not installed" in result.error


class TestRunOpenaiApiMultiTurn:
    def test_tool_use_then_text(self) -> None:
        case = _make_case()

        tool_resp = _make_openai_tool_response(
            "read_file",
            '{"path": "foo.rs"}',
            "call_1",
        )
        text_resp = _make_openai_text_response('[{"file":"foo.rs","line":1,"description":"bug"}]')

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [tool_resp, text_resp]

        openai_mod = types.ModuleType("openai")
        openai_mod.OpenAI = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"openai": openai_mod}):
            result = run_openai_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff+repo",
                max_turns=5,
                timeout=300,
            )

        assert len(result.comments) == 1
        assert result.comments[0].file == "foo.rs"
        assert result.error == ""
        assert mock_client.chat.completions.create.call_count == 2


class TestRunOpenaiApiCost:
    def test_cost_tracked(self) -> None:
        case = _make_case()

        text_resp = _make_openai_text_response("[]", inp_tokens=1000, out_tokens=500)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = text_resp

        openai_mod = types.ModuleType("openai")
        openai_mod.OpenAI = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"openai": openai_mod}):
            result = run_openai_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        # 1000 * 1.10/1M + 500 * 4.40/1M = 0.0011 + 0.0022 = 0.0033
        assert result.cost_usd > 0
        assert result.cost_usd < 0.01


# ---------------------------------------------------------------------------
# Google Search grounding tests
# ---------------------------------------------------------------------------


class TestRunGoogleApiSearchGrounding:
    def test_google_search_tool_included_diff_only(self) -> None:
        """Google Search grounding is added even for diff-only (no func tools)."""
        case = _make_case()
        google_mod, genai_mod, types_mod = _make_google_mocks()

        text_resp = _make_google_text_response("[]")
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = text_resp
        genai_mod.Client = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "google": google_mod,
                "google.genai": genai_mod,
                "google.genai.types": types_mod,
            },
        ):
            run_google_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        call_kwargs = mock_client.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        tools = getattr(config, "tools", None)
        assert tools is not None
        # Should have exactly one Tool: google_search (no function decls)
        has_search = any(getattr(t, "google_search", None) is not None for t in tools)
        assert has_search, "Google Search grounding tool not found"
        has_func = any(getattr(t, "function_declarations", None) is not None for t in tools)
        assert not has_func, "diff-only should not have function tools"

    def test_google_search_tool_alongside_function_tools(self) -> None:
        """Google Search grounding coexists with function declaration tools."""
        case = _make_case()
        google_mod, genai_mod, types_mod = _make_google_mocks()

        text_resp = _make_google_text_response("[]")
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = text_resp
        genai_mod.Client = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "google": google_mod,
                "google.genai": genai_mod,
                "google.genai.types": types_mod,
            },
        ):
            run_google_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff+repo",
                max_turns=5,
                timeout=300,
            )

        call_kwargs = mock_client.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        tools = getattr(config, "tools", None)
        assert tools is not None
        has_search = any(getattr(t, "google_search", None) is not None for t in tools)
        has_func = any(getattr(t, "function_declarations", None) is not None for t in tools)
        assert has_search, "Google Search grounding tool not found"
        assert has_func, "Function declaration tools not found"

    def test_graceful_fallback_when_google_search_missing(self) -> None:
        """If GoogleSearch is absent from SDK, runner still works."""
        case = _make_case()
        google_mod, genai_mod, types_mod = _make_google_mocks()

        # Remove GoogleSearch to simulate old SDK
        del types_mod.GoogleSearch  # type: ignore[attr-defined]

        text_resp = _make_google_text_response("[]")
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = text_resp
        genai_mod.Client = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "google": google_mod,
                "google.genai": genai_mod,
                "google.genai.types": types_mod,
            },
        ):
            result = run_google_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        # Should succeed without error (no google_search, but still works)
        assert result.error == ""


# ---------------------------------------------------------------------------
# OpenAI web search tool tests
# ---------------------------------------------------------------------------


class TestRunOpenaiApiWebSearch:
    def test_web_search_preview_included_diff_only(self) -> None:
        """web_search_preview is present even for diff-only (no func tools)."""
        case = _make_case()

        text_resp = _make_openai_text_response("[]")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = text_resp

        openai_mod = types.ModuleType("openai")
        openai_mod.OpenAI = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"openai": openai_mod}):
            run_openai_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff-only",
                max_turns=5,
                timeout=300,
            )

        call_kwargs = mock_client.chat.completions.create.call_args
        tools = call_kwargs.kwargs.get("tools")
        assert tools is not None
        web_tools = [t for t in tools if t.get("type") == "web_search_preview"]
        assert len(web_tools) == 1
        # diff-only: no function tools
        func_tools = [t for t in tools if t.get("type") == "function"]
        assert len(func_tools) == 0

    def test_web_search_preview_alongside_function_tools(self) -> None:
        """web_search_preview coexists with function tools for diff+repo."""
        case = _make_case()

        text_resp = _make_openai_text_response("[]")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = text_resp

        openai_mod = types.ModuleType("openai")
        openai_mod.OpenAI = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"openai": openai_mod}):
            run_openai_api(
                case,
                SAMPLE_DIFF,
                None,
                "diff+repo",
                max_turns=5,
                timeout=300,
            )

        call_kwargs = mock_client.chat.completions.create.call_args
        tools = call_kwargs.kwargs.get("tools")
        assert tools is not None
        web_tools = [t for t in tools if t.get("type") == "web_search_preview"]
        func_tools = [t for t in tools if t.get("type") == "function"]
        assert len(web_tools) == 1
        assert len(func_tools) == 3  # read_file, list_directory, search_text


class TestRunSinglePassSdk:
    def test_sdk_import_error_returns_fallback(self) -> None:
        with patch.dict(sys.modules, {"claude_agent_sdk": None}):
            pr = asyncio.run(
                _run_single_pass_sdk(
                    workspace=None,
                    prompt="explore",
                    max_turns=10,
                    model="",
                    timeout=300,
                )
            )
        assert pr.text == "(SDK not installed)"
        assert pr.cost == 0.0

    def test_sdk_path_returns_result(self, tmp_path: Path) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield ResultMessage(
                total_cost_usd=0.02,
                session_id="s1",
                result="found stuff",
            )

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            pr = asyncio.run(
                _run_single_pass_sdk(
                    workspace=tmp_path,
                    prompt="explore",
                    max_turns=10,
                    model="",
                    timeout=300,
                )
            )
        assert pr.text == "found stuff"
        assert pr.cost == 0.02


# ---------------------------------------------------------------------------
# run_agent_sdk_2pass tests
# ---------------------------------------------------------------------------


class TestRunAgentSdk2PassSdk:
    def test_two_pass_sdk_path(self, tmp_path: Path) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        call_count = 0

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Explorer pass
                yield ResultMessage(
                    total_cost_usd=0.03,
                    session_id="s1",
                    result="## Modified Symbols\n- fn changed",
                )
            else:
                # Reviewer pass
                yield ResultMessage(
                    total_cost_usd=0.04,
                    session_id="s2",
                    result='[{"file":"b.rs","line":5,"description":"off by one"}]',
                )

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        ws = tmp_path / "ws"
        ws.mkdir()
        pr_dir = ws / ".pr"
        pr_dir.mkdir()
        (pr_dir / "description.md").write_text("SDK PR")

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            result = run_agent_sdk_2pass(
                case,
                SAMPLE_DIFF,
                workspace=ws,
                context_level="diff+repo",
                timeout=600,
            )

        assert result.tool == "agent-sdk-2pass"
        assert len(result.comments) == 1
        assert result.comments[0].body == "off by one"
        assert result.cost_usd == pytest.approx(0.07)
        assert call_count == 2

    def test_explorer_empty_output_still_runs_reviewer(
        self,
        tmp_path: Path,
    ) -> None:
        sdk_mod, sdk_types_mod = _make_sdk_mocks()
        ResultMessage = sdk_mod.ResultMessage  # type: ignore[attr-defined]

        call_count = 0

        async def fake_query(**kwargs: object):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ResultMessage(
                    total_cost_usd=0.01,
                    session_id="s1",
                    result="",
                )
            else:
                yield ResultMessage(
                    total_cost_usd=0.02,
                    session_id="s2",
                    result="[]",
                )

        sdk_mod.query = fake_query  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "claude_agent_sdk": sdk_mod,
                "claude_agent_sdk.types": sdk_types_mod,
            },
        ):
            case = _make_case()
            result = run_agent_sdk_2pass(
                case,
                SAMPLE_DIFF,
                workspace=None,
                context_level="diff-only",
                timeout=600,
            )

        assert result.tool == "agent-sdk-2pass"
        assert result.comments == []
        assert call_count == 2


# ---------------------------------------------------------------------------
# annotate_diff
# ---------------------------------------------------------------------------


class TestAnnotateDiff:
    def test_strips_whitespace_only_hunk(self) -> None:
        diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -10,1 +10,1 @@\n"
            "-    let x = foo();\n"
            "+        let x = foo();\n"
        )
        result = annotate_diff(diff)
        # The indentation-only change should be stripped
        assert "-    let x = foo();" not in result
        assert "+        let x = foo();" not in result
        assert "formatting" in result.lower() or "FORMATTING" in result

    def test_preserves_real_change(self) -> None:
        diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -10,1 +10,1 @@\n"
            "-    let x = foo();\n"
            "+    let x = bar();\n"
        )
        result = annotate_diff(diff)
        assert "bar()" in result

    def test_annotates_scope_change(self) -> None:
        diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -380,2 +383,2 @@\n"
            "-    vm.add_program(&p)?;\n"
            "+            vm.add_program(&p)?;\n"
        )
        result = annotate_diff(diff)
        assert "SCOPE" in result

    def test_mixed_hunks(self) -> None:
        diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -10,1 +10,1 @@\n"
            "-    let x = foo();\n"
            "+        let x = foo();\n"
            "@@ -20,1 +20,1 @@\n"
            "-    let y = bar();\n"
            "+    let y = baz();\n"
        )
        result = annotate_diff(diff)
        # WS-only hunk stripped, real change preserved
        assert "baz()" in result
        assert "1/2" in result or "FORMATTING" in result

    def test_empty_diff(self) -> None:
        assert annotate_diff("") == ""
        assert annotate_diff("   ") == ""

    def test_all_formatting_warning(self) -> None:
        diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -10,1 +10,1 @@\n"
            "-    let x = foo();\n"
            "+        let x = foo();\n"
        )
        result = annotate_diff(diff)
        assert "WARNING" in result or "ALL" in result

    def test_no_annotation_when_no_stripping(self) -> None:
        diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -10,1 +10,1 @@\n"
            "-    let x = foo();\n"
            "+    let x = bar();\n"
        )
        result = annotate_diff(diff)
        assert "FORMATTING" not in result
        assert "WARNING" not in result


class TestV3Prompts:
    def test_v3_system_mentions_leo(self) -> None:
        from bugeval.agent_runner import _V3_SYSTEM

        assert "leo" in _V3_SYSTEM.lower()
        assert "compiler" in _V3_SYSTEM.lower()

    def test_v3_phase1_asks_for_survey_table(self) -> None:
        from bugeval.agent_runner import _V3_PHASE1_SURVEY

        assert "survey" in _V3_PHASE1_SURVEY.lower()
        assert "caller" in _V3_PHASE1_SURVEY.lower()
        assert "domain.md" in _V3_PHASE1_SURVEY

    def test_v3_phase2_checks_spec_and_scope(self) -> None:
        from bugeval.agent_runner import _V3_PHASE2_INVESTIGATE

        assert "domain.md" in _V3_PHASE2_INVESTIGATE
        assert "scope" in _V3_PHASE2_INVESTIGATE.lower()
        assert "caller" in _V3_PHASE2_INVESTIGATE.lower()

    def test_v3_phase3_requests_json(self) -> None:
        from bugeval.agent_runner import _V3_PHASE3_REPORT

        assert "json" in _V3_PHASE3_REPORT.lower()
        assert "[]" in _V3_PHASE3_REPORT
