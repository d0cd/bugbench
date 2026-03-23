"""Tests for git subprocess wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bugeval.git_utils import (
    GitError,
    clone_at_sha,
    commit_exists,
    ensure_repo,
    ensure_repos,
    get_changed_files,
    get_diff,
    run_git,
)


def _make_completed(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestRunGit:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_completed(stdout="ok\n"),
        )
        assert run_git("status", cwd=Path("/tmp")) == "ok\n"

    def test_failure_raises_git_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_completed(returncode=1, stderr="fatal: bad"),
        )
        with pytest.raises(GitError, match="fatal: bad"):
            run_git("status", cwd=Path("/tmp"))

    def test_timeout_raises_git_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def raise_timeout(*a: object, **kw: object) -> None:
            raise subprocess.TimeoutExpired(cmd="git", timeout=60)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        with pytest.raises(GitError, match="timed out"):
            run_git("log", cwd=Path("/tmp"))


class TestCommitExists:
    def test_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_completed(),
        )
        assert commit_exists("abc123", cwd=Path("/tmp")) is True

    def test_not_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_completed(returncode=1, stderr="not found"),
        )
        assert commit_exists("abc123", cwd=Path("/tmp")) is False


class TestGetChangedFiles:
    def test_parses_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_completed(stdout="src/main.rs\nsrc/lib.rs\n"),
        )
        files = get_changed_files("a", "b", cwd=Path("/tmp"))
        assert files == ["src/main.rs", "src/lib.rs"]

    def test_empty_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_completed(stdout=""),
        )
        assert get_changed_files("a", "b", cwd=Path("/tmp")) == []


class TestGetDiff:
    def test_returns_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_completed(stdout="diff output"),
        )
        assert get_diff("a", "b", cwd=Path("/tmp")) == "diff output"


class TestCloneAtSha:
    def test_url_uses_single_branch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        calls: list[list[str]] = []

        def mock_run(
            cmd: list[str],
            **kw: object,
        ) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            return _make_completed()

        monkeypatch.setattr(subprocess, "run", mock_run)
        dest = tmp_path / "repo"
        result = clone_at_sha("https://example.com/r", dest, "abc")
        assert result == dest
        assert "--single-branch" in calls[0]
        assert "--local" not in calls[0]

    def test_local_path_uses_local_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Create a fake local repo directory
        local_repo = tmp_path / "source_repo"
        local_repo.mkdir()

        calls: list[list[str]] = []

        def mock_run(
            cmd: list[str],
            **kw: object,
        ) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            return _make_completed()

        monkeypatch.setattr(subprocess, "run", mock_run)
        dest = tmp_path / "cloned"
        clone_at_sha(str(local_repo), dest, "abc123")
        assert "--local" in calls[0]
        assert "--single-branch" not in calls[0]

    def test_existing_dest_just_checkouts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        dest = tmp_path / "repo"
        dest.mkdir()  # Already exists

        calls: list[list[str]] = []

        def mock_run(
            cmd: list[str],
            **kw: object,
        ) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            return _make_completed()

        monkeypatch.setattr(subprocess, "run", mock_run)
        clone_at_sha("https://example.com/r", dest, "abc123")
        # Should only checkout, not clone
        assert len(calls) == 1
        assert "checkout" in calls[0]
        assert "clone" not in calls[0]

    def test_clone_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        def raise_timeout(*a: object, **kw: object) -> None:
            raise subprocess.TimeoutExpired(cmd="git", timeout=600)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        with pytest.raises(GitError, match="timed out"):
            clone_at_sha(
                "https://example.com/r",
                tmp_path / "repo",
                "abc",
            )


class TestEnsureRepo:
    def test_clones_missing_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        calls: list[list[str]] = []

        def mock_run(
            cmd: list[str],
            **kw: object,
        ) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            # Simulate clone creating the directory
            (tmp_path / "leo").mkdir(exist_ok=True)
            return _make_completed()

        monkeypatch.setattr(subprocess, "run", mock_run)
        path = ensure_repo("ProvableHQ/leo", tmp_path)
        assert path == tmp_path / "leo"
        assert any("clone" in c for c in calls)

    def test_fetches_existing_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "leo").mkdir()
        fetched = []

        def mock_run(
            cmd: list[str],
            **kw: object,
        ) -> subprocess.CompletedProcess[str]:
            fetched.append(cmd)
            return _make_completed()

        monkeypatch.setattr(subprocess, "run", mock_run)
        path = ensure_repo("ProvableHQ/leo", tmp_path)
        assert path == tmp_path / "leo"
        assert any("fetch" in c for c in fetched)

    def test_fetch_failure_is_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "sdk").mkdir()

        def mock_run(
            cmd: list[str],
            **kw: object,
        ) -> subprocess.CompletedProcess[str]:
            return _make_completed(returncode=1, stderr="network error")

        monkeypatch.setattr(subprocess, "run", mock_run)
        # Should not raise — fetch failure is tolerated
        path = ensure_repo("AleoNet/sdk", tmp_path)
        assert path == tmp_path / "sdk"


class TestEnsureRepos:
    def test_clones_multiple_in_parallel(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        def mock_run(
            cmd: list[str],
            **kw: object,
        ) -> subprocess.CompletedProcess[str]:
            # Simulate clone creating directories
            if "clone" in cmd:
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
            return _make_completed()

        monkeypatch.setattr(subprocess, "run", mock_run)
        repos = ["ProvableHQ/leo", "ProvableHQ/snarkOS", "AleoNet/sdk"]
        result = ensure_repos(repos, tmp_path, concurrency=3)
        assert len(result) == 3
        assert result["ProvableHQ/leo"] == tmp_path / "leo"
        assert result["ProvableHQ/snarkOS"] == tmp_path / "snarkOS"
        assert result["AleoNet/sdk"] == tmp_path / "sdk"
