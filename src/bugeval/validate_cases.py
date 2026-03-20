"""CLI command: validate-cases — validate promoted test cases against a repo checkout."""

from __future__ import annotations

import re
from collections import defaultdict
from enum import StrEnum
from pathlib import Path

import click

from bugeval.git_utils import commit_exists, format_patch, get_changed_files, get_diff_stats
from bugeval.io import load_all_cases, save_case
from bugeval.models import ExpectedFinding, TestCase

# ---------------------------------------------------------------------------
# Alignment checking (moved from validate_alignment.py)
# ---------------------------------------------------------------------------


class AlignmentStatus(StrEnum):
    aligned = "aligned"
    file_only = "file-only"
    misaligned = "misaligned"


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_GIT_RE = re.compile(r"^diff --git a/.+ b/(.+)$")


def parse_patch_files(patch_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse a unified diff and return {filepath: [(start, end), ...]} of changed hunks.

    Each (start, end) is the inclusive line range on the pre-image (- side).
    A hunk with count=0 produces a single-point range (start, start).
    """
    result: dict[str, list[tuple[int, int]]] = defaultdict(list)
    current_file: str | None = None

    for line in patch_text.splitlines():
        diff_match = _DIFF_GIT_RE.match(line)
        if diff_match:
            current_file = diff_match.group(1).strip()
            continue

        hunk_match = _HUNK_RE.match(line)
        if hunk_match and current_file is not None:
            start = int(hunk_match.group(1))
            count_str = hunk_match.group(2)
            count = int(count_str) if count_str is not None else 1
            end = start + max(count, 1) - 1
            result[current_file].append((start, end))

    return dict(result)


def check_finding_alignment(
    finding_file: str,
    finding_line: int,
    patch_files: dict[str, list[tuple[int, int]]],
) -> AlignmentStatus:
    """Check if a single expected finding aligns with the patch."""
    if finding_file not in patch_files:
        return AlignmentStatus.misaligned
    for start, end in patch_files[finding_file]:
        if start <= finding_line <= end:
            return AlignmentStatus.aligned
    return AlignmentStatus.file_only


def validate_case_alignment(
    case: TestCase,
    patch_text: str,
) -> list[tuple[ExpectedFinding, AlignmentStatus]]:
    """Validate all expected findings for a case against its patch."""
    patch_files = parse_patch_files(patch_text)
    return [
        (finding, check_finding_alignment(finding.file, finding.line, patch_files))
        for finding in case.expected_findings
    ]


def _case_level_status(
    results: list[tuple[ExpectedFinding, AlignmentStatus]],
) -> AlignmentStatus:
    """Return the best alignment status across all findings."""
    if not results:
        return AlignmentStatus.aligned
    statuses = [s for _, s in results]
    if AlignmentStatus.aligned in statuses:
        return AlignmentStatus.aligned
    if AlignmentStatus.file_only in statuses:
        return AlignmentStatus.file_only
    return AlignmentStatus.misaligned


def _repo_from_case_id(case_id: str) -> str:
    parts = case_id.rsplit("-", 1)
    return parts[0] if len(parts) == 2 else case_id


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("validate-cases")
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
    "--patches-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="If provided, verify that a .patch file exists for each case.",
)
@click.option(
    "--update-stats",
    is_flag=True,
    help="Auto-populate stats (lines_added, files_changed, etc.) and save.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report validation results without writing stats back.",
)
@click.option(
    "--check-alignment",
    is_flag=True,
    default=False,
    help="Check that expected findings fall within patch hunks (requires --patches-dir).",
)
def validate_cases(
    repo_dir: Path,
    cases_dir: Path,
    patches_dir: Path | None,
    update_stats: bool,
    dry_run: bool,
    check_alignment: bool,
) -> None:
    """Validate test cases in cases-dir against a repo checkout."""
    if check_alignment and patches_dir is None:
        raise click.UsageError("--check-alignment requires --patches-dir.")

    if not cases_dir.exists():
        click.echo(f"Cases directory not found: {cases_dir}", err=True)
        raise SystemExit(1)

    cases = load_all_cases(cases_dir)
    if not cases:
        click.echo("No cases found.")
        return

    # Run alignment check if requested
    if check_alignment and patches_dir is not None:
        _run_alignment_check(cases, cases_dir, patches_dir)
        return

    all_passed = True
    for case in cases:
        errors: list[str] = []

        # Check commits exist
        for attr, sha in [
            ("base_commit", case.base_commit),
            ("head_commit", case.head_commit),
            ("fix_commit", case.fix_commit),
        ]:
            if not commit_exists(sha, repo_dir):
                errors.append(f"{attr} not found: {sha[:12]}")

        # Check diff is non-empty (catches base_commit == head_commit)
        warnings: list[str] = []
        if not errors:
            try:
                diff = format_patch(case.base_commit, case.head_commit, repo_dir)
                if not diff.strip():
                    errors.append("empty diff between base_commit and head_commit")
                else:
                    changed = get_changed_files(case.base_commit, case.head_commit, repo_dir)
                    for finding in case.expected_findings:
                        if finding.file not in changed:
                            warnings.append(f"expected finding file '{finding.file}' not in diff")
            except Exception as exc:
                errors.append(f"could not compute diff: {exc}")

        # Check patch file exists (if patches_dir provided)
        if patches_dir is not None:
            patch_path = patches_dir / f"{case.id}.patch"
            if not patch_path.exists():
                errors.append(f"patch file missing: {patch_path}")

        if errors:
            click.echo(f"  FAIL {case.id}: {'; '.join(errors)}")
            all_passed = False
            continue

        for w in warnings:
            click.echo(f"  WARN {case.id}: {w}")

        # Auto-populate stats
        if update_stats and not dry_run:
            stats = get_diff_stats(case.base_commit, case.head_commit, repo_dir)
            updated = case.model_copy(update={"stats": stats})
            save_case(updated, cases_dir / f"{case.id}.yaml")
            click.echo(f"  PASS {case.id} (stats updated)")
        else:
            click.echo(f"  PASS {case.id}")

    if not all_passed:
        raise SystemExit(1)


def _run_alignment_check(
    cases: list[TestCase],
    cases_dir: Path,
    patches_dir: Path,
) -> None:
    """Run alignment check on all cases against their patches."""
    repo_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"aligned": 0, "file-only": 0, "misaligned": 0}
    )

    for case in cases:
        patch_path = patches_dir / f"{case.id}.patch"
        if not patch_path.exists():
            click.echo(f"[skip] {case.id} (no patch file)")
            continue

        patch_text = patch_path.read_text()
        per_finding = validate_case_alignment(case, patch_text)
        case_status = _case_level_status(per_finding)

        click.echo(f"[{case_status}] {case.id}")

        repo = _repo_from_case_id(case.id)
        repo_counts[repo][case_status] += 1

    _print_alignment_summary(repo_counts)


def _find_case_file(cases_dir: Path, case_id: str) -> Path | None:
    for path in cases_dir.rglob(f"{case_id}.yaml"):
        return path
    return None


def _print_alignment_summary(repo_counts: dict[str, dict[str, int]]) -> None:
    if not repo_counts:
        return

    totals: dict[str, int] = {"aligned": 0, "file-only": 0, "misaligned": 0}
    header = f"{'Repo':<20} {'aligned':>8} {'file-only':>10} {'misaligned':>11} {'total':>6}"
    click.echo("")
    click.echo(header)
    click.echo("-" * len(header))

    for repo in sorted(repo_counts):
        counts = repo_counts[repo]
        total = sum(counts.values())
        click.echo(
            f"{repo:<20} {counts['aligned']:>8} {counts['file-only']:>10}"
            f" {counts['misaligned']:>11} {total:>6}"
        )
        for key in totals:
            totals[key] += counts[key]

    grand_total = sum(totals.values())
    click.echo("-" * len(header))
    click.echo(
        f"{'TOTAL':<20} {totals['aligned']:>8} {totals['file-only']:>10}"
        f" {totals['misaligned']:>11} {grand_total:>6}"
    )
