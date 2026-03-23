"""Tests for the copilot_runner module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bugeval.copilot_runner import (
    _delete_remote_branch,
    _get_patch_diff,
    close_eval_pr,
    create_eval_branches,
    ensure_fork,
    open_eval_pr,
    open_pr_for_case,
    poll_for_review,
    run_copilot,
    scrape_pr_comments,
    scrape_pr_for_case,
)
from bugeval.mine import GhError
from bugeval.models import CaseKind, GroundTruth, TestCase
from bugeval.result_models import Comment, ToolResult


def _make_case(**overrides: object) -> TestCase:
    defaults = {
        "id": "snarkVM-001",
        "repo": "AleoNet/snarkVM",
        "kind": CaseKind.bug,
        "base_commit": "abc123",
        "introducing_pr_number": 42,
        "introducing_pr_title": "Add new feature",
        "introducing_pr_body": "This adds a feature",
    }
    defaults.update(overrides)
    return TestCase(**defaults)  # type: ignore[arg-type]


class TestEnsureFork:
    @patch("bugeval.copilot_runner.run_gh")
    def test_returns_fork_name(self, mock_gh: MagicMock) -> None:
        mock_gh.side_effect = [
            # fork command
            "",
            # whoami
            "testuser\n",
        ]
        result = ensure_fork("AleoNet/snarkVM")
        assert result == "testuser/snarkVM"
        mock_gh.assert_any_call(
            "repo",
            "fork",
            "AleoNet/snarkVM",
            "--clone=false",
        )

    @patch("bugeval.copilot_runner.run_gh")
    def test_fork_already_exists(self, mock_gh: MagicMock) -> None:
        from bugeval.mine import GhError

        mock_gh.side_effect = [
            GhError(["gh", "repo", "fork"], "already exists"),
            "testuser\n",
        ]
        result = ensure_fork("AleoNet/snarkVM")
        assert result == "testuser/snarkVM"

    @patch("bugeval.copilot_runner.run_gh")
    def test_fork_with_org_returns_org_name(self, mock_gh: MagicMock) -> None:
        # When org is provided, only the fork command is called — no whoami
        mock_gh.return_value = ""
        result = ensure_fork("AleoNet/snarkVM", org="myorg")
        assert result == "myorg/snarkVM"
        call_args = mock_gh.call_args_list[0][0]
        assert "--org" in call_args
        assert "myorg" in call_args
        # Should NOT have called the user API
        assert mock_gh.call_count == 1


class TestCreateEvalBranches:
    @patch("bugeval.copilot_runner.subprocess.run")
    def test_creates_branches_and_pushes(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        case = _make_case()
        base_branch, head_branch = create_eval_branches(
            fork="testuser/snarkVM",
            case=case,
            patch_diff="diff --git a/f.rs b/f.rs\n",
            repo_dir=tmp_path,
        )
        assert base_branch.startswith("base-")
        assert head_branch.startswith("review-")
        assert mock_run.call_count >= 3  # checkout, apply, push
        # Verify checkout uses parent of base_commit (introducing_commit fallback)
        first_call_args = mock_run.call_args_list[0][0][0]
        assert "abc123~1" in first_call_args

    @patch("bugeval.copilot_runner.subprocess.run")
    def test_uses_introducing_commit_from_truth(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        case = _make_case(
            truth=GroundTruth(introducing_commit="intro999"),
        )
        create_eval_branches(
            fork="testuser/snarkVM",
            case=case,
            patch_diff="diff --git a/f.rs b/f.rs\n",
            repo_dir=tmp_path,
        )
        first_call_args = mock_run.call_args_list[0][0][0]
        assert "intro999~1" in first_call_args

    @patch("bugeval.copilot_runner._delete_remote_branch")
    @patch("bugeval.copilot_runner.subprocess.run")
    def test_cleans_up_on_push_failure(
        self,
        mock_run: MagicMock,
        mock_delete: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If head push fails after base push, orphaned base branch is deleted."""
        ok = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        fail = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="push rejected",
        )

        call_count = 0

        def _side_effect(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            cmd = args[0] if args else kwargs.get("args", [])
            # The last subprocess.run call is the head push — make it fail.
            # Calls: checkout, amend, push(base), checkout, amend,
            #        apply, add, commit, push(head)
            if "push" in cmd and call_count > 3:
                return fail
            return ok

        mock_run.side_effect = _side_effect
        case = _make_case()
        with pytest.raises(GhError):
            create_eval_branches(
                fork="testuser/snarkVM",
                case=case,
                patch_diff="diff --git a/f.rs b/f.rs\n",
                repo_dir=tmp_path,
            )
        # Orphaned base branch should be cleaned up
        mock_delete.assert_called_once()
        call_args = mock_delete.call_args[0]
        assert call_args[0] == "testuser/snarkVM"
        assert call_args[1].startswith("base-")

    @patch("bugeval.copilot_runner._delete_remote_branch")
    @patch("bugeval.copilot_runner.subprocess.run")
    def test_aborts_cherrypick_on_failure(
        self,
        mock_run: MagicMock,
        mock_delete: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If apply fails and cherry-pick fails, cherry-pick --abort is called."""
        ok = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        apply_fail = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="apply failed",
        )
        cherry_fail = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="cherry-pick conflict",
        )

        def _side_effect(*args: Any, **kwargs: Any) -> Any:
            cmd = args[0] if args else kwargs.get("args", [])
            if "apply" in cmd:
                return apply_fail
            if "cherry-pick" in cmd and "--abort" not in cmd:
                return cherry_fail
            return ok

        mock_run.side_effect = _side_effect
        case = _make_case()
        with pytest.raises(GhError, match="cherry-pick conflict"):
            create_eval_branches(
                fork="testuser/snarkVM",
                case=case,
                patch_diff="diff --git a/f.rs b/f.rs\n",
                repo_dir=tmp_path,
            )
        # cherry-pick --abort should have been called
        abort_calls = [
            c for c in mock_run.call_args_list if "cherry-pick" in c[0][0] and "--abort" in c[0][0]
        ]
        assert len(abort_calls) >= 1

    @patch("bugeval.copilot_runner._delete_remote_branch")
    @patch("bugeval.copilot_runner.subprocess.run")
    def test_resets_working_tree(
        self,
        mock_run: MagicMock,
        mock_delete: MagicMock,
        tmp_path: Path,
    ) -> None:
        """On any failure, checkout -f and clean -fd are called in finally."""
        ok = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        fail = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="boom",
        )

        call_count = 0

        def _side_effect(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            cmd = args[0] if args else kwargs.get("args", [])
            # Fail on the first push (base push)
            if "push" in cmd and call_count <= 5:
                return fail
            return ok

        mock_run.side_effect = _side_effect
        case = _make_case()
        with pytest.raises(GhError):
            create_eval_branches(
                fork="testuser/snarkVM",
                case=case,
                patch_diff="diff --git a/f.rs b/f.rs\n",
                repo_dir=tmp_path,
            )
        # Verify finally block ran checkout -f and clean -fd
        all_cmds = [c[0][0] for c in mock_run.call_args_list]
        checkout_f_calls = [c for c in all_cmds if "checkout" in c and "-f" in c and "HEAD" in c]
        clean_calls = [c for c in all_cmds if "clean" in c and "-fd" in c]
        assert len(checkout_f_calls) >= 1
        assert len(clean_calls) >= 1


class TestDeleteRemoteBranch:
    @patch("bugeval.copilot_runner.run_gh")
    def test_deletes_branch(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = ""
        _delete_remote_branch("testuser/snarkVM", "base-abc123")
        mock_gh.assert_called_once()
        call_args = mock_gh.call_args[0]
        assert "repos/testuser/snarkVM/git/refs/heads/base-abc123" in call_args

    @patch("bugeval.copilot_runner.run_gh")
    def test_ignores_404(self, mock_gh: MagicMock) -> None:
        mock_gh.side_effect = GhError(["gh"], "404")
        # Should not raise
        _delete_remote_branch("testuser/snarkVM", "base-abc123")


class TestOpenEvalPr:
    @patch("bugeval.copilot_runner.run_gh")
    def test_returns_pr_number(
        self,
        mock_gh: MagicMock,
    ) -> None:
        mock_gh.return_value = "https://github.com/testuser/snarkVM/pull/99\n"
        case = _make_case()
        result = open_eval_pr(
            "testuser/snarkVM",
            "review-abc",
            "base-abc",
            case,
        )
        assert result == 99

    @patch("bugeval.copilot_runner.run_gh")
    def test_targets_base_branch(
        self,
        mock_gh: MagicMock,
    ) -> None:
        mock_gh.return_value = "https://github.com/testuser/snarkVM/pull/7\n"
        case = _make_case()
        open_eval_pr(
            "testuser/snarkVM",
            "review-abc",
            "base-abc",
            case,
        )
        call_args = mock_gh.call_args[0]
        assert "--base" in call_args
        base_idx = list(call_args).index("--base")
        assert call_args[base_idx + 1] == "base-abc"

    @patch("bugeval.copilot_runner.run_gh")
    def test_uses_pr_metadata(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = "https://github.com/testuser/snarkVM/pull/7\n"
        case = _make_case(
            introducing_pr_title="Refactor validator rotation logic",
            introducing_pr_body="Detailed body about refactoring",
        )
        open_eval_pr(
            "testuser/snarkVM",
            "review-abc",
            "base-abc",
            case,
        )
        call_args = mock_gh.call_args[0]
        # Title is in CLI args
        assert "Refactor validator rotation logic" in call_args
        # Body is written to a temp file (--body-file), not in CLI args
        assert "--body-file" in call_args

    @patch("bugeval.copilot_runner.run_gh")
    def test_scrubs_fix_references_in_title(
        self,
        mock_gh: MagicMock,
    ) -> None:
        mock_gh.return_value = "https://github.com/testuser/snarkVM/pull/7\n"
        case = _make_case(
            introducing_pr_title="Fix overflow",
            introducing_pr_body="",
        )
        open_eval_pr(
            "testuser/snarkVM",
            "review-abc",
            "base-abc",
            case,
        )
        call_args = mock_gh.call_args[0]
        # "Fix overflow" gets scrubbed, falls back to "code changes"
        assert "code changes" in call_args


class TestScrapePrComments:
    @patch("bugeval.copilot_runner.run_gh")
    def test_parses_review_comments(self, mock_gh: MagicMock) -> None:
        pr_comments = json.dumps(
            [
                {
                    "path": "src/main.rs",
                    "line": 42,
                    "body": "Potential null deref here",
                    "user": {"login": "copilot[bot]"},
                },
                {
                    "path": "src/lib.rs",
                    "line": 10,
                    "body": "Consider error handling",
                    "user": {"login": "copilot[bot]"},
                },
            ]
        )
        # First call = PR review comments, second = issue comments (empty)
        mock_gh.side_effect = [pr_comments, "[]"]
        comments = scrape_pr_comments("testuser/snarkVM", 99)
        assert len(comments) == 2
        assert comments[0].file == "src/main.rs"
        assert comments[0].line == 42
        assert comments[0].body == "Potential null deref here"

    @patch("bugeval.copilot_runner.run_gh")
    def test_filters_non_copilot(self, mock_gh: MagicMock) -> None:
        pr_comments = json.dumps(
            [
                {
                    "path": "src/main.rs",
                    "line": 42,
                    "body": "Copilot finding",
                    "user": {"login": "copilot[bot]"},
                },
                {
                    "path": "src/lib.rs",
                    "line": 10,
                    "body": "Human comment",
                    "user": {"login": "somedev"},
                },
            ]
        )
        mock_gh.side_effect = [pr_comments, "[]"]
        comments = scrape_pr_comments("testuser/snarkVM", 99)
        assert len(comments) == 1
        assert comments[0].body == "Copilot finding"

    @patch("bugeval.copilot_runner.run_gh")
    def test_empty_comments(self, mock_gh: MagicMock) -> None:
        mock_gh.side_effect = ["[]", "[]"]
        comments = scrape_pr_comments("testuser/snarkVM", 99)
        assert comments == []


class TestCloseEvalPr:
    @patch("bugeval.copilot_runner.run_gh")
    def test_closes_and_deletes_branches(
        self,
        mock_gh: MagicMock,
    ) -> None:
        mock_gh.return_value = ""
        close_eval_pr(
            "testuser/snarkVM",
            99,
            "review-abc",
            "base-abc",
        )
        assert mock_gh.call_count == 3  # close PR + delete head + delete base


class TestPollForReview:
    @patch("bugeval.copilot_runner.time.sleep")
    @patch("bugeval.copilot_runner.run_gh")
    def test_found_review(
        self,
        mock_gh: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_gh.return_value = json.dumps(
            {
                "reviews": [
                    {"author": {"login": "copilot[bot]"}, "state": "COMMENTED"},
                ],
            }
        )
        result = poll_for_review("testuser/snarkVM", 99, timeout=60)
        assert result is True

    @patch("bugeval.copilot_runner.time.sleep")
    @patch("bugeval.copilot_runner.time.monotonic")
    @patch("bugeval.copilot_runner.run_gh")
    def test_timeout(
        self,
        mock_gh: MagicMock,
        mock_time: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_gh.return_value = json.dumps({"reviews": []})
        # Simulate time passing beyond timeout
        mock_time.side_effect = [0.0, 100.0, 400.0]
        result = poll_for_review(
            "testuser/snarkVM",
            99,
            timeout=300,
            poll_interval=15,
        )
        assert result is False


class TestRunCopilot:
    @patch("bugeval.copilot_runner.close_eval_pr")
    @patch("bugeval.copilot_runner._scrape_raw_comments")
    @patch("bugeval.copilot_runner.scrape_pr_comments")
    @patch("bugeval.copilot_runner.poll_for_review")
    @patch("bugeval.copilot_runner.open_eval_pr")
    @patch("bugeval.copilot_runner.create_eval_branches")
    @patch("bugeval.copilot_runner.ensure_fork")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_success(
        self,
        mock_diff: MagicMock,
        mock_fork: MagicMock,
        mock_branch: MagicMock,
        mock_open: MagicMock,
        mock_poll: MagicMock,
        mock_scrape: MagicMock,
        mock_raw: MagicMock,
        mock_close: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.return_value = "diff content"
        mock_fork.return_value = "testuser/snarkVM"
        mock_branch.return_value = ("base-abc", "review-abc")
        mock_open.return_value = 99
        mock_poll.return_value = True
        mock_raw.return_value = [
            {
                "path": "src/main.rs",
                "line": 42,
                "body": "Bug found",
                "user": {"login": "copilot[bot]"},
            },
        ]
        mock_scrape.return_value = [
            Comment(
                file="src/main.rs",
                line=42,
                body="Bug found",
            ),
        ]
        case = _make_case()
        result = run_copilot(case, tmp_path)
        assert result.case_id == "snarkVM-001"
        assert result.tool == "copilot"
        assert len(result.comments) == 1
        assert result.error == ""
        mock_close.assert_called_once_with(
            "testuser/snarkVM",
            99,
            "review-abc",
            "base-abc",
        )

    @patch("bugeval.copilot_runner.close_eval_pr")
    @patch("bugeval.copilot_runner.poll_for_review")
    @patch("bugeval.copilot_runner.open_eval_pr")
    @patch("bugeval.copilot_runner.create_eval_branches")
    @patch("bugeval.copilot_runner.ensure_fork")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_timeout(
        self,
        mock_diff: MagicMock,
        mock_fork: MagicMock,
        mock_branch: MagicMock,
        mock_open: MagicMock,
        mock_poll: MagicMock,
        mock_close: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.return_value = "diff content"
        mock_fork.return_value = "testuser/snarkVM"
        mock_branch.return_value = ("base-abc", "review-abc")
        mock_open.return_value = 99
        mock_poll.return_value = False
        case = _make_case()
        result = run_copilot(case, tmp_path, timeout=60)
        assert result.case_id == "snarkVM-001"
        assert result.tool == "copilot"
        assert "timeout" in result.error.lower()
        mock_close.assert_called_once_with(
            "testuser/snarkVM",
            99,
            "review-abc",
            "base-abc",
        )

    @patch("bugeval.copilot_runner.ensure_fork")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_error(
        self,
        mock_diff: MagicMock,
        mock_fork: MagicMock,
        tmp_path: Path,
    ) -> None:
        from bugeval.mine import GhError

        mock_diff.return_value = "diff content"
        mock_fork.side_effect = GhError(
            ["gh", "repo", "fork"],
            "network error",
        )
        case = _make_case()
        result = run_copilot(case, tmp_path)
        assert result.case_id == "snarkVM-001"
        assert result.tool == "copilot"
        assert result.error != ""
        assert len(result.comments) == 0


class TestGetPatchDiff:
    @patch("bugeval.copilot_runner.run_git")  # patched at import location
    def test_uses_introducing_commit(self, mock_git: MagicMock, tmp_path: Path) -> None:
        mock_git.return_value = "diff output"
        case = _make_case(
            truth=GroundTruth(introducing_commit="intro999"),
        )
        result = _get_patch_diff(case, tmp_path)
        assert result == "diff output"
        mock_git.assert_called_once_with(
            "diff",
            "intro999~1",
            "intro999",
            cwd=tmp_path,
        )

    @patch("bugeval.copilot_runner.run_git")  # patched at import location
    def test_falls_back_to_base_commit(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.return_value = "diff output"
        case = _make_case()  # no truth, base_commit="abc123"
        result = _get_patch_diff(case, tmp_path)
        assert result == "diff output"
        mock_git.assert_called_once_with(
            "diff",
            "abc123~1",
            "abc123",
            cwd=tmp_path,
        )

    def test_no_commit_returns_empty(self, tmp_path: Path) -> None:
        case = _make_case(base_commit="")
        result = _get_patch_diff(case, tmp_path)
        assert result == ""


class TestCopilotTranscript:
    @patch("bugeval.copilot_runner.close_eval_pr")
    @patch("bugeval.copilot_runner._scrape_raw_comments")
    @patch("bugeval.copilot_runner.scrape_pr_comments")
    @patch("bugeval.copilot_runner.poll_for_review")
    @patch("bugeval.copilot_runner.open_eval_pr")
    @patch("bugeval.copilot_runner.create_eval_branches")
    @patch("bugeval.copilot_runner.ensure_fork")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_transcript_saved(
        self,
        mock_diff: MagicMock,
        mock_fork: MagicMock,
        mock_branch: MagicMock,
        mock_open: MagicMock,
        mock_poll: MagicMock,
        mock_scrape: MagicMock,
        mock_raw: MagicMock,
        mock_close: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.return_value = "diff content"
        mock_fork.return_value = "testuser/snarkVM"
        mock_branch.return_value = ("base-abc", "review-abc")
        mock_open.return_value = 99
        mock_poll.return_value = True
        mock_raw.return_value = [
            {
                "path": "src/main.rs",
                "line": 42,
                "body": "Bug found",
                "user": {"login": "copilot[bot]"},
            },
        ]
        mock_scrape.return_value = [
            Comment(file="src/main.rs", line=42, body="Bug found"),
        ]
        transcript_dir = tmp_path / "transcripts"
        case = _make_case()
        result = run_copilot(
            case,
            tmp_path,
            transcript_dir=transcript_dir,
        )
        assert result.transcript_path != ""
        path = Path(result.transcript_path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert "pr_metadata" in data
        assert data["pr_metadata"]["fork"] == "testuser/snarkVM"
        assert data["pr_metadata"]["pr_number"] == 99
        assert "raw_comments" in data
        assert "patch_diff" in data
        assert "scrubbed_title" in data
        assert "scrubbed_body" in data
        assert "time_seconds" in data

    @patch("bugeval.copilot_runner.close_eval_pr")
    @patch("bugeval.copilot_runner._scrape_raw_comments")
    @patch("bugeval.copilot_runner.scrape_pr_comments")
    @patch("bugeval.copilot_runner.poll_for_review")
    @patch("bugeval.copilot_runner.open_eval_pr")
    @patch("bugeval.copilot_runner.create_eval_branches")
    @patch("bugeval.copilot_runner.ensure_fork")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_no_transcript_without_dir(
        self,
        mock_diff: MagicMock,
        mock_fork: MagicMock,
        mock_branch: MagicMock,
        mock_open: MagicMock,
        mock_poll: MagicMock,
        mock_scrape: MagicMock,
        mock_raw: MagicMock,
        mock_close: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.return_value = "diff content"
        mock_fork.return_value = "testuser/snarkVM"
        mock_branch.return_value = ("base-abc", "review-abc")
        mock_open.return_value = 99
        mock_poll.return_value = True
        mock_raw.return_value = []
        mock_scrape.return_value = []
        case = _make_case()
        result = run_copilot(case, tmp_path)
        assert result.transcript_path == ""


class TestEnsureForkOrgReturn:
    @patch("bugeval.copilot_runner.run_gh")
    def test_org_returns_org_not_username(self, mock_gh: MagicMock) -> None:
        """When org is given, return org/name even if fork raises."""
        from bugeval.mine import GhError

        mock_gh.side_effect = GhError(["gh"], "already exists")
        result = ensure_fork("AleoNet/snarkVM", org="eval-org")
        assert result == "eval-org/snarkVM"
        # Should NOT have queried the user API
        assert mock_gh.call_count == 1


class TestOpenPrForCase:
    @patch("bugeval.copilot_runner.open_eval_pr")
    @patch("bugeval.copilot_runner.create_eval_branches")
    @patch("bugeval.copilot_runner.ensure_tool_repo")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_returns_pending_result(
        self,
        mock_diff: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_open: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.return_value = "diff content"
        mock_repo.return_value = "org/snarkVM-copilot"
        mock_branch.return_value = ("base-abc", "review-abc")
        mock_open.return_value = 42
        case = _make_case()
        result = open_pr_for_case(case, tmp_path, "copilot", org="org")
        assert result.pr_number == 42
        assert result.pr_state == "pending-review"
        assert result.pr_head_branch == "review-abc"
        assert result.pr_base_branch == "base-abc"
        assert result.tool == "copilot"
        assert result.comments == []
        assert result.error == ""

    @patch("bugeval.greptile_runner._trigger_greptile")
    @patch("bugeval.copilot_runner.open_eval_pr")
    @patch("bugeval.copilot_runner.create_eval_branches")
    @patch("bugeval.copilot_runner.ensure_tool_repo")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_triggers_greptile(
        self,
        mock_diff: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_open: MagicMock,
        mock_trigger: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.return_value = "diff content"
        mock_repo.return_value = "org/snarkVM-greptile"
        mock_branch.return_value = ("base-abc", "review-abc")
        mock_open.return_value = 42
        case = _make_case()
        result = open_pr_for_case(
            case,
            tmp_path,
            "greptile",
            org="org",
        )
        assert result.pr_number == 42
        assert result.pr_state == "pending-review"
        assert result.tool == "greptile"
        mock_trigger.assert_called_once_with(
            "org/snarkVM-greptile",
            42,
        )

    @patch("bugeval.coderabbit_runner._trigger_coderabbit")
    @patch("bugeval.copilot_runner.open_eval_pr")
    @patch("bugeval.copilot_runner.create_eval_branches")
    @patch("bugeval.copilot_runner.ensure_tool_repo")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_triggers_coderabbit(
        self,
        mock_diff: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_open: MagicMock,
        mock_trigger: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.return_value = "diff content"
        mock_repo.return_value = "org/snarkVM-coderabbit"
        mock_branch.return_value = ("base-abc", "review-abc")
        mock_open.return_value = 42
        case = _make_case()
        result = open_pr_for_case(
            case,
            tmp_path,
            "coderabbit",
            org="org",
        )
        assert result.pr_number == 42
        assert result.pr_state == "pending-review"
        assert result.tool == "coderabbit"
        mock_trigger.assert_called_once_with(
            "org/snarkVM-coderabbit",
            42,
        )

    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_error_returns_error_result(
        self,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.side_effect = RuntimeError("network failure")
        case = _make_case()
        result = open_pr_for_case(
            case,
            tmp_path,
            "copilot",
            org="org",
        )
        assert result.error != ""
        assert result.case_id == "snarkVM-001"
        assert result.tool == "copilot"
        assert result.comments == []

    @patch("bugeval.copilot_runner.open_eval_pr")
    @patch("bugeval.copilot_runner.create_eval_branches")
    @patch("bugeval.copilot_runner.ensure_fork")
    @patch("bugeval.copilot_runner._get_patch_diff")
    def test_no_org_uses_ensure_fork(
        self,
        mock_diff: MagicMock,
        mock_fork: MagicMock,
        mock_branch: MagicMock,
        mock_open: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_diff.return_value = "diff content"
        mock_fork.return_value = "user/snarkVM"
        mock_branch.return_value = ("base-abc", "review-abc")
        mock_open.return_value = 10
        case = _make_case()
        result = open_pr_for_case(case, tmp_path, "copilot")
        assert result.pr_number == 10
        mock_fork.assert_called_once()


class TestScrapePrForCase:
    def _pending(self, tool: str = "copilot") -> ToolResult:
        return ToolResult(
            case_id="leo-001",
            tool=tool,
            pr_number=42,
            pr_state="pending-review",
            pr_head_branch="review-abc",
            pr_base_branch="base-abc",
        )

    @patch("bugeval.copilot_runner.close_eval_pr")
    @patch("bugeval.copilot_runner.scrape_pr_comments")
    @patch("bugeval.copilot_runner.poll_for_review")
    def test_scrapes_when_review_found(
        self,
        mock_poll: MagicMock,
        mock_scrape: MagicMock,
        mock_close: MagicMock,
    ) -> None:
        mock_poll.return_value = True
        mock_scrape.return_value = [
            Comment(file="f.rs", line=1, body="bug"),
        ]
        result = scrape_pr_for_case(
            self._pending(),
            "org/leo-copilot",
            close=True,
        )
        assert result.pr_state == "closed"
        assert len(result.comments) == 1
        assert result.comments[0].body == "bug"
        mock_close.assert_called_once_with(
            "org/leo-copilot",
            42,
            "review-abc",
            "base-abc",
        )

    @patch("bugeval.copilot_runner.poll_for_review")
    def test_returns_pending_when_no_review(
        self,
        mock_poll: MagicMock,
    ) -> None:
        mock_poll.return_value = False
        result = scrape_pr_for_case(
            self._pending(),
            "org/leo-copilot",
            close=False,
        )
        assert result.pr_state == "pending-review"
        assert result.comments == []

    @patch("bugeval.copilot_runner.scrape_pr_comments")
    @patch("bugeval.copilot_runner.poll_for_review")
    def test_reviewed_without_close(
        self,
        mock_poll: MagicMock,
        mock_scrape: MagicMock,
    ) -> None:
        mock_poll.return_value = True
        mock_scrape.return_value = [
            Comment(file="f.rs", line=1, body="issue"),
        ]
        result = scrape_pr_for_case(
            self._pending(),
            "org/leo-copilot",
            close=False,
        )
        assert result.pr_state == "reviewed"
        assert len(result.comments) == 1

    @patch("bugeval.copilot_runner.scrape_pr_comments")
    @patch("bugeval.copilot_runner.poll_for_review")
    def test_correct_bot_name_for_coderabbit(
        self,
        mock_poll: MagicMock,
        mock_scrape: MagicMock,
    ) -> None:
        mock_poll.return_value = True
        mock_scrape.return_value = []
        pending = self._pending(tool="coderabbit")
        scrape_pr_for_case(pending, "org/leo-coderabbit", close=False)
        mock_poll.assert_called_once_with(
            "org/leo-coderabbit",
            42,
            "coderabbitai",
            timeout=5,
            poll_interval=5,
        )
        mock_scrape.assert_called_once_with(
            "org/leo-coderabbit",
            42,
            "coderabbitai",
        )


class TestTwoPhaseRoundTrip:
    def test_open_save_load_scrape(
        self,
        monkeypatch: Any,
        tmp_path: Path,
    ) -> None:
        """Full round-trip: open -> save -> load -> scrape -> closed with comments."""
        from bugeval.io import load_result, save_result

        case = _make_case()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock GitHub calls for open phase
        monkeypatch.setattr(
            "bugeval.copilot_runner._get_patch_diff",
            lambda *a: "diff",
        )
        monkeypatch.setattr(
            "bugeval.copilot_runner.ensure_tool_repo",
            lambda *a, **kw: "org/leo-copilot",
        )
        monkeypatch.setattr(
            "bugeval.copilot_runner.create_eval_branches",
            lambda *a, **kw: ("base-abc", "review-abc"),
        )
        monkeypatch.setattr(
            "bugeval.copilot_runner.open_eval_pr",
            lambda *a, **kw: 42,
        )

        # Phase 1: Open
        result = open_pr_for_case(case, repo_dir, "copilot", org="org")
        assert result.pr_state == "pending-review"
        assert result.pr_number == 42

        # Save to disk
        result_path = tmp_path / "result.yaml"
        save_result(result, result_path)

        # Load back
        loaded = load_result(result_path)
        assert loaded.pr_state == "pending-review"
        assert loaded.pr_number == 42
        assert loaded.pr_head_branch == "review-abc"
        assert loaded.pr_base_branch == "base-abc"

        # Mock GitHub calls for scrape phase
        monkeypatch.setattr(
            "bugeval.copilot_runner.poll_for_review",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "bugeval.copilot_runner.scrape_pr_comments",
            lambda *a, **kw: [Comment(file="f.rs", line=10, body="bug here")],
        )
        monkeypatch.setattr(
            "bugeval.copilot_runner.close_eval_pr",
            lambda *a, **kw: None,
        )

        # Phase 2: Scrape
        final = scrape_pr_for_case(loaded, "org/leo-copilot", close=True)
        assert final.pr_state == "closed"
        assert len(final.comments) == 1
        assert final.comments[0].file == "f.rs"
        assert final.pr_number == 42
