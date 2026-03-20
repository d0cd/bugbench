"""manage-fresh-repos CLI: fresh repo lifecycle for PR tools without git history."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from uuid import uuid4

import click

from bugeval.git_utils import clone_repo_local, run_git
from bugeval.github_scraper import GhError, run_gh
from bugeval.pr_eval_models import EvalConfig, load_eval_config


def fresh_repo_name(repo: str, tool: str) -> str:
    """Compute the fresh repo name for a repo+tool pair.

    Example: 'ProvableHQ/leo', 'github-copilot' -> 'leo-github-copilot'
    """
    repo_short = repo.split("/")[-1]
    return f"{repo_short}-{tool}"


def _create_fresh_repo(eval_org: str, repo: str, tool: str, dry_run: bool) -> None:
    name = fresh_repo_name(repo, tool)
    full = f"{eval_org}/{name}"
    if dry_run:
        click.echo(f"[dry-run] gh repo create {full} --public")
        return
    run_gh("repo", "create", full, "--public")
    click.echo(f"Created fresh repo: {full}")


def _delete_fresh_repo(eval_org: str, repo: str, tool: str, dry_run: bool) -> None:
    name = fresh_repo_name(repo, tool)
    full = f"{eval_org}/{name}"
    if dry_run:
        click.echo(f"[dry-run] gh repo delete {full} --yes")
        return
    run_gh("repo", "delete", full, "--yes")
    click.echo(f"Deleted: {full}")


def _verify_fresh_repo(eval_org: str, repo: str, tool: str) -> None:
    name = fresh_repo_name(repo, tool)
    full = f"{eval_org}/{name}"
    try:
        run_gh("api", f"repos/{full}")
        click.echo(f"OK: {full} exists")
    except GhError:
        click.echo(f"MISSING: {full}", err=True)


def _push_branch(remote_url: str, branch: str, cwd: Path) -> None:
    """Push a branch to a remote, tolerating 'already exists' on retry."""
    from bugeval.git_utils import GitError

    try:
        run_git("push", remote_url, f"{branch}:{branch}", cwd=cwd)
    except GitError as exc:
        if "already exists" in exc.stderr or "non-fast-forward" in exc.stderr:
            # Branch from a prior run — force-push to overwrite.
            run_git("push", "--force", remote_url, f"{branch}:{branch}", cwd=cwd)
        else:
            raise


def push_case_branches(
    upstream_cache: Path,
    base_commit: str,
    patch_path: Path,
    case_id: str,
    tool_name: str,
    fresh_repo_url: str,
    tmp_parent: Path,
) -> tuple[str, str]:
    """Push orphan base branch + patch branch to fresh repo in a single throwaway clone.

    Returns (base_branch, patch_branch) names.
    """
    from bugeval.pr_lifecycle import make_branch_name

    base_branch = f"bugeval-base/{case_id}"
    patch_branch = make_branch_name(case_id, tool_name)
    tmp_dir = tmp_parent / f"bugeval-fresh-{uuid4().hex[:8]}"

    try:
        clone_repo_local(upstream_cache, tmp_dir)

        # Create orphan commit at base_commit (no parents, full tree)
        run_git("checkout", base_commit, cwd=tmp_dir)
        run_git("checkout", "--orphan", base_branch, cwd=tmp_dir)
        run_git("add", "-A", cwd=tmp_dir)
        run_git(
            "commit", "--message", f"bugeval: base state for {case_id}",
            "--allow-empty", cwd=tmp_dir,
        )
        _push_branch(fresh_repo_url, base_branch, tmp_dir)

        # Create patch branch on top of the orphan base
        run_git("checkout", "-b", patch_branch, cwd=tmp_dir)
        run_git("apply", str(patch_path.resolve()), cwd=tmp_dir)
        run_git("add", "-A", cwd=tmp_dir)
        run_git(
            "commit", "--message", f"bugeval: apply patch for {case_id}",
            "--allow-empty", cwd=tmp_dir,
        )
        _push_branch(fresh_repo_url, patch_branch, tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return base_branch, patch_branch


def clean_branches(eval_org: str, repo: str, tool: str, dry_run: bool) -> None:
    """Delete all bugeval-* branches from a fresh repo."""
    name = fresh_repo_name(repo, tool)
    full = f"{eval_org}/{name}"
    try:
        output = run_gh(
            "api", f"repos/{full}/git/refs",
            "--jq", '.[].ref',
        )
    except GhError:
        click.echo(f"Could not list refs for {full}", err=True)
        return

    for ref in output.strip().splitlines():
        ref = ref.strip()
        if not ref:
            continue
        # refs/heads/bugeval-base/... or refs/heads/bugeval/...
        branch = ref.removeprefix("refs/heads/")
        if not branch.startswith("bugeval"):
            continue
        if dry_run:
            click.echo(f"[dry-run] delete branch {branch} from {full}")
        else:
            try:
                run_gh("api", "--method", "DELETE", f"repos/{full}/git/{ref}")
                click.echo(f"Deleted branch {branch} from {full}")
            except GhError as exc:
                click.echo(f"Failed to delete {branch} from {full}: {exc}", err=True)


@click.command("manage-fresh-repos")
@click.option(
    "--config",
    "config_path",
    default="config/config.yaml",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to config.yaml",
)
@click.option(
    "--action",
    type=click.Choice(["create", "verify", "cleanup", "clean-branches"]),
    required=True,
    help="Fresh repo lifecycle action",
)
@click.option("--tools", "tools_filter", default=None, help="Comma-separated tool names to include")
@click.option("--dry-run", is_flag=True, default=False, help="Print commands without executing")
def manage_fresh_repos(
    config_path: str, action: str, tools_filter: str | None, dry_run: bool
) -> None:
    """Manage fresh GitHub repos (no history) for PR-mode evaluation tools."""
    config: EvalConfig = load_eval_config(Path(config_path))

    if not config.eval_org:
        click.echo("Error: github.eval_org is not set in config.yaml", err=True)
        sys.exit(1)

    if not config.repos:
        click.echo("Error: no repos configured in config.yaml", err=True)
        sys.exit(1)

    pr_tools = [t for t in config.pr_tools if t.fresh_repo]
    if tools_filter:
        names = {n.strip() for n in tools_filter.split(",")}
        pr_tools = [t for t in pr_tools if t.name in names]
        if not pr_tools:
            click.echo(f"Error: no fresh_repo PR tool matched: {tools_filter}", err=True)
            sys.exit(1)

    for tool in pr_tools:
        for repo in config.repos.values():
            if action == "create":
                _create_fresh_repo(config.eval_org, repo, tool.name, dry_run)
            elif action == "verify":
                _verify_fresh_repo(config.eval_org, repo, tool.name)
            elif action == "cleanup":
                _delete_fresh_repo(config.eval_org, repo, tool.name, dry_run)
            elif action == "clean-branches":
                clean_branches(config.eval_org, repo, tool.name, dry_run)
