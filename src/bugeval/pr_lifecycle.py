"""Stateless helpers for each step of the PR lifecycle."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from bugeval.git_utils import run_git
from bugeval.github_scraper import GhError, run_gh
from bugeval.models import TestCase


def make_branch_name(case_id: str, tool: str) -> str:
    """Compute a sanitized branch name for a (case, tool) pair.

    Returns a branch name of the form 'bugeval/{slug}' with max 80 chars.
    """
    raw = f"{case_id}-{tool}"
    slug = re.sub(r"[^a-zA-Z0-9._-]", "-", raw)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    branch = f"bugeval/{slug}"
    return branch[:80]


def apply_patch_to_branch(
    branch: str,
    base_commit: str,
    patch_path: Path,
    fork_url: str,
    cwd: Path,
) -> None:
    """Checkout a new branch, apply patch, commit, and push to fork_url.

    Uses 'git apply' (not 'git am') since patches are raw unified diffs.
    """
    run_git("checkout", "-b", branch, base_commit, cwd=cwd)
    run_git("apply", str(patch_path), cwd=cwd)
    run_git("add", "-A", cwd=cwd)
    run_git(
        "commit",
        "--message",
        f"bugeval: apply patch for {branch}",
        "--allow-empty",
        cwd=cwd,
    )
    run_git("push", fork_url, f"{branch}:{branch}", cwd=cwd)


def open_pr(
    fork_repo: str,
    upstream_repo: str,
    branch: str,
    case: TestCase,
    dry_run: bool = False,
) -> int:
    """Open a PR on fork_repo against upstream_repo. Returns PR number.

    In dry-run mode returns 0 without calling gh.
    """
    if dry_run:
        return 0

    owner, name = upstream_repo.split("/", 1)
    title = f"[bugeval] {case.id} — {case.description[:60]}"
    body = (
        f"Automated bugeval PR for case `{case.id}`.\n\n"
        f"Repo: {upstream_repo}\n"
        f"Base commit: `{case.base_commit}`\n"
    )
    output = run_gh(
        "pr",
        "create",
        "--repo",
        fork_repo,
        "--base",
        f"{owner}:{name}:main",
        "--head",
        branch,
        "--title",
        title,
        "--body",
        body,
    )
    # gh pr create outputs the PR URL; extract the number from it
    match = re.search(r"/pull/(\d+)", output)
    if match:
        return int(match.group(1))
    # Fallback: query the PR number from the API
    fork_output = run_gh(
        "pr",
        "list",
        "--repo",
        fork_repo,
        "--head",
        branch,
        "--json",
        "number",
        "--limit",
        "1",
    )
    prs: list[dict[str, Any]] = json.loads(fork_output)
    if prs:
        return int(prs[0]["number"])
    return 0


def poll_for_review(
    fork_repo: str,
    pr_number: int,
    timeout_seconds: int = 3600,
    poll_interval: int = 60,
) -> bool:
    """Poll GitHub until a PR review appears or timeout is reached.

    Returns True if a review was found, False on timeout.
    """
    owner, repo = fork_repo.split("/", 1)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            output = run_gh("api", f"repos/{owner}/{repo}/pulls/{pr_number}/reviews")
            reviews: list[dict[str, Any]] = json.loads(output)
            if reviews:
                return True
        except GhError:
            pass
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
    return False


def scrape_review_comments(fork_repo: str, pr_number: int) -> list[dict[str, Any]]:
    """Fetch all reviews and inline comments for a PR.

    Returns a combined list of comment dicts with a 'source' key.
    """
    owner, repo = fork_repo.split("/", 1)
    results: list[dict[str, Any]] = []

    # PR-level reviews
    try:
        output = run_gh("api", f"repos/{owner}/{repo}/pulls/{pr_number}/reviews")
        reviews: list[dict[str, Any]] = json.loads(output)
        for r in reviews:
            r["source"] = "review"
            results.append(r)
    except GhError:
        pass

    # Inline review comments
    try:
        output = run_gh("api", f"repos/{owner}/{repo}/pulls/{pr_number}/comments")
        comments: list[dict[str, Any]] = json.loads(output)
        for c in comments:
            c["source"] = "inline_comment"
            results.append(c)
    except GhError:
        pass

    return results


def close_pr_delete_branch(
    fork_repo: str,
    pr_number: int,
    branch: str,
    dry_run: bool = False,
) -> None:
    """Close a PR and delete its branch on the fork.

    In dry-run mode prints what would be done without calling gh.
    """
    if dry_run:
        return
    run_gh(
        "pr",
        "close",
        str(pr_number),
        "--repo",
        fork_repo,
        "--delete-branch",
    )
