"""Copilot evaluation: create PR on fork, wait for review, scrape comments."""

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
    _delete_remote_branch,
    _get_patch_diff,
    _tool_repo_name,
    close_eval_pr,
    create_eval_branches,
    ensure_fork,
    ensure_tool_repo,
    open_eval_pr,
    poll_for_review,
    save_pr_transcript,
    scrape_pr_comments,
)
from bugeval.result_models import ToolResult

log = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    "_delete_remote_branch",
    "_get_patch_diff",
    "_tool_repo_name",
    "close_eval_pr",
    "create_eval_branches",
    "ensure_fork",
    "ensure_tool_repo",
    "open_eval_pr",
    "poll_for_review",
    "scrape_pr_comments",
]


def _scrape_raw_comments(fork: str, pr_number: int) -> list[dict[str, Any]]:
    """Scrape all raw review comments from a PR (unfiltered)."""
    output = run_gh(
        "api",
        f"repos/{fork}/pulls/{pr_number}/comments",
    )
    raw: list[dict[str, Any]] = json.loads(output)
    return raw


_BOT_NAMES: dict[str, str] = {
    "copilot": "copilot",
    "greptile": "greptile",
    "coderabbit": "coderabbitai",
}


def scrape_pr_for_case(
    pending: ToolResult,
    fork: str,
    *,
    close: bool = True,
) -> ToolResult:
    """Check once for a review on a pending PR, scrape if found.

    Does a quick check (not a long poll): timeout=5, poll_interval=5.
    If no review yet, returns the pending result unchanged.
    If review found, scrapes comments and optionally closes the PR.
    """
    bot_name = _BOT_NAMES.get(pending.tool, pending.tool)
    found = poll_for_review(
        fork,
        pending.pr_number,
        bot_name,
        timeout=5,
        poll_interval=5,
    )
    if not found:
        # Re-trigger for tools that need explicit triggers
        if pending.tool == "greptile":
            from bugeval.greptile_runner import _trigger_greptile

            _trigger_greptile(fork, pending.pr_number)
        elif pending.tool == "coderabbit":
            from bugeval.coderabbit_runner import _trigger_coderabbit

            _trigger_coderabbit(fork, pending.pr_number)
        return pending

    comments = scrape_pr_comments(fork, pending.pr_number, bot_name)

    new_state = "reviewed"
    if close:
        close_eval_pr(
            fork,
            pending.pr_number,
            pending.pr_head_branch,
            pending.pr_base_branch,
        )
        new_state = "closed"

    return pending.model_copy(
        update={"comments": comments, "pr_state": new_state},
    )


def open_pr_for_case(
    case: TestCase,
    repo_dir: Path,
    tool: str,
    org: str = "",
) -> ToolResult:
    """Open a PR for a test case and trigger tool-specific review.

    Returns a ToolResult with pr_state="pending-review" and no comments.
    The caller is expected to poll/scrape later in a separate phase.
    """
    pr_number = 0
    try:
        patch_diff = _get_patch_diff(case, repo_dir)
        if org:
            fork = ensure_tool_repo(case.repo, tool, org)
        else:
            fork = ensure_fork(case.repo)

        base_branch, head_branch = create_eval_branches(
            fork=fork,
            case=case,
            patch_diff=patch_diff,
            repo_dir=repo_dir,
        )

        pr_number = open_eval_pr(fork, head_branch, base_branch, case)

        # Trigger tool-specific review
        if tool == "greptile":
            from bugeval.greptile_runner import _trigger_greptile

            _trigger_greptile(fork, pr_number)
        elif tool == "coderabbit":
            from bugeval.coderabbit_runner import (
                _trigger_coderabbit,
            )

            _trigger_coderabbit(fork, pr_number)
        # copilot: automatic, no trigger needed

        return ToolResult(
            case_id=case.id,
            tool=tool,
            context_level="diff+repo",
            comments=[],
            pr_number=pr_number,
            pr_state="pending-review",
            pr_head_branch=head_branch,
            pr_base_branch=base_branch,
        )
    except (
        GhError,
        GitError,
        subprocess.CalledProcessError,
        OSError,
        json.JSONDecodeError,
        RuntimeError,
    ) as exc:
        return ToolResult(
            case_id=case.id,
            tool=tool,
            context_level="diff+repo",
            comments=[],
            pr_number=pr_number,
            error=str(exc),
        )


def run_copilot(
    case: TestCase,
    repo_dir: Path,
    timeout: int = 300,
    org: str = "",
    transcript_dir: Path | None = None,
) -> ToolResult:
    """Run the full Copilot evaluation lifecycle for a test case."""
    start = time.monotonic()
    fork = ""
    base_branch = ""
    head_branch = ""
    pr_number = 0
    patch_diff = ""
    try:
        patch_diff = _get_patch_diff(case, repo_dir)
        fork = ensure_tool_repo(case.repo, "copilot", org) if org else ensure_fork(case.repo)

        # Push opaque base + head branches (no case ID in names)
        base_branch, head_branch = create_eval_branches(
            fork=fork,
            case=case,
            patch_diff=patch_diff,
            repo_dir=repo_dir,
        )

        pr_number = open_eval_pr(fork, head_branch, base_branch, case)
        found = poll_for_review(fork, pr_number, timeout=timeout)

        if not found:
            elapsed = time.monotonic() - start
            if transcript_dir:
                save_pr_transcript(
                    transcript_dir,
                    case.id,
                    "copilot",
                    fork=fork,
                    branch=head_branch,
                    pr_number=pr_number,
                    scrubbed_title=_scrub_fix_references(
                        case.introducing_pr_title or "",
                    ),
                    scrubbed_body=_scrub_fix_references(
                        case.introducing_pr_body or "",
                    ),
                    raw_comments=[],
                    patch_diff=patch_diff,
                    time_seconds=elapsed,
                )
            return ToolResult(
                case_id=case.id,
                tool="copilot",
                context_level="diff+repo",
                comments=[],
                time_seconds=elapsed,
                pr_number=pr_number,
                error=f"Timeout waiting for Copilot review ({timeout}s)",
            )

        raw_comments = _scrape_raw_comments(fork, pr_number)
        comments = scrape_pr_comments(fork, pr_number)
        elapsed = time.monotonic() - start

        scrubbed_title = _scrub_fix_references(
            case.introducing_pr_title or "",
        )
        scrubbed_body = _scrub_fix_references(
            case.introducing_pr_body or "",
        )
        transcript_path = ""
        if transcript_dir:
            transcript_path = save_pr_transcript(
                transcript_dir,
                case.id,
                "copilot",
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
            tool="copilot",
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
            tool="copilot",
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
