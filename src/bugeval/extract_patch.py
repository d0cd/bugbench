"""CLI command: extract-patch — generate .patch files for test cases."""

from __future__ import annotations

from pathlib import Path

import click

from bugeval.git_utils import GitError, format_patch
from bugeval.io import load_all_cases, load_case
from bugeval.models import TestCase


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
def extract_patch(
    case_id: str | None,
    extract_all: bool,
    repo_dir: Path,
    cases_dir: Path,
    output_dir: Path,
    dry_run: bool,
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
