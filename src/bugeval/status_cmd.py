"""CLI command: status — show pipeline progress for a run directory."""

from __future__ import annotations

from pathlib import Path

import click

from bugeval.pr_eval_models import CaseToolStatus, RunState


def get_run_status(run_dir: Path) -> dict:
    """Collect progress stats from a run directory. Returns a dict."""
    checkpoint_path = run_dir / "checkpoint.yaml"
    checkpoint_exists = checkpoint_path.exists()
    run_state = RunState.load(checkpoint_path) if checkpoint_exists else RunState()

    states = run_state.states()
    total = len(states)
    done = sum(1 for s in states if s.status == CaseToolStatus.done)
    failed = sum(1 for s in states if s.status == CaseToolStatus.failed)
    pending = total - done - failed

    # Count normalized YAMLs (exclude checkpoint.yaml itself)
    normalized_count = sum(1 for p in run_dir.glob("*.yaml") if p.name != "checkpoint.yaml")

    scores_dir = run_dir / "scores"
    scored_count = len(list(scores_dir.glob("*.yaml"))) if scores_dir.exists() else 0
    report_path = run_dir / "analysis" / "report.md"

    return {
        "run_dir": str(run_dir),
        "total": total,
        "done": done,
        "failed": failed,
        "pending": pending,
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
        f"  Checkpoint:  {info['done']}/{info['total']} done "
        f"({info['failed']} failed, {info['pending']} pending)"
    )
    click.echo(f"  Normalized:  {info['normalized']} results")
    click.echo(f"  Scored:      {info['scored']} results")
    analysis = f"yes ({info['report_path']})" if info["has_analysis"] else "no"
    click.echo(f"  Analysis:    {analysis}")
