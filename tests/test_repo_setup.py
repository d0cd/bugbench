"""Tests for repo_setup."""

from pathlib import Path
from unittest.mock import patch

import pytest

from bugeval.git_utils import GitError
from bugeval.models import Category, Difficulty, PRSize, Severity, TestCase
from bugeval.repo_setup import (
    cleanup_repo,
    get_or_create_cached_repo,
    materialize_workspace,
    setup_repo_for_case,
)


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
    """Default: checkout base_commit only (no git apply) — repo stays in pre-fix state."""
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
    assert mock_git.call_count == 1
    mock_git.assert_called_once_with("checkout", case.base_commit, cwd=expected_repo_dir)
    assert result == expected_repo_dir


def test_setup_repo_for_case_apply_patch_true(tmp_path: Path) -> None:
    """apply_patch=True: checkout base_commit then apply the diff (post-fix state)."""
    case = _make_case()
    patch_path = tmp_path / "case.patch"
    patch_path.write_text("--- a/foo\n+++ b/foo\n")

    with (
        patch("bugeval.repo_setup.clone_repo") as mock_clone,
        patch("bugeval.repo_setup.run_git") as mock_git,
    ):
        mock_clone.return_value = tmp_path / case.id
        result = setup_repo_for_case(case, patch_path, tmp_path, apply_patch=True)

    expected_repo_dir = tmp_path / case.id
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


def test_setup_repo_for_case_uses_local_clone_when_cache_provided(tmp_path: Path) -> None:
    case = _make_case()
    patch_path = tmp_path / "case.patch"
    patch_path.write_text("--- a/foo\n+++ b/foo\n")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached_repo = cache_dir / "provable-org-aleo-lang"
    cached_repo.mkdir()

    with (
        patch("bugeval.repo_setup.clone_repo_local") as mock_local_clone,
        patch("bugeval.repo_setup.run_git") as mock_git,
        patch("bugeval.repo_setup.get_or_create_cached_repo", return_value=cached_repo),
    ):
        result = setup_repo_for_case(case, patch_path, tmp_path, cache_dir=cache_dir)

    mock_local_clone.assert_called_once_with(cached_repo, tmp_path / case.id)
    assert mock_git.call_count == 1  # only checkout, no apply
    assert result == tmp_path / case.id


def test_setup_repo_for_case_falls_back_to_remote_when_no_cache(tmp_path: Path) -> None:
    case = _make_case()
    patch_path = tmp_path / "case.patch"
    patch_path.write_text("--- a/foo\n+++ b/foo\n")

    with (
        patch("bugeval.repo_setup.clone_repo") as mock_clone,
        patch("bugeval.repo_setup.run_git"),
    ):
        mock_clone.return_value = tmp_path / case.id
        setup_repo_for_case(case, patch_path, tmp_path, cache_dir=None)

    mock_clone.assert_called_once_with(f"https://github.com/{case.repo}.git", tmp_path / case.id)


