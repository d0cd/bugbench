"""Thin subprocess wrapper for git operations."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    def __init__(self, command: list[str], stderr: str) -> None:
        self.command = command
        self.stderr = stderr
        super().__init__(f"Git command failed: {' '.join(command)}\n{stderr}")


def run_git(*args: str, cwd: Path, timeout: int = 60) -> str:
    """Run a git command and return stdout. Raises GitError on failure."""
    cmd = ["git", *args]
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GitError(cmd, f"Command timed out after {timeout}s")
    if result.returncode != 0:
        raise GitError(cmd, result.stderr)
    return result.stdout


def commit_exists(sha: str, cwd: Path) -> bool:
    """Check whether a commit SHA exists in the repo."""
    try:
        run_git("cat-file", "-e", sha, cwd=cwd)
        return True
    except GitError:
        return False


def get_diff(base: str, head: str, cwd: Path) -> str:
    """Return the diff between two commits."""
    return run_git("diff", base, head, cwd=cwd)


def get_changed_files(base: str, head: str, cwd: Path) -> list[str]:
    """Return list of files changed between two commits."""
    output = run_git("diff", "--name-only", base, head, cwd=cwd)
    return [f for f in output.strip().splitlines() if f]


def ensure_repo(
    repo: str,
    repos_dir: Path,
    timeout: int = 600,
) -> Path:
    """Clone repo if missing, or fetch latest if it exists. Returns local path."""
    slug = repo.split("/", 1)[1] if "/" in repo else repo
    dest = repos_dir / slug
    if dest.exists():
        try:
            run_git("fetch", "--all", cwd=dest, timeout=timeout)
        except GitError:
            pass  # offline is OK — we already have the repo
        return dest
    url = f"https://github.com/{repo}.git"
    cmd = ["git", "clone", url, str(dest)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise GitError(cmd, f"Clone timed out after {timeout}s")
    if result.returncode != 0:
        raise GitError(cmd, result.stderr)
    return dest


def ensure_repos(
    repos: list[str],
    repos_dir: Path,
    concurrency: int = 4,
) -> dict[str, Path]:
    """Clone/fetch multiple repos in parallel. Returns {repo: local_path}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    repos_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(ensure_repo, repo, repos_dir): repo for repo in repos}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                path = future.result()
                result[repo] = path
            except Exception as exc:
                raise GitError(["clone"], f"Failed to clone {repo}: {exc}")

    return result


def clone_at_sha(source: str, dest: Path, sha: str, timeout: int = 600) -> Path:
    """Clone repo and checkout at specific SHA.

    If *source* is a local path, uses --local for fast hardlink cloning.
    """
    if dest.exists():
        # Already cloned (e.g., previous aborted run) — just checkout
        run_git("checkout", sha, cwd=dest)
        return dest
    is_local = not source.startswith("http") and Path(source).is_dir()
    cmd = ["git", "clone"]
    if is_local:
        cmd.append("--local")
    else:
        cmd.append("--single-branch")
    cmd.extend([source, str(dest)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GitError(cmd, f"Clone timed out after {timeout}s")
    if result.returncode != 0:
        raise GitError(cmd, result.stderr)
    run_git("checkout", sha, cwd=dest)
    return dest
