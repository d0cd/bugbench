"""CLI command: scrape-github — fetch bug-fix PRs from a GitHub repo."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click

from bugeval.github_scraper import (
    build_candidates,
    extract_expected_findings,
    fetch_bug_issues,
    fetch_fix_prs,
    fetch_pr_diff,
    filter_already_processed,
    load_scrape_state,
    save_scrape_state,
)
from bugeval.io import save_candidates
from bugeval.models import ScrapeState


@click.command("scrape-github")
@click.option("--repo", required=True, help="GitHub repo in owner/name format.")
@click.option("--limit", default=200, show_default=True, help="Max issues/PRs to fetch.")
@click.option(
    "--min-confidence",
    default=0.3,
    show_default=True,
    help="Minimum confidence threshold for candidates.",
)
@click.option(
    "--output-dir",
    default="candidates",
    show_default=True,
    help="Directory to write candidate YAML files.",
)
@click.option(
    "--labels",
    default="bug,fix,regression,defect",
    show_default=True,
    help="Comma-separated issue labels to search.",
)
@click.option(
    "--since",
    default=None,
    help="Only fetch issues/PRs after this ISO date (e.g. 2024-01-01).",
)
@click.option("--no-cache", is_flag=True, help="Ignore scrape state, re-process all PRs.")
@click.option("--dry-run", is_flag=True, help="Print to stdout without writing files.")
def scrape_github(
    repo: str,
    limit: int,
    min_confidence: float,
    output_dir: str,
    labels: str,
    since: str | None,
    no_cache: bool,
    dry_run: bool,
) -> None:
    """Scrape GitHub for bug-fix PRs and output ranked candidates YAML."""
    out_dir = Path(output_dir)
    repo_slug = repo.replace("/", "-")
    state_path = out_dir / f"{repo_slug}.state.yaml"
    candidates_path = out_dir / f"{repo_slug}.yaml"

    # 1. Load scrape state
    if no_cache:
        state = None
    else:
        try:
            state = load_scrape_state(state_path)
        except Exception:
            click.echo(
                f"Warning: could not read state from {state_path}, treating as --no-cache.",
                err=True,
            )
            state = None

    label_list = [lbl.strip() for lbl in labels.split(",") if lbl.strip()]

    # 2. Fetch issues and PRs
    click.echo(f"Fetching issues from {repo}...")
    issues = fetch_bug_issues(repo, limit=limit, labels=label_list, since=since)

    click.echo(f"Fetching merged PRs from {repo}...")
    prs = fetch_fix_prs(repo, limit=limit, since=since)

    # 3. Filter already-processed PRs
    new_prs = filter_already_processed(prs, state)
    click.echo(f"Found {len(issues)} issues, {len(new_prs)} new PRs.")

    # 4. Build candidates (links issues to PRs, scores)
    candidates = build_candidates(repo, issues, new_prs)

    # 5. Filter by min-confidence first
    filtered = [c for c in candidates if c.confidence >= min_confidence]
    filtered.sort(key=lambda c: c.confidence, reverse=True)

    # 6. Fetch diff hunks for top filtered candidates and populate expected_findings
    for candidate in filtered[:50]:
        try:
            diff_files = fetch_pr_diff(repo, candidate.pr_number)
            findings = extract_expected_findings(diff_files)
            candidate.expected_findings = findings
        except Exception:
            pass  # Non-fatal: best-effort diff fetching

    click.echo(f"{len(filtered)} candidates above confidence {min_confidence:.2f}.")

    if not filtered:
        click.echo("No candidates found.")
        return

    # 7. Output
    if dry_run:
        click.echo(f"\n--- Dry run: {len(filtered)} candidates for {repo} ---")
        for c in filtered:
            click.echo(f"  PR #{c.pr_number} | conf={c.confidence:.2f} | {c.title[:60]}")
        return

    # 8. Write candidates file
    out_dir.mkdir(parents=True, exist_ok=True)
    save_candidates(filtered, candidates_path)
    click.echo(f"Wrote {len(filtered)} candidates to {candidates_path}")

    # 9. Update scrape state
    processed_pr_numbers = [pr["number"] for pr in new_prs]  # type: ignore[index]
    if state is not None:
        all_processed = list(set(state.processed_pr_numbers) | set(processed_pr_numbers))
    else:
        all_processed = list(set(processed_pr_numbers))

    new_state = ScrapeState(
        repo=repo,
        last_scraped_at=datetime.now(),
        processed_pr_numbers=all_processed,
    )
    save_scrape_state(new_state, state_path)
    click.echo(f"Scrape state updated: {len(all_processed)} total PRs processed.")
