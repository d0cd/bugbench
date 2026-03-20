"""review-disputes CLI: export disputed findings to CSV, import human decisions."""

from __future__ import annotations

import csv
from pathlib import Path

import click
import yaml

from bugeval.io import load_case, save_case


def export_disputes(results_dir: Path) -> list[dict[str, str]]:
    """Read cross-validation results and collect disputed findings."""
    rows: list[dict[str, str]] = []
    for path in sorted(results_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        case_id = data.get("case_id", path.stem)
        for v in data.get("verdicts", []):
            if v.get("verdict") == "disputed":
                rows.append({
                    "case_id": case_id,
                    "finding_index": str(v.get("index", "")),
                    "finding_summary": v.get("finding_summary", ""),
                    "model_reason": v.get("reason", ""),
                    "human_decision": "",  # to be filled by reviewer
                    "updated_summary": "",  # optional: corrected summary
                })
    return rows


def write_disputes_csv(rows: list[dict[str, str]], output: Path) -> None:
    """Write dispute rows to CSV."""
    if not rows:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_id", "finding_index", "finding_summary",
        "model_reason", "human_decision", "updated_summary",
    ]
    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_disputes_csv(path: Path) -> list[dict[str, str]]:
    """Read reviewed disputes CSV."""
    rows: list[dict[str, str]] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def apply_dispute_decisions(
    rows: list[dict[str, str]],
    cases_dir: Path,
) -> tuple[int, int, int]:
    """Apply human decisions from reviewed CSV to case YAML files.

    Returns (updated, removed, skipped) counts.

    human_decision values:
    - "keep": finding is correct, mark case as verified
    - "fix": update the finding summary with updated_summary
    - "remove": remove this finding from expected_findings
    - "invalidate": mark entire case as invalid
    - "" (empty): skip, no decision made
    """
    # Group decisions by case_id
    by_case: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)

    updated = 0
    removed = 0
    skipped = 0

    for case_id, decisions in by_case.items():
        # Find the case YAML file
        case_path = _find_case_file(cases_dir, case_id)
        if case_path is None:
            skipped += len(decisions)
            continue

        case = load_case(case_path)
        modified = False
        indices_to_remove: list[int] = []

        for d in decisions:
            idx = int(d.get("finding_index", -1))
            decision = d.get("human_decision", "").strip().lower()

            if not decision:
                skipped += 1
                continue

            if decision == "keep":
                updated += 1
                modified = True
            elif decision == "fix":
                new_summary = d.get("updated_summary", "").strip()
                if new_summary and 0 <= idx < len(case.expected_findings):
                    case.expected_findings[idx] = (
                        case.expected_findings[idx].model_copy(
                            update={"summary": new_summary}
                        )
                    )
                    updated += 1
                    modified = True
                else:
                    skipped += 1
            elif decision == "remove":
                indices_to_remove.append(idx)
                removed += 1
                modified = True
            elif decision == "invalidate":
                case = case.model_copy(
                    update={"valid_for_code_review": False}
                )
                removed += 1
                modified = True
            else:
                skipped += 1

        # Remove findings in reverse order to preserve indices
        for idx in sorted(indices_to_remove, reverse=True):
            if 0 <= idx < len(case.expected_findings):
                case.expected_findings.pop(idx)

        if modified:
            case = case.model_copy(update={
                "verified": True,
                "verified_by": "human+cross-validation",
            })
            save_case(case, case_path)

    return updated, removed, skipped


def _find_case_file(cases_dir: Path, case_id: str) -> Path | None:
    for path in cases_dir.rglob("*.yaml"):
        if path.stem == case_id:
            return path
    return None


@click.group("review-disputes")
def review_disputes() -> None:
    """Export disputed findings to CSV, import human decisions."""


@review_disputes.command("export")
@click.option(
    "--results-dir", required=True, type=click.Path(exists=True),
    help="Directory with cross-validation result YAML files",
)
@click.option(
    "--output", required=True, type=click.Path(),
    help="Output CSV path",
)
def export_cmd(results_dir: str, output: str) -> None:
    """Export disputed findings to CSV for human review."""
    rows = export_disputes(Path(results_dir))
    if not rows:
        click.echo("No disputed findings found.")
        return
    write_disputes_csv(rows, Path(output))
    click.echo(f"Exported {len(rows)} disputed findings to {output}")


@review_disputes.command("import")
@click.option(
    "--csv-file", required=True, type=click.Path(exists=True),
    help="Reviewed CSV file",
)
@click.option(
    "--cases-dir", required=True, type=click.Path(exists=True),
    help="Cases directory",
)
def import_cmd(csv_file: str, cases_dir: str) -> None:
    """Import human decisions from reviewed CSV and update case YAML files."""
    rows = read_disputes_csv(Path(csv_file))
    if not rows:
        click.echo("No rows in CSV.")
        return
    updated, removed, skipped = apply_dispute_decisions(
        rows, Path(cases_dir)
    )
    click.echo(
        f"Updated: {updated}, Removed: {removed}, Skipped: {skipped}"
    )
