"""CLI command: merge-cases — merge sharded case dirs into one output directory."""

from __future__ import annotations

import glob as glob_module
from collections import defaultdict
from pathlib import Path

import click

from bugeval.io import load_case, save_case
from bugeval.models import TestCase


def _expand_dirs(patterns: tuple[str, ...]) -> list[Path]:
    """Expand glob patterns to a sorted, deduplicated list of existing directories."""
    seen: set[Path] = set()
    dirs: list[Path] = []
    for pattern in sorted(patterns):
        for match in sorted(glob_module.glob(pattern)):
            p = Path(match).resolve()
            if p.is_dir() and p not in seen:
                seen.add(p)
                dirs.append(p)
    return dirs


def _load_cases_from_dir(directory: Path) -> list[TestCase]:
    """Load all case YAML files from a directory (skip checkpoint files)."""
    cases: list[TestCase] = []
    for f in sorted(directory.glob("*.yaml")):
        if "checkpoint" in f.name:
            continue
        try:
            cases.append(load_case(f))
        except Exception as e:
            click.echo(f"  WARN: skipping {f.name}: {e}", err=True)
    return cases


def _max_index(repo: str, output_dir: Path) -> int:
    """Return the highest existing sequential index for a repo in output_dir."""
    repo_short = repo.split("/")[-1][:15].replace(".", "-")
    prefix = f"{repo_short}-"
    max_index = 0
    for f in output_dir.glob(f"{prefix}*.yaml"):
        try:
            max_index = max(max_index, int(f.stem[len(prefix):]))
        except ValueError:
            pass
    return max_index


@click.command("merge-cases")
@click.option(
    "--input-dirs", "-i",
    multiple=True,
    required=True,
    help="Input directories to merge. Glob patterns accepted, e.g. 'cases/leo-*'.",
)
@click.option(
    "--output-dir", "-o",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write merged cases into.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview what would be written without touching any files.",
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Allow writing into a non-empty output dir. "
        "Existing files are NEVER overwritten — only new cases are added."
    ),
)
def merge_cases(
    input_dirs: tuple[str, ...],
    output_dir: Path,
    dry_run: bool,
    force: bool,
) -> None:
    """Merge sharded case directories into a single output directory.

    Cases are deduplicated by fix_commit and renumbered sequentially per repo.
    Existing files in the output dir are NEVER overwritten.
    """
    # 1. Expand globs → concrete dirs
    dirs = _expand_dirs(input_dirs)
    if not dirs:
        raise click.UsageError(f"No directories found matching: {list(input_dirs)}")

    click.echo(f"Input dirs ({len(dirs)}):")
    for d in dirs:
        click.echo(f"  {d}")

    # 2. Safety: refuse non-empty output unless --force or --dry-run
    existing_cases: list[Path] = []
    if output_dir.exists():
        existing_cases = [
            f for f in output_dir.glob("*.yaml") if "checkpoint" not in f.name
        ]
    if existing_cases and not force and not dry_run:
        raise click.UsageError(
            f"\nOutput dir '{output_dir}' already contains {len(existing_cases)} case file(s).\n"
            "To append without overwriting: add --force\n"
            "To preview first:             add --dry-run"
        )

    # 3. Load all cases, deduplicate by fix_commit
    all_cases: list[TestCase] = []
    seen_commits: set[str] = set()
    duplicates = 0

    for d in dirs:
        for case in _load_cases_from_dir(d):
            if case.fix_commit in seen_commits:
                duplicates += 1
            else:
                seen_commits.add(case.fix_commit)
                all_cases.append(case)

    click.echo(
        f"\nLoaded {len(all_cases)} unique cases "
        f"({duplicates} duplicates skipped) from {len(dirs)} dirs."
    )

    if not all_cases:
        click.echo("Nothing to merge.")
        return

    # 4. Group by repo, sort within group for stable ordering
    by_repo: dict[str, list[TestCase]] = defaultdict(list)
    for case in all_cases:
        by_repo[case.repo].append(case)

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    blocked = 0

    for repo in sorted(by_repo):
        cases = by_repo[repo]
        repo_short = repo.split("/")[-1][:15].replace(".", "-")
        next_idx = 1 if dry_run else _max_index(repo, output_dir) + 1

        for case in cases:
            new_id = f"{repo_short}-{next_idx:03d}"
            dest = output_dir / f"{new_id}.yaml"

            # Safety: never overwrite an existing file, even with --force
            if dest.exists():
                click.echo(f"  BLOCKED {dest.name} already exists — skipping (no overwrite ever)")
                blocked += 1
                next_idx += 1
                continue

            if dry_run:
                click.echo(f"  [dry-run] {case.id:20s} → {new_id}")
            else:
                save_case(case.model_copy(update={"id": new_id}), dest)
                written += 1

            next_idx += 1

    click.echo("")
    if dry_run:
        click.echo(f"[dry-run] Would write {len(all_cases)} cases to {output_dir}/")
    else:
        click.echo(f"Merged {written} cases → {output_dir}/")
        if blocked:
            click.echo(f"Blocked {blocked} file(s) that already existed (never overwritten).")
