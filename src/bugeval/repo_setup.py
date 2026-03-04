"""Shared repo setup and cleanup for agent evaluation modes."""

from __future__ import annotations

import shutil
from pathlib import Path

from bugeval.git_utils import clone_repo, run_git
from bugeval.models import TestCase


def setup_repo_for_case(case: TestCase, patch_path: Path, work_dir: Path) -> Path:
    """Clone repo, checkout base commit, apply patch. Returns repo directory."""
    repo_dir = work_dir / case.id
    clone_repo(f"https://github.com/{case.repo}.git", repo_dir)
    run_git("checkout", case.base_commit, cwd=repo_dir)
    run_git("apply", str(patch_path), cwd=repo_dir)
    return repo_dir


def cleanup_repo(repo_dir: Path) -> None:
    """Remove the cloned repo directory."""
    shutil.rmtree(repo_dir, ignore_errors=True)
