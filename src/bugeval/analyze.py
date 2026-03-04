# src/bugeval/analyze.py
"""Aggregate judge scores into comparison tables and charts."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import click
import yaml

from bugeval.judge_models import JudgeScore


def compute_catch_rate(scores: list[JudgeScore]) -> float:
    """Fraction of cases scoring >= 2 (correct-id or better)."""
    if not scores:
        return 0.0
    return sum(1 for s in scores if s.score >= 2) / len(scores)


def compute_snr(scores: list[JudgeScore]) -> float:
    """Average signal-to-noise ratio across scores."""
    if not scores:
        return 0.0
    return sum(s.noise.snr for s in scores) / len(scores)


def aggregate_scores(scores: list[JudgeScore]) -> dict[str, dict[str, Any]]:
    """Group scores by tool and compute per-tool metrics."""
    by_tool: dict[str, list[JudgeScore]] = {}
    for s in scores:
        by_tool.setdefault(s.tool, []).append(s)

    result = {}
    for tool, tool_scores in sorted(by_tool.items()):
        dist = {i: sum(1 for s in tool_scores if s.score == i) for i in range(4)}
        result[tool] = {
            "count": len(tool_scores),
            "catch_rate": compute_catch_rate(tool_scores),
            "avg_snr": compute_snr(tool_scores),
            "score_dist": dist,
            "avg_score": sum(s.score for s in tool_scores) / len(tool_scores),
        }
    return result


def generate_markdown(agg: dict[str, dict[str, Any]]) -> str:
    """Produce a markdown comparison table from aggregated scores."""
    lines = [
        "| Tool | Cases | Catch Rate | Avg Score | Avg SNR |",
        "|------|-------|-----------|-----------|---------|",
    ]
    for tool, metrics in agg.items():
        lines.append(
            f"| {tool} | {metrics['count']} "
            f"| {metrics['catch_rate']:.1%} "
            f"| {metrics['avg_score']:.2f} "
            f"| {metrics['avg_snr']:.2f} |"
        )
    return "\n".join(lines)


def generate_csv(agg: dict[str, dict[str, Any]], path: Path) -> None:
    """Write aggregated scores to CSV."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "tool",
                "count",
                "catch_rate",
                "avg_score",
                "avg_snr",
                "score_0",
                "score_1",
                "score_2",
                "score_3",
            ],
        )
        writer.writeheader()
        for tool, m in agg.items():
            dist = m["score_dist"]
            writer.writerow(
                {
                    "tool": tool,
                    "count": m["count"],
                    "catch_rate": round(m["catch_rate"], 4),
                    "avg_score": round(m["avg_score"], 4),
                    "avg_snr": round(m["avg_snr"], 4),
                    "score_0": dist.get(0, 0),
                    "score_1": dist.get(1, 0),
                    "score_2": dist.get(2, 0),
                    "score_3": dist.get(3, 0),
                }
            )


def generate_charts(agg: dict[str, dict[str, Any]], out_dir: Path) -> None:
    """Write catch_rate.png and score_dist.png to out_dir."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    tools = list(agg.keys())

    # Catch rate bar chart
    catch_rates = [agg[t]["catch_rate"] for t in tools]
    fig, ax = plt.subplots()
    ax.bar(tools, catch_rates)
    ax.set_ylabel("Catch Rate (score >= 2)")
    ax.set_title("Bug Detection Rate by Tool")
    ax.set_ylim(0, 1)
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    fig.savefig(out_dir / "catch_rate.png", dpi=150)
    plt.close(fig)

    # Score distribution stacked bar
    score_0 = [agg[t]["score_dist"].get(0, 0) for t in tools]
    score_1 = [agg[t]["score_dist"].get(1, 0) for t in tools]
    score_2 = [agg[t]["score_dist"].get(2, 0) for t in tools]
    score_3 = [agg[t]["score_dist"].get(3, 0) for t in tools]
    fig, ax = plt.subplots()
    x = range(len(tools))
    ax.bar(x, score_0, label="0 missed")
    ax.bar(x, score_1, bottom=score_0, label="1 wrong-area")
    bottom_2 = [a + b for a, b in zip(score_0, score_1)]
    ax.bar(x, score_2, bottom=bottom_2, label="2 correct-id")
    bottom_3 = [a + b for a, b in zip(bottom_2, score_2)]
    ax.bar(x, score_3, bottom=bottom_3, label="3 correct+fix")
    ax.set_xticks(list(x))
    ax.set_xticklabels(tools, rotation=30, ha="right")
    ax.set_ylabel("Count")
    ax.set_title("Score Distribution by Tool")
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_dir / "score_dist.png", dpi=150)
    plt.close(fig)


@click.command("analyze")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Path to run directory (must contain scores/ subdirectory)",
)
@click.option("--no-charts", is_flag=True, default=False, help="Skip matplotlib chart generation")
def analyze(run_dir: str, no_charts: bool) -> None:
    """Aggregate judge scores into comparison tables and charts."""
    resolved = Path(run_dir)
    scores_dir = resolved / "scores"
    if not scores_dir.exists() or not list(scores_dir.glob("*.yaml")):
        click.echo(f"No score files found in {scores_dir}")
        return

    scores = []
    for path in sorted(scores_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        scores.append(JudgeScore(**data))

    agg = aggregate_scores(scores)
    out_dir = resolved / "analysis"
    out_dir.mkdir(exist_ok=True)

    # Markdown report
    md = generate_markdown(agg)
    (out_dir / "report.md").write_text(md)
    click.echo(f"Report -> {out_dir / 'report.md'}")

    # CSV
    generate_csv(agg, out_dir / "scores.csv")
    click.echo(f"CSV -> {out_dir / 'scores.csv'}")

    # Charts
    if not no_charts:
        generate_charts(agg, out_dir)
        click.echo(f"Charts -> {out_dir}/")

    click.echo("\n" + md)