def test_get_or_create_cached_repo_creates_on_miss(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    with (
        patch("bugeval.repo_setup.is_repo", return_value=False),
        patch("bugeval.repo_setup.clone_repo") as mock_clone,
    ):
        result = get_or_create_cached_repo("provable-org/aleo-lang", cache_dir)

    expected = cache_dir / "provable-org-aleo-lang"
    mock_clone.assert_called_once_with("https://github.com/provable-org/aleo-lang.git", expected)
    assert result == expected


def test_get_or_create_cached_repo_reuses_on_hit(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached_repo = cache_dir / "provable-org-aleo-lang"
    cached_repo.mkdir()

    with (
        patch("bugeval.repo_setup.is_repo", return_value=True),
        patch("bugeval.repo_setup.clone_repo") as mock_clone,
    ):
        result = get_or_create_cached_repo("provable-org/aleo-lang", cache_dir)

    mock_clone.assert_not_called()
    assert result == cached_repo


def test_get_or_create_cached_repo_cleans_partial_cache(tmp_path: Path) -> None:
    """A directory that exists but isn't a valid git repo is removed before re-cloning."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    partial = cache_dir / "provable-org-aleo-lang"
    partial.mkdir()
    (partial / "some_partial_file").write_text("incomplete")

    with (
        patch("bugeval.repo_setup.is_repo", return_value=False),
        patch("bugeval.repo_setup.clone_repo") as mock_clone,
    ):
        get_or_create_cached_repo("provable-org/aleo-lang", cache_dir)

    mock_clone.assert_called_once()
    # shutil.rmtree was called on the partial dir (verified by clone being called once)


def test_get_or_create_cached_repo_concurrent_calls_clone_once(tmp_path: Path) -> None:
    """Concurrent threads calling get_or_create_cached_repo for the same repo only clone once."""
    import concurrent.futures

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    clone_call_count = 0

    def fake_clone(url: str, path: Path) -> None:
        nonlocal clone_call_count
        clone_call_count += 1

    # is_repo returns False first call, True thereafter (simulates clone completing)
    is_repo_results: list[bool] = []

    def fake_is_repo(path: Path) -> bool:
        if not is_repo_results:
            is_repo_results.append(True)
            return False  # first caller sees no valid repo
        return True  # subsequent callers see completed repo

    with (
        patch("bugeval.repo_setup.is_repo", side_effect=fake_is_repo),
        patch("bugeval.repo_setup.clone_repo", side_effect=fake_clone),
    ):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(get_or_create_cached_repo, "provable-org/aleo-lang", cache_dir)
                for _ in range(5)
            ]
            concurrent.futures.wait(futures)

    assert clone_call_count == 1


def test_clone_repo_local_calls_git_clone_local(tmp_path: Path) -> None:
    from bugeval.git_utils import clone_repo_local

    src = tmp_path / "source"
    dest = tmp_path / "dest"

    with patch("bugeval.git_utils.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        clone_repo_local(src, dest)

    args = mock_run.call_args[0][0]
    assert args[0] == "git"
    assert "--local" in args
    assert str(src) in args
    assert str(dest) in args


def test_cleanup_repo_removes_directory(tmp_path: Path) -> None:
    repo_dir = tmp_path / "some-repo"
    repo_dir.mkdir()
    (repo_dir / "file.rs").write_text("fn main() {}")

    with patch("bugeval.repo_setup.shutil.rmtree") as mock_rmtree:
        cleanup_repo(repo_dir)
    mock_rmtree.assert_called_once_with(repo_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# materialize_workspace tests
# ---------------------------------------------------------------------------


def _make_case_with_pr(**overrides: object) -> TestCase:
    defaults: dict[str, object] = {
        "id": "aleo-lang-001",
        "repo": "provable-org/aleo-lang",
        "base_commit": "abc123",
        "head_commit": "def456",
        "fix_commit": "def456",
        "category": Category.logic,
        "difficulty": Difficulty.medium,
        "severity": Severity.high,
        "language": "rust",
        "pr_size": PRSize.small,
        "description": "Off-by-one in loop bound",
        "expected_findings": [],
        "pr_title": "Fix loop bound error",
        "pr_body": "This PR fixes the off-by-one error in the loop.",
        "pr_commit_messages": ["fix: correct loop bound", "test: add boundary test"],
        "stats": {"lines_added": 5, "lines_deleted": 2, "files_changed": 1, "hunks": 1},
    }
    defaults.update(overrides)
    return TestCase(**defaults)  # type: ignore[arg-type]


class TestMaterializeWorkspace:
    def test_creates_pr_directory(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws)
        assert (ws / ".pr").is_dir()

    def test_writes_description_md_with_title_and_body(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws)
        desc = (ws / ".pr" / "description.md").read_text()
        assert "# Fix loop bound error" in desc
        assert "This PR fixes the off-by-one error in the loop." in desc

    def test_writes_commits_txt(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws)
        commits = (ws / ".pr" / "commits.txt").read_text()
        assert "fix: correct loop bound\n" in commits
        assert "test: add boundary test\n" in commits

    def test_writes_diff_patch(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "--- a/foo\n+++ b/foo\n", "diff-only", ws)
        assert (ws / "diff.patch").exists()
        content = (ws / "diff.patch").read_text()
        assert "--- a/foo" in content

    def test_diff_patch_is_sanitized(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        raw = "diff --git a/f b/f\nindex abc123..def456 100644\n--- a/f\n+++ b/f\n"
        materialize_workspace(case, raw, "diff-only", ws)
        content = (ws / "diff.patch").read_text()
        assert "index abc123..def456" not in content

    def test_writes_domain_md_for_diff_repo_domain(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff+repo+domain", ws)
        domain = (ws / ".pr" / "domain.md").read_text()
        assert "Category: logic" in domain
        assert "Severity: high" in domain
        assert "Language: rust" in domain
        assert "Description: Off-by-one in loop bound" in domain

    def test_no_domain_md_for_diff_only(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws)
        assert not (ws / ".pr" / "domain.md").exists()

    def test_description_md_includes_stats(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws)
        desc = (ws / ".pr" / "description.md").read_text()
        assert "5" in desc  # lines_added
        assert "2" in desc  # lines_deleted
        assert "1" in desc  # files_changed

    def test_empty_pr_context_still_creates_files(self, tmp_path: Path) -> None:
        case = _make_case_with_pr(
            pr_title="",
            pr_body="",
            pr_commit_messages=[],
            stats=None,
        )
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "", "diff-only", ws)
        assert (ws / ".pr" / "description.md").exists()
        assert (ws / ".pr" / "commits.txt").exists()
        assert (ws / "diff.patch").exists()

    def test_pr_body_truncated_when_too_long(self, tmp_path: Path) -> None:
        long_body = "x" * 5000
        case = _make_case_with_pr(pr_body=long_body)
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws)
        desc = (ws / ".pr" / "description.md").read_text()
        # Body should be truncated to 3000 chars
        assert len(long_body) == 5000
        assert "x" * 3000 in desc
        assert "x" * 3001 not in desc

    def test_blind_mode_redacts_description(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws, blind=True)
        desc = (ws / ".pr" / "description.md").read_text()
        assert "Fix loop bound error" not in desc
        assert "off-by-one" not in desc
        assert "Pull Request" in desc
        assert "redacted" in desc.lower()

    def test_blind_mode_empties_commits(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws, blind=True)
        commits = (ws / ".pr" / "commits.txt").read_text()
        assert commits == ""

    def test_blind_mode_preserves_diff(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "--- a/foo\n+++ b/foo\n", "diff-only", ws, blind=True)
        assert (ws / "diff.patch").exists()
        content = (ws / "diff.patch").read_text()
        assert "--- a/foo" in content

    def test_blind_mode_preserves_stats(self, tmp_path: Path) -> None:
        case = _make_case_with_pr()
        ws = tmp_path / "workspace"
        ws.mkdir()
        materialize_workspace(case, "diff content", "diff-only", ws, blind=True)
        desc = (ws / ".pr" / "description.md").read_text()
        assert "Files changed:" in desc
