"""CLI command: status — show pipeline progress for a run directory."""

from __future__ import annotations

from pathlib import Path

import click


def get_run_status(run_dir: Path) -> dict:
    """Collect progress stats from a run directory. Returns a dict."""
    raw_dir = run_dir / "raw"

    done = 0
    failed = 0
    if raw_dir.exists():
        for case_dir in raw_dir.iterdir():
            if not case_dir.is_dir():
                continue
            has_metadata = (case_dir / "metadata.json").exists()
            has_comments = (case_dir / "comments.json").exists()
            has_error = (case_dir / "error.json").exists()
            if has_metadata or has_comments:
                done += 1
            elif has_error:
                failed += 1

    total = done + failed

    # Count normalized YAMLs
    normalized_count = sum(1 for p in run_dir.glob("*.yaml") if p.name != "checkpoint.yaml")

    scores_dir = run_dir / "scores"
    scored_count = len(list(scores_dir.glob("*.yaml"))) if scores_dir.exists() else 0
    report_path = run_dir / "analysis" / "report.md"

    return {
        "run_dir": str(run_dir),
        "total": total,
        "done": done,
        "failed": failed,
        "pending": 0,
        "normalized": normalized_count,
        "scored": scored_count,
        "has_analysis": report_path.exists(),
        "report_path": str(report_path) if report_path.exists() else None,
    }


@click.command("status")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Path to a run directory",
)
def status(run_dir: str) -> None:
    """Show pipeline progress for a run directory."""
    info = get_run_status(Path(run_dir))
    click.echo(f"Run: {info['run_dir']}")
    click.echo(
        f"  Raw:         {info['done']}/{info['total']} done "
        f"({info['failed']} failed)"
    )
    click.echo(f"  Normalized:  {info['normalized']} results")
    click.echo(f"  Scored:      {info['scored']} results")
    analysis = f"yes ({info['report_path']})" if info["has_analysis"] else "no"
    click.echo(f"  Analysis:    {analysis}")
