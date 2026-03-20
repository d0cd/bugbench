"""CLI command: scrape-reviewer-comments — fetch human reviewer comments from GitHub PRs."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import click

from bugeval.github_scraper import (
    _batch_fetch_pr_reviews_graphql,
    _parse_reviewer_findings,
    extract_reviewer_bug_signals,
)
from bugeval.io import load_case, save_case
from bugeval.models import TestCase

_CHECKPOINT_FILE = ".scrape_reviews_checkpoint.json"


def _load_checkpoint(checkpoint_path: Path) -> set[str]:
    if checkpoint_path.exists():
        try:
            return set(json.loads(checkpoint_path.read_text()))
        except Exception:
            pass
    return set()


def _save_checkpoint(checkpoint_path: Path, done: set[str]) -> None:
    checkpoint_path.write_text(json.dumps(sorted(done)))


@click.command("scrape-reviewer-comments")
@click.option(
    "--cases-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory containing test case YAML files.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print what would be done without modifying files.",
)
@click.option(
    "--limit",
    default=0,
    type=int,
    show_default=True,
    help="Max cases to process (0 = unlimited).",
)
@click.option(
    "--fail-after",
    default=10,
    type=int,
    show_default=True,
    help="Abort after N consecutive failures.",
)
@click.option(
    "--max-concurrent",
    default=1,
    type=int,
    show_default=True,
    help="Number of concurrent GraphQL batches (reserved for future use).",
)
@click.option(
    "--skip-existing",
    is_flag=True,
    default=False,
    help="Skip cases that already have non-empty reviewer_notes or reviewer_findings.",
)
def scrape_reviewer_comments(
    cases_dir: Path,
    dry_run: bool,
    limit: int,
    fail_after: int,
    max_concurrent: int,
    skip_existing: bool,
) -> None:
    """Fetch human reviewer comments from original GitHub PRs into test cases."""
    checkpoint_path = cases_dir / _CHECKPOINT_FILE
    done_ids: set[str] = _load_checkpoint(checkpoint_path)
    if done_ids:
        click.echo(f"Resuming: {len(done_ids)} already processed (from checkpoint).")

    yaml_files = sorted(cases_dir.rglob("*.yaml"))
    case_files = [f for f in yaml_files if not f.name.startswith(".")]

    # Load cases and group by repo
    cases_by_repo: dict[str, list[tuple[Path, TestCase]]] = defaultdict(list)
    skipped = 0
    for case_path in case_files:
        case = load_case(case_path)
        if case.id in done_ids:
            continue
        if case.pr_number is None:
            skipped += 1
            continue
        if skip_existing and (case.reviewer_notes or case.reviewer_findings):
            done_ids.add(case.id)
            skipped += 1
            continue
        cases_by_repo[case.repo].append((case_path, case))

    # Flatten for limit, preserving repo grouping
    all_cases: list[tuple[Path, TestCase]] = []
    for repo_cases in cases_by_repo.values():
        all_cases.extend(repo_cases)
    if limit > 0:
        all_cases = all_cases[:limit]

    # Re-group after limit
    grouped: dict[str, list[tuple[Path, TestCase]]] = defaultdict(list)
    for case_path, case in all_cases:
        grouped[case.repo].append((case_path, case))

    total_scraped = 0
    total_with_comments = 0
    total_with_findings = 0
    consecutive_failures = 0
    aborted = False

    for repo, repo_cases in grouped.items():
        if aborted:
            break
        owner, name = repo.split("/", 1)

        # Process in batches of 25
        batch_size = 25
        for batch_start in range(0, len(repo_cases), batch_size):
            if aborted:
                break
            batch = repo_cases[batch_start : batch_start + batch_size]
            pr_numbers = [case.pr_number for _, case in batch if case.pr_number is not None]

            try:
                reviews_map = _batch_fetch_pr_reviews_graphql(owner, name, pr_numbers)
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += len(batch)
                click.echo(f"[error] batch for {repo}: {exc}", err=True)
                if consecutive_failures >= fail_after:
                    click.echo(f"Aborting: {consecutive_failures} consecutive failures.", err=True)
                    aborted = True
                # Mark batch as done even on failure
                for _, case in batch:
                    done_ids.add(case.id)
                if not dry_run:
                    _save_checkpoint(checkpoint_path, done_ids)
                continue

            for case_path, case in batch:
                assert case.pr_number is not None
                reviews = reviews_map.get(case.pr_number, [])
                _signals, notes = extract_reviewer_bug_signals(reviews)
                findings = _parse_reviewer_findings(reviews)

                total_scraped += 1
                if notes:
                    total_with_comments += 1
                if findings:
                    total_with_findings += 1

                if dry_run:
                    click.echo(f"[dry-run] {case.id}: {len(notes)} notes, {len(findings)} findings")
                else:
                    updated = case.model_copy(
                        update={
                            "reviewer_notes": notes,
                            "reviewer_findings": findings,
                        }
                    )
                    save_case(updated, case_path)

                done_ids.add(case.id)

            if not dry_run:
                _save_checkpoint(checkpoint_path, done_ids)

    if not dry_run:
        _save_checkpoint(checkpoint_path, done_ids)

    click.echo(
        f"Done. Scraped {total_scraped} cases, "
        f"{total_with_comments} had reviewer comments, "
        f"{total_with_findings} had inline findings."
    )
    if skipped:
        click.echo(f"Skipped {skipped} cases (no PR number or already had data).")
