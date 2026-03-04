"""manage-forks CLI: fork lifecycle management via gh CLI."""

from __future__ import annotations

import sys

import click

from bugeval.github_scraper import GhError, run_gh
from bugeval.pr_eval_models import EvalConfig, load_eval_config


def fork_name(repo: str, tool: str) -> str:
    """Compute the fork name for a repo+tool pair.

    Example: 'provable-org/aleo-lang', 'coderabbit' -> 'aleo-lang-coderabbit'
    """
    repo_short = repo.split("/")[-1]
    return f"{repo_short}-{tool}"


def _create_fork(eval_org: str, repo: str, tool: str, dry_run: bool) -> None:
    """Fork repo into eval_org with a tool-specific name."""
    name = fork_name(repo, tool)
    if dry_run:
        click.echo(
            f"[dry-run] gh repo fork {repo} --org {eval_org} --fork-name {name} --clone=false"
        )
        return
    run_gh("repo", "fork", repo, "--org", eval_org, "--fork-name", name, "--clone=false")
    click.echo(f"Created fork: {eval_org}/{name}")


def _verify_fork(eval_org: str, repo: str, tool: str) -> None:
    """Verify fork exists in eval_org."""
    name = fork_name(repo, tool)
    fork_repo = f"{eval_org}/{name}"
    try:
        run_gh("api", f"repos/{fork_repo}")
        click.echo(f"OK: {fork_repo} exists")
    except GhError:
        click.echo(f"MISSING: {fork_repo}", err=True)


def _sync_fork(eval_org: str, repo: str, tool: str, dry_run: bool) -> None:
    """Sync fork with upstream."""
    name = fork_name(repo, tool)
    fork_repo = f"{eval_org}/{name}"
    if dry_run:
        click.echo(f"[dry-run] gh repo sync {fork_repo}")
        return
    run_gh("repo", "sync", fork_repo)
    click.echo(f"Synced: {fork_repo}")


def _delete_fork(eval_org: str, repo: str, tool: str, dry_run: bool) -> None:
    """Delete fork from eval_org."""
    name = fork_name(repo, tool)
    fork_repo = f"{eval_org}/{name}"
    if dry_run:
        click.echo(f"[dry-run] gh repo delete {fork_repo} --yes")
        return
    run_gh("repo", "delete", fork_repo, "--yes")
    click.echo(f"Deleted: {fork_repo}")


@click.command("manage-forks")
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
    type=click.Choice(["create", "verify", "sync", "cleanup"]),
    required=True,
    help="Fork lifecycle action to perform",
)
@click.option("--tool", "tool_filter", default=None, help="Only operate on this tool (by name)")
@click.option("--dry-run", is_flag=True, default=False, help="Print commands without executing")
def manage_forks(config_path: str, action: str, tool_filter: str | None, dry_run: bool) -> None:
    """Manage GitHub forks for PR-mode evaluation tools."""
    from pathlib import Path

    config: EvalConfig = load_eval_config(Path(config_path))

    if not config.eval_org:
        click.echo("Error: github.eval_org is not set in config.yaml", err=True)
        sys.exit(1)

    if not config.repos:
        click.echo("Error: no repos configured in config.yaml", err=True)
        sys.exit(1)

    pr_tools = config.pr_tools
    if tool_filter:
        pr_tools = [t for t in pr_tools if t.name == tool_filter]
        if not pr_tools:
            click.echo(f"Error: no PR tool named '{tool_filter}'", err=True)
            sys.exit(1)

    for tool in pr_tools:
        for repo in config.repos.values():
            if action == "create":
                _create_fork(config.eval_org, repo, tool.name, dry_run)
            elif action == "verify":
                _verify_fork(config.eval_org, repo, tool.name)
            elif action == "sync":
                _sync_fork(config.eval_org, repo, tool.name, dry_run)
            elif action == "cleanup":
                _delete_fork(config.eval_org, repo, tool.name, dry_run)
