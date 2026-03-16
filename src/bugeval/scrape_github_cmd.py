"""CLI command: scrape-github — fetch bug-fix PRs from a GitHub repo."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from bugeval.github_scraper import (
    GhError,
    build_candidates,
    build_labeled_pr_candidates,
    compute_pr_size,
    detect_language,
    enrich_with_reviews,
    extract_expected_findings,
    fetch_bug_issues,
    fetch_fix_prs,
    fetch_pr_diff,
    fetch_prs_by_label,
    filter_already_processed,
    load_scrape_state,
    run_gh,
    save_scrape_state,
)
from bugeval.io import save_candidates
from bugeval.models import Candidate, CaseStats, ScrapeState

_EVAL_FORK_MAP = {
    # evaluation_fork_repo: original_repo
    "ai-code-review-evaluation/sentry-greptile": "getsentry/sentry",
    "ai-code-review-evaluation/grafana-greptile": "grafana/grafana",
    "ai-code-review-evaluation/discourse-greptile": "discourse/discourse",
    "ai-code-review-evaluation/keycloak-greptile": "keycloak/keycloak",
    "ai-code-review-evaluation/cal.com-greptile": "calcom/cal.com",
}


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
@click.option(
    "--mine-reviews",
    is_flag=True,
    default=False,
    help="Fetch reviewer comments for each candidate (slower, richer signal).",
)
@click.option(
    "--fetch-by-label",
    is_flag=True,
    default=False,
    help="Also fetch merged PRs by bug labels, merging with issue-linked candidates.",
)
def scrape_github(
    repo: str,
    limit: int,
    min_confidence: float,
    output_dir: str,
    labels: str,
    since: str | None,
    no_cache: bool,
    dry_run: bool,
    mine_reviews: bool,
    fetch_by_label: bool,
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

    if fetch_by_label:
        click.echo("Fetching PRs by bug labels...")
        labeled_prs = fetch_prs_by_label(repo, since=since)
        existing_pr_nums = {c.pr_number for c in candidates}
        labeled_candidates = build_labeled_pr_candidates(repo, labeled_prs, existing_pr_nums)
        click.echo(f"  Found {len(labeled_candidates)} additional label-based candidates")
        candidates.extend(labeled_candidates)

    # 5. Filter by min-confidence first
    filtered = [c for c in candidates if c.confidence >= min_confidence]
    filtered.sort(key=lambda c: c.confidence, reverse=True)

    # 5b. Enrich top candidates with reviewer comments (post-filter: fewer API calls)
    if mine_reviews:
        click.echo(f"Fetching reviewer comments for top {min(50, len(filtered))} candidates...")
        filtered = enrich_with_reviews(repo, filtered, top_n=50)
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
            rev_flag = " [reviewer]" if c.reviewer_notes else ""
            click.echo(f"  PR #{c.pr_number} | conf={c.confidence:.2f}{rev_flag} | {c.title[:60]}")
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


@click.command("scrape-benchmark")
@click.option(
    "--output-dir",
    default="candidates",
    show_default=True,
    help="Directory to write candidate YAML.",
)
@click.option("--dry-run", is_flag=True)
def scrape_benchmark(output_dir: str, dry_run: bool) -> None:
    """Scrape the exact 50 bugs from the Greptile AI code review benchmark.

    Fetches PRs from the ai-code-review-evaluation GitHub org forks,
    links each back to its original repo commit via title search.
    """
    all_candidates: list[Candidate] = []

    for fork_repo, original_repo in _EVAL_FORK_MAP.items():
        click.echo(f"Fetching benchmark bugs from {fork_repo}...")
        try:
            args: list[str] = [
                "pr",
                "list",
                "--repo",
                fork_repo,
                "--state",
                "all",
                "--json",
                "number,title,body,additions,deletions,files,mergeCommit,labels",
                "--limit",
                "12",
                "--search",
                "created:>2025-01-01 -author:app/dependabot",
            ]
            output = run_gh(*args)
            fork_prs: list[dict[str, Any]] = json.loads(output)
        except GhError as e:
            click.echo(f"  Error: {e}", err=True)
            continue

        # Filter to likely benchmark PRs (not Bump/sync/chore deps)
        bug_prs = [
            pr
            for pr in fork_prs
            if not any(
                kw in (pr.get("title") or "").lower()
                for kw in ("bump", "chore(deps", "sync", "dependabot")
            )
        ][:10]  # at most 10 bugs per repo

        click.echo(f"  Found {len(bug_prs)} benchmark bug PRs")

        for pr in bug_prs:
            title = str(pr.get("title") or "")
            additions = int(pr.get("additions") or 0)
            deletions = int(pr.get("deletions") or 0)
            pr_files: list[dict[str, Any]] = pr.get("files") or []
            file_names = [str(f.get("path", "")) for f in pr_files]

            # Try to find the original PR in the source repo by title
            fix_commit = ""
            try:
                search_args: list[str] = [
                    "pr",
                    "list",
                    "--repo",
                    original_repo,
                    "--state",
                    "merged",
                    "--json",
                    "number,mergeCommit",
                    "--search",
                    title[:60],
                    "--limit",
                    "3",
                ]
                search_out = run_gh(*search_args)
                matches: list[dict[str, Any]] = json.loads(search_out)
                if matches:
                    fix_commit = str((matches[0].get("mergeCommit") or {}).get("oid", ""))
            except (GhError, json.JSONDecodeError):
                pass

            all_candidates.append(
                Candidate(
                    repo=original_repo,
                    pr_number=int(pr["number"]),
                    fix_commit=fix_commit or str((pr.get("mergeCommit") or {}).get("oid", "")),
                    base_commit=None,
                    head_commit=None,
                    confidence=0.9,  # hand-curated benchmark bugs = very high confidence
                    signals=["greptile_benchmark", "labeled_bug"],
                    title=title,
                    body=str(pr.get("body") or ""),
                    labels=[str(lbl.get("name", "")) for lbl in (pr.get("labels") or [])],
                    files_changed=file_names,
                    diff_stats=CaseStats(
                        lines_added=additions,
                        lines_deleted=deletions,
                        files_changed=len(file_names),
                        hunks=0,
                    ),
                    expected_findings=[],
                    language=detect_language(file_names),
                    pr_size=compute_pr_size(additions, deletions),
                )
            )

    click.echo(f"\nTotal benchmark candidates: {len(all_candidates)}")

    if dry_run:
        for c in all_candidates:
            click.echo(f"  {c.repo} PR#{c.pr_number} conf={c.confidence} | {c.title[:60]}")
        return

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "greptile-benchmark.yaml"
    save_candidates(all_candidates, out_path)
    click.echo(f"Wrote {len(all_candidates)} candidates → {out_path}")
