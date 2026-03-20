"""CLI command: extract-patch — generate .patch files for test cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from bugeval.git_utils import GitError, format_patch
from bugeval.github_scraper import GhError, run_gh
from bugeval.io import load_all_cases, load_case, save_case
from bugeval.models import TestCase


def lookup_pr_for_commit(repo: str, commit_sha: str) -> tuple[int | None, str | None]:
    """Look up PR number and base SHA for a commit via gh API.

    Returns (pr_number, pr_base_sha) or (None, None) if not found.
    """
    try:
        raw = run_gh("api", f"repos/{repo}/commits/{commit_sha}/pulls")
        data: list[dict[str, Any]] = json.loads(raw)
    except (GhError, json.JSONDecodeError, ValueError):
        return None, None

    if not data:
        return None, None

    first = data[0]
    pr_number: int | None = first.get("number")
    base_sha: str | None = (first.get("base") or {}).get("sha")
    return pr_number, base_sha


def _lookup_pr_base_sha(repo: str, pr_number: int) -> str | None:
    """Fetch the base SHA for a known PR number."""
    try:
        raw = run_gh("api", f"repos/{repo}/pulls/{pr_number}")
        data: dict[str, Any] = json.loads(raw)
        return (data.get("base") or {}).get("sha")
    except (GhError, json.JSONDecodeError, ValueError):
        return None


def _enrich_case(case: TestCase, case_path: Path, dry_run: bool) -> bool:
    """Enrich a case with PR number and base_commit. Returns True if changed."""
    if case.pr_number is not None:
        return False

    pr_number, base_sha = lookup_pr_for_commit(case.repo, case.fix_commit)
    if pr_number is None or base_sha is None:
        click.echo(f"[enrich-skip] {case.id}: no PR found")
        return False

    if dry_run:
        click.echo(f"[enrich-dry-run] {case.id}: PR #{pr_number}, base={base_sha[:12]}")
        return False

    updated = case.model_copy(update={"pr_number": pr_number, "base_commit": base_sha})
    save_case(updated, case_path)
    click.echo(f"[enriched] {case.id}: PR #{pr_number}")
    return True


def _write_patch(case: TestCase, repo_dir: Path, output_dir: Path) -> Path:
    """Generate and write a patch file for a single case. Returns the patch path."""
    patch_content = format_patch(case.base_commit, case.head_commit, repo_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    patch_path = output_dir / f"{case.id}.patch"
    patch_path.write_text(patch_content)
    return patch_path


@click.command("extract-patch")
@click.option("--case", "case_id", default=None, help="Extract patch for a single case by ID.")
@click.option("--all", "extract_all", is_flag=True, help="Extract patches for all cases.")
@click.option(
    "--repo-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the git repository checkout.",
)
@click.option(
    "--cases-dir",
    default="cases",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory containing test case YAML files.",
)
@click.option(
    "--output-dir",
    default="patches",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write .patch files.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print patches that would be generated without writing.",
)
@click.option(
    "--enrich",
    is_flag=True,
    default=False,
    help="Look up PR metadata (pr_number, base_commit) from GitHub before extracting.",
)
def extract_patch(
    case_id: str | None,
    extract_all: bool,
    repo_dir: Path,
    cases_dir: Path,
    output_dir: Path,
    dry_run: bool,
    enrich: bool,
) -> None:
    """Generate .patch files from base_commit to head_commit for test cases."""
    if not case_id and not extract_all:
        raise click.UsageError("Specify --case CASE_ID or --all.")

    if case_id:
        case_path = cases_dir / f"{case_id}.yaml"
        if not case_path.exists():
            click.echo(f"Case not found: {case_path}", err=True)
            raise SystemExit(1)
        case = load_case(case_path)
        if enrich:
            _enrich_case(case, case_path, dry_run)
            case = load_case(case_path)
        patch_path = output_dir / f"{case.id}.patch"
        if dry_run:
            click.echo(f"Would write {patch_path}")
            return
        written = _write_patch(case, repo_dir, output_dir)
        click.echo(f"Wrote {written}")
        return

    # --all
    cases = load_all_cases(cases_dir)
    if not cases:
        click.echo("No cases found.")
        return

    if enrich:
        enriched = 0
        for case in cases:
            case_path = _find_case_file(cases_dir, case.id)
            if case_path and _enrich_case(case, case_path, dry_run):
                enriched += 1
        if enriched:
            click.echo(f"Enriched {enriched} cases.")
            cases = load_all_cases(cases_dir)

    written_count = 0
    skipped_count = 0
    for case in cases:
        patch_path = output_dir / f"{case.id}.patch"
        if dry_run:
            click.echo(f"Would write {patch_path}")
        else:
            try:
                written = _write_patch(case, repo_dir, output_dir)
                click.echo(f"Wrote {written}")
                written_count += 1
            except GitError as exc:
                click.echo(f"SKIP {case.id}: {exc}", err=True)
                skipped_count += 1

    if dry_run:
        click.echo(f"Would extract {len(cases)} patches to {output_dir}/")
    else:
        summary = f"Extracted {written_count} patches to {output_dir}/"
        if skipped_count:
            summary += f" ({skipped_count} skipped — commit not in repo)"
        click.echo(summary)


def _find_case_file(cases_dir: Path, case_id: str) -> Path | None:
    """Find the YAML file for a given case id within cases_dir."""
    for path in cases_dir.rglob(f"{case_id}.yaml"):
        return path
    return None
