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
    base_branch: str = "main",
) -> int:
    """Open a PR on fork_repo against upstream_repo. Returns PR number.

    In dry-run mode returns 0 without calling gh.
    """
    if dry_run:
        return 0

    title = case.pr_title or f"[bugeval] {case.id} — {case.description[:60]}"
    sections: list[str] = []
    if case.pr_body:
        sections.append(case.pr_body[:3000])
    if case.pr_commit_messages:
        sections.append("**Commits:**\n" + "\n".join(f"- {m}" for m in case.pr_commit_messages))
    body = "\n\n".join(sections) if sections else case.description
    # PR is opened on the target repo (tools are installed there).
    # --base: intra-repo PR from bug branch → base (clean state).
    output = run_gh(
        "pr",
        "create",
        "--repo",
        fork_repo,
        "--base",
        base_branch,
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
    """Fetch all reviews, inline comments, and PR issue comments for a PR.

    Returns a combined list of comment dicts with a 'source' key:
    - 'review': PR-level review summaries
    - 'inline_comment': inline review comments (line-level)
    - 'issue_comment': regular PR comments (many tools post findings here)
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

    # Inline review comments (line-level)
    try:
        output = run_gh("api", f"repos/{owner}/{repo}/pulls/{pr_number}/comments")
        comments: list[dict[str, Any]] = json.loads(output)
        for c in comments:
            c["source"] = "inline_comment"
            results.append(c)
    except GhError:
        pass

    # PR issue comments (regular comments on the PR thread — many tools post here)
    try:
        output = run_gh("api", f"repos/{owner}/{repo}/issues/{pr_number}/comments")
        issue_comments: list[dict[str, Any]] = json.loads(output)
        for c in issue_comments:
            c["source"] = "issue_comment"
            results.append(c)
    except GhError:
        pass

    return results


def request_review(
    fork_repo: str,
    pr_number: int,
    reviewer: str,
    dry_run: bool = False,
) -> None:
    """Request a review from a specific user or bot (e.g. 'copilot').

    Some tools (like GitHub Copilot) don't auto-trigger on PR creation
    and require an explicit review request.

    Uses the REST API directly because ``gh pr edit --add-reviewer`` can
    fail on orgs with Projects Classic enabled (GraphQL deprecation bug).
    """
    if dry_run:
        return
    owner, repo = fork_repo.split("/", 1)
    run_gh(
        "api",
        f"repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
        "--method", "POST",
        "-f", f"reviewers[]={reviewer}",
    )


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
