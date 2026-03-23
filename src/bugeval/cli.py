"""CLI entry point for bugeval."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from bugeval.models import TestCase


@click.group()
def cli() -> None:
    """Bug-finding evaluation framework."""


@cli.command()
@click.option("--repo", required=True, help="GitHub repo (e.g. ProvableHQ/snarkVM)")
@click.option("--limit", default=200, help="Max PRs/issues to scrape")
@click.option("--since", default="2023-01-01", help="Only PRs merged after this date")
@click.option("--output-dir", default="cases", help="Output directory for case YAMLs")
@click.option("--concurrency", default=1, help="Parallel workers")
@click.option(
    "--from-issues",
    is_flag=True,
    help="Mine from bug-labeled issues instead of PRs",
)
@click.option("--from-git", is_flag=True, help="Mine from local git history")
@click.option("--repo-dir", default="", help="Path to local repo clone")
def mine(
    repo: str,
    limit: int,
    since: str,
    output_dir: str,
    concurrency: int,
    from_issues: bool,
    from_git: bool,
    repo_dir: str,
) -> None:
    """Scrape fix PRs and build initial test cases."""
    from pathlib import Path

    if from_git:
        if not repo_dir:
            click.echo("Error: --repo-dir required with --from-git")
            raise SystemExit(1)
        from bugeval.mine import mine_from_git

        cases = mine_from_git(
            repo=repo,
            repo_dir=Path(repo_dir),
            since=since,
            output_dir=Path(output_dir),
        )
        click.echo(f"Mined {len(cases)} cases from local git in {repo}")
    elif from_issues:
        from bugeval.mine import mine_from_issues

        cases = mine_from_issues(
            repo=repo,
            limit=limit,
            since=since,
            output_dir=Path(output_dir),
        )
        click.echo(f"Mined {len(cases)} cases from bug issues in {repo}")
    else:
        from bugeval.mine import mine_repo

        cases = mine_repo(
            repo=repo,
            limit=limit,
            since=since,
            output_dir=Path(output_dir),
            concurrency=concurrency,
        )
        click.echo(f"Mined {len(cases)} cases from {repo}")


@cli.command()
@click.option("--cases-dir", required=True, help="Directory containing case YAMLs")
@click.option("--repo-dir", required=True, help="Path to local repo clone")
@click.option("--concurrency", default=1, help="Parallel workers")
def blame(cases_dir: str, repo_dir: str, concurrency: int) -> None:
    """Find introducing commits via git blame."""
    from pathlib import Path

    from bugeval.blame import blame_cases

    blame_cases(Path(cases_dir), Path(repo_dir), concurrency)


@cli.command("ground-truth")
@click.option("--cases-dir", required=True, help="Directory containing case YAMLs")
@click.option("--repo-dir", required=True, help="Path to local repo clone")
@click.option("--concurrency", default=1, help="Parallel workers")
def ground_truth(cases_dir: str, repo_dir: str, concurrency: int) -> None:
    """Build ground truth via diff intersection."""
    from pathlib import Path

    from bugeval.ground_truth import build_ground_truth

    build_ground_truth(Path(cases_dir), Path(repo_dir), concurrency)


@cli.command()
@click.option("--cases-dir", required=True, help="Directory containing case YAMLs")
@click.option("--repo-dir", default="", help="Path to local repo clone (for diffs)")
@click.option("--models", default="claude,gemini", help="Models for cross-validation")
@click.option("--concurrency", default=5, help="Parallel workers")
@click.option("--dry-run", is_flag=True, help="Validate without calling LLMs")
def validate(cases_dir: str, repo_dir: str, models: str, concurrency: int, dry_run: bool) -> None:
    """Cross-model validation of ground truth."""
    from pathlib import Path

    from bugeval.validate import validate_cases

    model_list = [m.strip() for m in models.split(",")]
    validate_cases(Path(cases_dir), Path(repo_dir), model_list, concurrency, dry_run)


@cli.command("clean-cases")
@click.option("--repo", required=True, help="GitHub repo")
@click.option("--count", default=50, help="Number of clean cases to generate")
@click.option("--cases-dir", default="cases", help="Output directory")
@click.option("--since", default="2023-01-01", help="Only PRs merged after this date")
def clean_cases(repo: str, count: int, cases_dir: str, since: str) -> None:
    """Generate negative control cases (clean PRs)."""
    from pathlib import Path

    from bugeval.clean_cases import mine_clean_cases

    cases = mine_clean_cases(repo, count, Path(cases_dir), since)
    click.echo(f"Generated {len(cases)} clean cases from {repo}")


@cli.command()
@click.option(
    "--tool",
    required=True,
    help="Tool: copilot, greptile, coderabbit, agent, agent-gemini,"
    " agent-openai, agent-cli-claude, agent-cli-gemini, agent-cli-codex,"
    " agent-sdk, agent-sdk-2pass",
)
@click.option("--cases-dir", default="cases", help="Test cases directory")
@click.option("--run-dir", required=True, help="Output directory for results")
@click.option(
    "--context",
    default="",
    help="Context level for agent (diff-only, diff+repo, diff+repo+domain)",
)
@click.option("--concurrency", default=1, help="Parallel workers")
@click.option("--timeout", default=300, help="Timeout per case in seconds")
@click.option("--dry-run", is_flag=True, help="Validate setup without running tools")
@click.option("--repo-dir", default="", help="Path to local repo clone")
@click.option(
    "--thinking-budget",
    default=0,
    type=int,
    help="Extended thinking budget tokens (0=disabled, agent only)",
)
@click.option(
    "--max-turns",
    default=30,
    type=int,
    show_default=True,
    help="Max agent turns (SDK/API runners)",
)
@click.option(
    "--model",
    default="",
    help="Model override for agent runners (e.g. claude-opus-4-6)",
)
@click.option(
    "--org",
    default="",
    help="GitHub org for PR tool forks (copilot, greptile, coderabbit)",
)
@click.option(
    "--docker",
    is_flag=True,
    help="Run agent in Docker container (allows Bash tool safely)",
)
@click.option(
    "--docker-image",
    default="bugeval-agent",
    help="Docker image name for --docker mode",
)
def evaluate(
    tool: str,
    cases_dir: str,
    run_dir: str,
    context: str,
    repo_dir: str,
    concurrency: int,
    timeout: int,
    dry_run: bool,
    thinking_budget: int,
    max_turns: int,
    model: str,
    org: str,
    docker: bool,
    docker_image: str,
) -> None:
    """Run a tool against test cases."""
    from pathlib import Path

    from bugeval.evaluate import evaluate_tool

    evaluate_tool(
        tool,
        Path(cases_dir),
        Path(run_dir),
        context,
        Path(repo_dir),
        concurrency,
        timeout,
        dry_run,
        thinking_budget=thinking_budget,
        max_turns=max_turns,
        model=model,
        org=org,
        docker=docker,
        docker_image=docker_image,
    )


@cli.command()
@click.option("--run-dir", required=True, help="Run directory with results")
@click.option("--cases-dir", default="cases", help="Test cases directory")
@click.option("--concurrency", default=1, help="Parallel LLM scoring (not yet used)")
@click.option("--dry-run", is_flag=True, help="Mechanical scoring only, skip LLM")
@click.option(
    "--judge-model",
    default="claude-haiku-4-5",
    help="Model for LLM judge",
)
@click.option(
    "--judge-models",
    default="",
    help="Comma-separated models for ensemble voting",
)
@click.option(
    "--backend",
    default="api",
    type=click.Choice(["api", "sdk"]),
    help="LLM backend: api (needs ANTHROPIC_API_KEY) or sdk (uses Claude Code)",
)
def score(
    run_dir: str,
    cases_dir: str,
    concurrency: int,
    dry_run: bool,
    judge_model: str,
    judge_models: str,
    backend: str,
) -> None:
    """Score tool results (mechanical + LLM quality)."""
    from pathlib import Path

    from bugeval.score import score_run

    models_list = (
        [m.strip() for m in judge_models.split(",") if m.strip()] if judge_models else None
    )
    # concurrency is accepted for future use but not yet implemented
    score_run(
        Path(run_dir),
        Path(cases_dir),
        dry_run,
        judge_model=judge_model,
        judge_models=models_list,
        backend=backend,
    )


@cli.command()
@click.option("--run-dir", required=True, help="Run directory with scores")
@click.option("--cases-dir", default="cases", help="Test cases directory")
@click.option("--no-charts", is_flag=True, help="Skip chart generation")
def analyze(run_dir: str, cases_dir: str, no_charts: bool) -> None:
    """Analyze scores and generate comparison report."""
    from pathlib import Path

    from bugeval.analyze import run_analysis

    run_analysis(Path(run_dir), Path(cases_dir), no_charts)


@cli.command("dashboard")
@click.option("--port", default=5000, show_default=True, help="Port to listen on")
@click.option(
    "--cases-dir",
    default="cases",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Directory containing case YAML files",
)
@click.option(
    "--results-dir",
    default="results",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Root directory for run outputs",
)
@click.option("--debug", is_flag=True, default=False, help="Enable Flask debug mode")
def dashboard(port: int, cases_dir: str, results_dir: str, debug: bool) -> None:
    """Launch the local review dashboard."""
    from pathlib import Path

    from bugeval.dashboard import create_app

    app = create_app(Path(cases_dir), Path(results_dir))
    click.echo(f"Dashboard -> http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=debug)


@cli.command("add-case")
@click.option(
    "--pr-url",
    required=True,
    help="GitHub PR URL (e.g. https://github.com/owner/repo/pull/123)",
)
@click.option("--cases-dir", default="cases", help="Output directory")
@click.option(
    "--repo-dir",
    default="",
    help="Local repo clone (for blame + ground truth)",
)
@click.option("--dry-run", is_flag=True, help="Show what would be added without writing")
def add_case(pr_url: str, cases_dir: str, repo_dir: str, dry_run: bool) -> None:
    """Manually add a test case from a fix PR URL."""
    from pathlib import Path

    from bugeval.add_case import add_case_from_pr

    result = add_case_from_pr(
        pr_url,
        Path(cases_dir),
        Path(repo_dir),
        dry_run=dry_run,
    )
    if result is None:
        click.echo("Skipped: duplicate or error.")
    elif dry_run:
        click.echo(f"[dry-run] Would add: {result.id} (PR #{result.fix_pr_number})")
    else:
        click.echo(f"Added: {result.id} (PR #{result.fix_pr_number})")


def _preflight_open_prs(
    cases_path: Path,
    repo_path: Path,
    org: str,
    tool: str,
    cases: list[TestCase],
) -> bool:
    """Run preflight checks before opening PRs.

    Returns True if all critical checks pass.
    Prints warnings for non-critical issues.
    """
    import random

    # 1. Cases exist
    if not cases:
        click.echo(f"Error: No active cases found in {cases_path}")
        raise SystemExit(1)

    # 2. repo_dir is a git repo
    if not repo_path.exists() or not (repo_path / ".git").exists():
        click.echo(f"Error: repo_dir {repo_path} is not a git repository")
        raise SystemExit(1)

    # 3. GitHub auth
    try:
        subprocess.run(
            ["gh", "auth", "status"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        click.echo("Error: GitHub auth failed. Run: gh auth login")
        raise SystemExit(1)

    # 4. Per-tool repo exists (warning, not error)
    repos_seen: set[str] = set()
    for case in cases:
        slug = case.repo.split("/", 1)[1]
        fork = f"{org}/{slug}-{tool}"
        if fork in repos_seen:
            continue
        repos_seen.add(fork)
        try:
            subprocess.run(
                ["gh", "repo", "view", fork],
                check=True,
                capture_output=True,
                text=True,
            )
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
        ):
            click.echo(f"Warning: Tool repo {fork} not found. It will be created automatically.")

    # 5. Sample SHA check (warning, not error)
    sample = random.sample(cases, min(3, len(cases)))
    for case in sample:
        slug = case.repo.split("/", 1)[1]
        clone_dir = repo_path.parent / f"{slug}-{tool}"
        if not clone_dir.exists():
            clone_dir = repo_path
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(clone_dir),
                    "cat-file",
                    "-e",
                    case.base_commit,
                ],
                check=True,
                capture_output=True,
            )
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
        ):
            click.echo(
                f"Warning: Commit {case.base_commit}"
                f" not found in {clone_dir}."
                f" Try: git -C {clone_dir}"
                f" fetch origin"
            )

    return True


@cli.command("open-prs")
@click.option(
    "--tool",
    required=True,
    help="PR tool: copilot, greptile, coderabbit",
)
@click.option("--cases-dir", default="cases", help="Test cases directory")
@click.option("--run-dir", required=True, help="Output directory for results")
@click.option("--repo-dir", required=True, help="Path to local repo clone")
@click.option("--org", required=True, help="GitHub org for tool repos")
@click.option(
    "--concurrency",
    default=3,
    show_default=True,
    help="Parallel PR creation",
)
def open_prs(
    tool: str,
    cases_dir: str,
    run_dir: str,
    repo_dir: str,
    org: str,
    concurrency: int,
) -> None:
    """Open PRs for all cases (phase 1 of two-phase PR eval)."""
    import logging
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from bugeval.copilot_runner import open_pr_for_case
    from bugeval.evaluate import ensure_per_tool_clone
    from bugeval.io import load_cases, load_result, save_result
    from bugeval.result_models import ToolResult

    logger = logging.getLogger(__name__)

    cases_path = Path(cases_dir)
    run_path = Path(run_dir)
    repo_path = Path(repo_dir)

    cases = load_cases(cases_path)

    _preflight_open_prs(
        cases_path,
        repo_path,
        org,
        tool,
        cases,
    )

    repo_path = ensure_per_tool_clone(tool, repo_path)
    results_dir = run_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Filter to pending cases
    # - pending-review / reviewed / closed → skip
    # - error result → delete and retry
    # - no file → proceed
    pending = []
    for case in cases:
        result_path = results_dir / f"{case.id}--{tool}.yaml"
        if result_path.exists():
            try:
                existing = load_result(result_path)
                if existing.pr_state in (
                    "pending-review",
                    "reviewed",
                    "closed",
                ):
                    continue
                # Error result: remove stale file so we retry
                if existing.error:
                    result_path.unlink(missing_ok=True)
            except Exception:
                # Corrupt file — remove and retry
                result_path.unlink(missing_ok=True)
        pending.append(case)

    total = len(pending)
    if total == 0:
        click.echo("All cases already have PRs open.")
        return

    completed = 0

    def _process(case: TestCase) -> ToolResult:
        return open_pr_for_case(case, repo_path, tool, org)

    def _safe_save(result: ToolResult, path: Path) -> bool:
        """Save result, but never overwrite a pending-review file."""
        if path.exists():
            try:
                existing = load_result(path)
                if existing.pr_state == "pending-review":
                    return False
            except Exception:
                pass
        save_result(result, path)
        return True

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_process, c): c for c in pending}
        for future in as_completed(futures):
            case = futures[future]
            try:
                result = future.result()
                fname = f"{case.id}--{tool}.yaml"
                saved = _safe_save(result, results_dir / fname)
                completed += 1
                if not saved:
                    click.echo(
                        f"Skipped save for {case.id}: pending-review exists ({completed}/{total})"
                    )
                elif result.pr_number:
                    click.echo(f"Opened PR #{result.pr_number} for {case.id} ({completed}/{total})")
                else:
                    click.echo(f"Error for {case.id}: {result.error} ({completed}/{total})")
            except Exception:
                logger.exception("Failed to open PR for %s", case.id)
                completed += 1

    click.echo(f"Done: {completed}/{total} cases processed")


@cli.command("scrape-prs")
@click.option("--run-dir", required=True, help="Run directory with pending results")
@click.option("--cases-dir", default="cases", help="Test cases directory")
@click.option("--org", required=True, help="GitHub org for tool repos")
@click.option(
    "--close/--no-close",
    default=True,
    help="Close PRs after scraping",
)
def scrape_prs(run_dir: str, cases_dir: str, org: str, close: bool) -> None:
    """Scrape reviews from pending PRs (phase 2 of two-phase PR eval)."""
    import logging
    from pathlib import Path

    from bugeval.copilot_runner import scrape_pr_for_case
    from bugeval.io import load_cases, load_result, save_result

    logger = logging.getLogger(__name__)

    results_dir = Path(run_dir) / "results"
    if not results_dir.exists():
        click.echo("Scraped: 0 reviewed, 0 still pending")
        return

    cases = load_cases(Path(cases_dir))
    case_map = {c.id: c for c in cases}

    reviewed = 0
    pending = 0
    errors = 0

    for p in sorted(results_dir.glob("*.yaml")):
        try:
            result = load_result(p)
        except Exception:
            logger.exception("Failed to load %s", p)
            errors += 1
            continue

        if result.pr_state != "pending-review":
            continue

        case = case_map.get(result.case_id)
        if not case:
            logger.warning("No case found for %s, skipping", result.case_id)
            errors += 1
            continue

        repo_slug = case.repo.split("/", 1)[1]
        fork = f"{org}/{repo_slug}-{result.tool}"

        try:
            updated = scrape_pr_for_case(result, fork, close=close)
        except Exception:
            logger.exception("Failed to scrape PR for %s", result.case_id)
            errors += 1
            continue

        if updated.pr_state != "pending-review":
            save_result(updated, p)
            reviewed += 1
        else:
            pending += 1

    parts = [f"{reviewed} reviewed", f"{pending} still pending"]
    if errors:
        parts.append(f"{errors} errors")
    click.echo(f"Scraped: {', '.join(parts)}")


@cli.command("cleanup-prs")
@click.option("--org", required=True, help="GitHub org")
@click.option("--tool", default="", help="Specific tool (default: all PR tools)")
@click.option("--dry-run", is_flag=True, help="Show what would be cleaned without acting")
def cleanup_prs(org: str, tool: str, dry_run: bool) -> None:
    """Clean up orphaned PRs and stale branches on tool repos."""
    import json
    import logging
    import re

    from bugeval.copilot_runner import (
        _delete_remote_branch,
        close_eval_pr,
    )
    from bugeval.mine import run_gh

    logger = logging.getLogger(__name__)

    pr_tools = [tool] if tool else ["copilot", "greptile", "coderabbit"]

    for t in pr_tools:
        fork = f"{org}/leo-{t}"

        # 1. List and close open PRs
        try:
            raw = run_gh(
                "pr",
                "list",
                "--repo",
                fork,
                "--state",
                "open",
                "--json",
                "number,headRefName,baseRefName",
            )
            prs = json.loads(raw) if raw.strip() else []
        except Exception:
            logger.warning("Failed to list PRs on %s", fork)
            prs = []

        closed = 0
        for pr in prs:
            pr_num = pr["number"]
            head = pr["headRefName"]
            base = pr.get("baseRefName", "")
            if dry_run:
                click.echo(f"[dry-run] Would close PR #{pr_num} on {fork}")
            else:
                try:
                    close_eval_pr(fork, pr_num, head, base)
                    closed += 1
                except Exception:
                    logger.warning(
                        "Failed to close PR #%d on %s",
                        pr_num,
                        fork,
                    )

        # 2. Find and delete stale branches
        try:
            refs_raw = run_gh(
                "api",
                f"repos/{fork}/git/refs/heads",
                "--jq",
                ".[].ref",
            )
            refs = [r.strip() for r in refs_raw.splitlines() if r.strip()]
        except Exception:
            logger.warning(
                "Failed to list branches on %s",
                fork,
            )
            refs = []

        # Branches with open PRs (already handled above)
        pr_branches = set()
        for pr in prs:
            pr_branches.add(pr["headRefName"])
            if pr.get("baseRefName"):
                pr_branches.add(pr["baseRefName"])

        stale_pattern = re.compile(r"^refs/heads/(base-|review-)")
        stale = []
        for ref in refs:
            if not stale_pattern.match(ref):
                continue
            branch = ref.removeprefix("refs/heads/")
            if branch not in pr_branches:
                stale.append(branch)

        deleted = 0
        for branch in stale:
            if dry_run:
                click.echo(f"[dry-run] Would delete branch {branch} on {fork}")
            else:
                try:
                    _delete_remote_branch(fork, branch)
                    deleted += 1
                except Exception:
                    logger.warning(
                        "Failed to delete branch %s on %s",
                        branch,
                        fork,
                    )

        if dry_run:
            click.echo(f"{fork}: {len(prs)} open PRs, {len(stale)} stale branches (dry-run)")
        else:
            click.echo(f"Closed {closed} PRs, deleted {deleted} branches on {fork}")


# Import and register curate command
from bugeval.curate import curate  # noqa: E402

cli.add_command(curate)
