"""Greptile evaluation: PR-based lifecycle via GitHub App."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from bugeval.agent_runner import _scrub_fix_references
from bugeval.git_utils import GitError
from bugeval.mine import GhError, run_gh
from bugeval.models import TestCase
from bugeval.pr_utils import (
    _get_patch_diff,
    close_eval_pr,
    create_eval_branches,
    ensure_fork,
    ensure_tool_repo,
    open_eval_pr,
    poll_for_review,
    save_pr_transcript,
    scrape_pr_comments,
)
from bugeval.result_models import Comment, ToolResult

log = logging.getLogger(__name__)


def _trigger_greptile(fork: str, pr_number: int) -> None:
    """Comment @greptile on the PR to trigger a review."""
    try:
        run_gh(
            "pr",
            "comment",
            str(pr_number),
            "--repo",
            fork,
            "--body",
            "@greptile",
        )
        log.info("Triggered Greptile review on PR #%d", pr_number)
    except GhError:
        log.warning("Failed to trigger Greptile on PR #%d", pr_number)


def poll_for_greptile_review(
    fork: str,
    pr_number: int,
    timeout: int = 300,
    poll_interval: int = 15,
) -> bool:
    """Trigger Greptile via @greptileai comment, then poll for review."""
    _trigger_greptile(fork, pr_number)
    return poll_for_review(fork, pr_number, "greptile", timeout, poll_interval)


def scrape_greptile_comments(fork: str, pr_number: int) -> list[Comment]:
    """Scrape review comments from a PR, filtering to Greptile only."""
    return scrape_pr_comments(fork, pr_number, "greptile")


def _scrape_raw_greptile_comments(
    fork: str,
    pr_number: int,
) -> list[dict[str, Any]]:
    """Scrape all raw review comments from a PR (unfiltered)."""
    output = run_gh(
        "api",
        f"repos/{fork}/pulls/{pr_number}/comments",
    )
    raw: list[dict[str, Any]] = json.loads(output)
    return raw


def run_greptile(
    case: TestCase,
    repo_dir: Path,
    timeout: int = 300,
    org: str = "",
    transcript_dir: Path | None = None,
) -> ToolResult:
    """Run the full Greptile evaluation lifecycle for a test case."""
    start = time.monotonic()
    fork = ""
    base_branch = ""
    head_branch = ""
    pr_number = 0
    patch_diff = ""
    try:
        patch_diff = _get_patch_diff(case, repo_dir)
        fork = ensure_tool_repo(case.repo, "greptile", org) if org else ensure_fork(case.repo)

        base_branch, head_branch = create_eval_branches(
            fork=fork,
            case=case,
            patch_diff=patch_diff,
            repo_dir=repo_dir,
        )

        pr_number = open_eval_pr(fork, head_branch, base_branch, case)

        scrubbed_title = (
            _scrub_fix_references(case.introducing_pr_title) if case.introducing_pr_title else ""
        )
        scrubbed_body = (
            _scrub_fix_references(case.introducing_pr_body) if case.introducing_pr_body else ""
        )

        found = poll_for_greptile_review(fork, pr_number, timeout=timeout)

        if not found:
            elapsed = time.monotonic() - start
            if transcript_dir:
                save_pr_transcript(
                    transcript_dir,
                    case.id,
                    "greptile",
                    fork=fork,
                    branch=head_branch,
                    pr_number=pr_number,
                    scrubbed_title=scrubbed_title,
                    scrubbed_body=scrubbed_body,
                    raw_comments=[],
                    patch_diff=patch_diff,
                    time_seconds=elapsed,
                )
            return ToolResult(
                case_id=case.id,
                tool="greptile",
                context_level="diff+repo",
                comments=[],
                time_seconds=elapsed,
                pr_number=pr_number,
                error=f"Timeout waiting for Greptile review ({timeout}s)",
            )

        raw_comments = _scrape_raw_greptile_comments(fork, pr_number)
        comments = scrape_greptile_comments(fork, pr_number)
        elapsed = time.monotonic() - start

        transcript_path = ""
        if transcript_dir:
            transcript_path = save_pr_transcript(
                transcript_dir,
                case.id,
                "greptile",
                fork=fork,
                branch=head_branch,
                pr_number=pr_number,
                scrubbed_title=scrubbed_title,
                scrubbed_body=scrubbed_body,
                raw_comments=raw_comments,
                patch_diff=patch_diff,
                time_seconds=elapsed,
            )
        return ToolResult(
            case_id=case.id,
            tool="greptile",
            context_level="diff+repo",
            comments=comments,
            time_seconds=elapsed,
            transcript_path=transcript_path,
            pr_number=pr_number,
        )
    except (
        GhError,
        GitError,
        subprocess.CalledProcessError,
        OSError,
        json.JSONDecodeError,
        RuntimeError,
    ) as exc:
        elapsed = time.monotonic() - start
        return ToolResult(
            case_id=case.id,
            tool="greptile",
            context_level="diff+repo",
            comments=[],
            time_seconds=elapsed,
            pr_number=pr_number,
            error=str(exc),
        )
    finally:
        if pr_number and fork and head_branch:
            try:
                close_eval_pr(fork, pr_number, head_branch, base_branch)
            except (GhError, subprocess.CalledProcessError, OSError):
                log.warning(
                    "Failed to clean up PR #%d on %s",
                    pr_number,
                    fork,
                )
