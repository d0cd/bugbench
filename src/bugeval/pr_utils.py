"""Shared PR evaluation utilities used by copilot, greptile, and coderabbit runners."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from bugeval.agent_runner import _scrub_fix_references
from bugeval.git_utils import run_git
from bugeval.mine import GhError, run_gh
from bugeval.models import TestCase
from bugeval.result_models import Comment

log = logging.getLogger(__name__)

_GIT_TIMEOUT = 120


def _tool_repo_name(repo: str, tool: str) -> str:
    """Build the per-tool repo name: {repo_slug}-{tool}."""
    _, name = repo.split("/", 1)
    return f"{name}-{tool}"


def ensure_tool_repo(repo: str, tool: str, org: str) -> str:
    """Ensure a dedicated per-tool repo exists in the org, return full name.

    Creates `{org}/{repo_slug}-{tool}` (e.g. `bug-tools-eval/leo-copilot`)
    as a private repo if it doesn't already exist. Each tool gets its own
    isolated repo — no cross-tool contamination.
    """
    repo_name = _tool_repo_name(repo, tool)
    full_name = f"{org}/{repo_name}"

    # Check if it already exists
    try:
        run_gh("repo", "view", full_name, "--json", "name")
        return full_name
    except GhError:
        pass

    # Create it
    try:
        run_gh(
            "repo",
            "create",
            full_name,
            "--private",
            "--description",
            f"bugeval: {tool} evaluation repo for {repo}",
        )
    except GhError:
        # May already exist (race condition) — verify
        run_gh("repo", "view", full_name, "--json", "name")

    return full_name


def ensure_fork(repo: str, org: str = "") -> str:
    """Legacy: create a standard GitHub fork. Prefer ensure_tool_repo()."""
    args = ["repo", "fork", repo, "--clone=false"]
    if org:
        args.extend(["--org", org])
    try:
        run_gh(*args)
    except GhError:
        pass
    _, name = repo.split("/", 1)
    if org:
        return f"{org}/{name}"
    username = run_gh(
        "api",
        "user",
        "--jq",
        ".login",
    ).strip()
    return f"{username}/{name}"


def _opaque_id() -> str:
    """Generate a short opaque ID for branch names (no case info leakage)."""
    import hashlib

    return hashlib.sha256(str(time.monotonic()).encode()).hexdigest()[:10]


def _delete_remote_branch(fork: str, branch: str) -> None:
    """Delete a remote branch via GitHub API. Ignores 404 (already gone)."""
    try:
        run_gh(
            "api",
            "--method",
            "DELETE",
            f"repos/{fork}/git/refs/heads/{branch}",
        )
    except GhError:
        pass  # 404 or other — branch may already be gone


def create_eval_branches(
    fork: str,
    case: TestCase,
    patch_diff: str,
    repo_dir: Path,
) -> tuple[str, str]:
    """Push base and review branches to the tool repo. Returns (base_branch, head_branch).

    - base_branch: repo state at introducing~1 (what was there before the bug)
    - head_branch: repo state after applying the introducing changes (the buggy PR)
    - Branch names are opaque (no case ID, no commit SHA) to prevent info leakage
    - Commit messages are generic
    """
    opaque = _opaque_id()
    base_branch = f"base-{opaque}"
    head_branch = f"review-{opaque}"
    introducing = (case.truth.introducing_commit if case.truth else None) or case.base_commit

    _eval_env = {
        **os.environ.copy(),
        "GIT_COMMITTER_NAME": "bugeval",
        "GIT_COMMITTER_EMAIL": "bugeval@users.noreply.github.com",
        "GIT_AUTHOR_NAME": "bugeval",
        "GIT_AUTHOR_EMAIL": "bugeval@users.noreply.github.com",
    }

    def _git(*args: str) -> None:
        cmd = ["git", "-C", str(repo_dir), *args]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=_eval_env,
        )
        if result.returncode != 0:
            raise GhError(cmd, result.stderr)

    base_pushed = False
    try:
        # Push base branch (introducing~1)
        # Amend with generic identity to avoid GH007 email privacy rejections
        _git("checkout", "-B", base_branch, f"{introducing}~1")
        _git(
            "commit",
            "--amend",
            "--no-edit",
            "--allow-empty",
            "--reset-author",
        )
        _git(
            "push",
            "--force",
            f"https://github.com/{fork}.git",
            f"{base_branch}:{base_branch}",
        )
        base_pushed = True

        # Push head branch (introducing changes applied on top of base)
        _git("checkout", "-B", head_branch, f"{introducing}~1")
        _git(
            "commit",
            "--amend",
            "--no-edit",
            "--allow-empty",
            "--reset-author",
        )

        # Apply the introducing diff. Try git apply first; fall back to
        # cherry-pick for binary patches that git apply can't handle.
        apply_cmd = [
            "git",
            "-C",
            str(repo_dir),
            "apply",
            "--allow-empty",
        ]
        proc = subprocess.run(
            apply_cmd,
            input=patch_diff,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=_eval_env,
        )
        if proc.returncode != 0:
            # Fallback: cherry-pick the introducing commit directly
            log.info(
                "git apply failed for %s, trying cherry-pick of %s",
                case.id,
                introducing,
            )
            try:
                _git("cherry-pick", "--no-commit", introducing)
            except GhError as cp_err:
                # Abort the failed cherry-pick before raising
                try:
                    _git("cherry-pick", "--abort")
                except GhError:
                    pass
                raise cp_err

        _git("add", "-A")
        _git("commit", "-m", "code changes", "--allow-empty")
        _git(
            "push",
            "--force",
            f"https://github.com/{fork}.git",
            f"{head_branch}:{head_branch}",
        )
        return base_branch, head_branch
    except Exception:
        # Clean up orphaned base branch if head was never pushed
        if base_pushed:
            _delete_remote_branch(fork, base_branch)
        raise
    finally:
        # Always reset local working tree
        try:
            _git("cherry-pick", "--abort")
        except GhError:
            pass
        try:
            _git("checkout", "-f", "HEAD")
        except GhError:
            pass
        try:
            _git("clean", "-fd")
        except GhError:
            pass


def open_eval_pr(
    fork: str,
    head_branch: str,
    base_branch: str,
    case: TestCase,
) -> int:
    """Open a PR on the tool repo: head_branch → base_branch."""
    scrubbed_title = (
        _scrub_fix_references(case.introducing_pr_title) if case.introducing_pr_title else ""
    )
    title = scrubbed_title or "code changes"
    body = _scrub_fix_references(case.introducing_pr_body) if case.introducing_pr_body else ""
    # Write body to temp file to avoid shell escaping issues with special chars
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        delete=False,
    ) as f:
        f.write(body)
        body_file = f.name

    try:
        output = run_gh(
            "pr",
            "create",
            "--repo",
            fork,
            "--head",
            head_branch,
            "--base",
            base_branch,
            "--title",
            title,
            "--body-file",
            body_file,
        )
    finally:
        Path(body_file).unlink(missing_ok=True)

    url = output.strip()
    pr_number = int(url.rstrip("/").split("/")[-1])
    log.info("Opened PR #%d on %s", pr_number, fork)
    return pr_number


def poll_for_review(
    fork: str,
    pr_number: int,
    bot_name: str = "copilot",
    timeout: int = 300,
    poll_interval: int = 15,
) -> bool:
    """Poll until a review or inline comments from the named bot appear, or timeout."""
    start = time.monotonic()
    while True:
        # Check reviews (e.g., copilot-pull-request-reviewer[bot])
        try:
            output = run_gh(
                "pr",
                "view",
                str(pr_number),
                "--repo",
                fork,
                "--json",
                "reviews",
            )
            data = json.loads(output)
            reviews = data.get("reviews") or []
            for review in reviews:
                author = (review.get("author") or {}).get("login", "")
                if bot_name.lower() in author.lower():
                    log.info(
                        "%s review found on PR #%d",
                        bot_name,
                        pr_number,
                    )
                    return True
        except (GhError, json.JSONDecodeError):
            pass  # gh pr view may fail on fresh repos; fall through to comment check

        # Check inline PR review comments AND issue comments.
        # Some bots (Copilot) post as PR review comments,
        # others (CodeRabbit, Greptile) post as issue comments.
        for endpoint in (
            f"repos/{fork}/pulls/{pr_number}/comments",
            f"repos/{fork}/issues/{pr_number}/comments",
        ):
            try:
                output = run_gh("api", endpoint)
                comments = json.loads(output)
                if isinstance(comments, list):
                    for comment in comments:
                        if not isinstance(comment, dict):
                            continue
                        user = comment.get("user") or {}
                        login = str(user.get("login", ""))
                        if bot_name.lower() in login.lower():
                            log.info(
                                "%s comment found on PR #%d (%s)",
                                bot_name,
                                pr_number,
                                endpoint.split("/")[-1],
                            )
                            return True
            except (GhError, json.JSONDecodeError):
                pass

        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            log.warning(
                "Timed out waiting for %s review on PR #%d",
                bot_name,
                pr_number,
            )
            return False
        time.sleep(poll_interval)


def scrape_pr_comments(
    fork: str,
    pr_number: int,
    bot_name: str = "copilot",
) -> list[Comment]:
    """Scrape review comments from a PR, filtering to the named bot.

    Checks both PR review comments (inline) and issue comments (general).
    PR review comments have file/line; issue comments have body only.
    """
    comments: list[Comment] = []

    # 1. PR review comments (inline, have file + line)
    try:
        output = run_gh(
            "api",
            f"repos/{fork}/pulls/{pr_number}/comments",
        )
        for rc in json.loads(output):
            if not isinstance(rc, dict):
                continue
            user = rc.get("user") or {}
            login = str(user.get("login", "") if isinstance(user, dict) else "")
            if bot_name.lower() not in login.lower():
                continue
            comments.append(
                Comment(
                    file=str(rc.get("path", "")),
                    line=int(rc.get("line") or 0),  # type: ignore[arg-type]
                    body=str(rc.get("body", "")),
                )
            )
    except (GhError, json.JSONDecodeError) as exc:
        log.debug("Failed to fetch PR review comments for %s#%d: %s", fork, pr_number, exc)

    # 2. Issue comments (general, no file/line — used by CodeRabbit, Greptile)
    try:
        output = run_gh(
            "api",
            f"repos/{fork}/issues/{pr_number}/comments",
        )
        for rc in json.loads(output):
            if not isinstance(rc, dict):
                continue
            user = rc.get("user") or {}
            login = str(user.get("login", "") if isinstance(user, dict) else "")
            if bot_name.lower() not in login.lower():
                continue
            body = str(rc.get("body", ""))
            # Skip trigger comments (e.g. "@greptile", "@coderabbitai review")
            if body.strip().startswith("@"):
                continue
            comments.append(
                Comment(file="", line=0, body=body),
            )
    except (GhError, json.JSONDecodeError) as exc:
        log.debug("Failed to fetch issue comments for %s#%d: %s", fork, pr_number, exc)

    return comments


def close_eval_pr(
    fork: str,
    pr_number: int,
    head_branch: str,
    base_branch: str = "",
) -> None:
    """Close the eval PR and delete remote branches."""
    run_gh(
        "pr",
        "close",
        str(pr_number),
        "--repo",
        fork,
    )
    # Delete head branch
    try:
        run_gh(
            "api",
            "--method",
            "DELETE",
            f"repos/{fork}/git/refs/heads/{head_branch}",
        )
    except GhError:
        pass  # Branch may already be gone
    # Delete base branch if provided
    if base_branch:
        try:
            run_gh(
                "api",
                "--method",
                "DELETE",
                f"repos/{fork}/git/refs/heads/{base_branch}",
            )
        except GhError:
            pass
    log.info("Closed PR #%d, cleaned branches", pr_number)


def _get_patch_diff(case: TestCase, repo_dir: Path) -> str:
    """Get the diff for the introducing commit."""
    introducing = (case.truth.introducing_commit if case.truth else None) or case.base_commit
    if not introducing:
        return ""
    return run_git("diff", f"{introducing}~1", introducing, cwd=repo_dir)


def save_pr_transcript(
    transcript_dir: Path,
    case_id: str,
    tool_name: str,
    *,
    fork: str,
    branch: str,
    pr_number: int,
    scrubbed_title: str,
    scrubbed_body: str,
    raw_comments: list[dict[str, Any]],
    patch_diff: str,
    time_seconds: float,
) -> str:
    """Save a PR tool interaction transcript for audit."""
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"{case_id}-{tool_name}.json"
    data = {
        "pr_metadata": {
            "fork": fork,
            "branch": branch,
            "pr_number": pr_number,
        },
        "scrubbed_title": scrubbed_title,
        "scrubbed_body": scrubbed_body,
        "raw_comments": raw_comments,
        "patch_diff": patch_diff,
        "time_seconds": time_seconds,
    }
    path.write_text(json.dumps(data, indent=2, default=str))
    return str(path)


def get_default_branch(fork: str) -> str:
    """Get the default branch of a fork repo."""
    output = run_gh(
        "repo",
        "view",
        fork,
        "--json",
        "defaultBranchRef",
        "-q",
        ".defaultBranchRef.name",
    )
    return output.strip() or "main"
