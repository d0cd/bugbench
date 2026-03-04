"""Tests for repo_setup."""

from pathlib import Path
from unittest.mock import patch

import pytest

from bugeval.git_utils import GitError
from bugeval.models import Category, Difficulty, PRSize, Severity, TestCase
from bugeval.repo_setup import cleanup_repo, setup_repo_for_case


def _make_case() -> TestCase:
    return TestCase(
        id="aleo-lang-001",
        repo="provable-org/aleo-lang",
        base_commit="abc123",
        head_commit="def456",
        fix_commit="def456",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.small,
        description="Test case",
        expected_findings=[],
    )


def test_setup_repo_for_case_calls_clone_and_git(tmp_path: Path) -> None:
    case = _make_case()
    patch_path = tmp_path / "case.patch"
    patch_path.write_text("--- a/foo\n+++ b/foo\n")

    with (
        patch("bugeval.repo_setup.clone_repo") as mock_clone,
        patch("bugeval.repo_setup.run_git") as mock_git,
    ):
        mock_clone.return_value = tmp_path / case.id
        result = setup_repo_for_case(case, patch_path, tmp_path)

    expected_repo_dir = tmp_path / case.id
    mock_clone.assert_called_once_with(f"https://github.com/{case.repo}.git", expected_repo_dir)
    assert mock_git.call_count == 2
    mock_git.assert_any_call("checkout", case.base_commit, cwd=expected_repo_dir)
    mock_git.assert_any_call("apply", str(patch_path), cwd=expected_repo_dir)
    assert result == expected_repo_dir


def test_setup_repo_for_case_clone_failure(tmp_path: Path) -> None:
    case = _make_case()
    patch_path = tmp_path / "case.patch"

    with patch("bugeval.repo_setup.clone_repo") as mock_clone:
        mock_clone.side_effect = GitError(["git", "clone"], "fatal: repo not found")
        with pytest.raises(GitError):
            setup_repo_for_case(case, patch_path, tmp_path)


def test_cleanup_repo_removes_directory(tmp_path: Path) -> None:
    repo_dir = tmp_path / "some-repo"
    repo_dir.mkdir()
    (repo_dir / "file.rs").write_text("fn main() {}")

    with patch("bugeval.repo_setup.shutil.rmtree") as mock_rmtree:
        cleanup_repo(repo_dir)
    mock_rmtree.assert_called_once_with(repo_dir, ignore_errors=True)
