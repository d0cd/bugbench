"""Shared repo setup and cleanup for agent evaluation modes."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from bugeval.git_utils import clone_repo, clone_repo_local, is_repo, run_git, sanitize_patch
from bugeval.models import TestCase

_PR_BODY_MAX = 3000

# Per-repo locks prevent concurrent threads from racing to create the same cache
# entry. asyncio.to_thread runs setup_repo_for_case in a thread pool, so we need
# threading.Lock (not asyncio.Lock) here.
_cache_locks: dict[str, threading.Lock] = {}
_cache_locks_mutex = threading.Lock()


def _get_cache_lock(repo: str) -> threading.Lock:
    with _cache_locks_mutex:
        if repo not in _cache_locks:
            _cache_locks[repo] = threading.Lock()
        return _cache_locks[repo]


def get_or_create_cached_repo(repo: str, cache_dir: Path) -> Path:
    """Return path to a cached clone of repo, creating it if absent.

    Thread-safe: uses a per-repo lock so concurrent callers wait rather than
    racing to clone. Detects incomplete caches (directory exists but is not a
    valid git repo) and removes them before re-cloning.
    """
    name = repo.replace("/", "-")
    cache_path = cache_dir / name
    with _get_cache_lock(repo):
        if not is_repo(cache_path):
            # Remove any partial directory left by a previous interrupted clone.
            if cache_path.exists():
                shutil.rmtree(cache_path)
            clone_repo(f"https://github.com/{repo}.git", cache_path)
    return cache_path


def setup_repo_for_case(
    case: TestCase,
    patch_path: Path,
    work_dir: Path,
    cache_dir: Path | None = None,
    apply_patch: bool = False,
) -> Path:
    """Clone repo and checkout base commit. Returns repo directory.

    By default the repo is left at base_commit (the pre-fix state with the bug
    present), which matches what a reviewer would see when a PR is filed. Pass
    apply_patch=True to apply the diff on top, leaving the repo in the post-fix
    state (the old behaviour, now only used in tests).
    """
    repo_dir = work_dir / case.id
    if cache_dir is not None:
        cache_path = get_or_create_cached_repo(case.repo, cache_dir)
        clone_repo_local(cache_path, repo_dir)
    else:
        clone_repo(f"https://github.com/{case.repo}.git", repo_dir)
    run_git("checkout", case.base_commit, cwd=repo_dir)
    if apply_patch:
        run_git("apply", str(patch_path.resolve()), cwd=repo_dir)
    return repo_dir


def materialize_workspace(
    case: TestCase,
    patch_content: str,
    context_level: str,
    workspace_dir: Path,
    blind: bool = False,
) -> None:
    """Write PR context files and sanitized diff into a workspace directory."""
    pr_dir = workspace_dir / ".pr"
    pr_dir.mkdir(parents=True, exist_ok=True)

    # description.md
    sections: list[str] = []
    if blind:
        sections.append("# Pull Request\n\n(description redacted)")
    else:
        if case.pr_title:
            sections.append(f"# {case.pr_title}")
        body = case.pr_body[:_PR_BODY_MAX] if case.pr_body else ""
        if body:
            sections.append(body)
    if case.stats is not None:
        stats_text = (
            f"Files changed: {case.stats.files_changed}\n"
            f"Lines added: {case.stats.lines_added}\n"
            f"Lines deleted: {case.stats.lines_deleted}"
        )
        sections.append(stats_text)
    (pr_dir / "description.md").write_text("\n\n".join(sections))

    # commits.txt
    if blind:
        (pr_dir / "commits.txt").write_text("")
    elif case.pr_commit_messages:
        (pr_dir / "commits.txt").write_text("\n".join(case.pr_commit_messages) + "\n")
    else:
        (pr_dir / "commits.txt").write_text("")

    # domain.md (only for diff+repo+domain)
    if context_level == "diff+repo+domain":
        domain_text = (
            f"Category: {case.category}\n"
            f"Severity: {case.severity}\n"
            f"Language: {case.language}\n"
            f"Description: {case.description}"
        )
        (pr_dir / "domain.md").write_text(domain_text)

    # diff.patch (sanitized)
    (workspace_dir / "diff.patch").write_text(sanitize_patch(patch_content))


def cleanup_repo(repo_dir: Path) -> None:
    """Remove the cloned repo directory."""
    shutil.rmtree(repo_dir, ignore_errors=True)
