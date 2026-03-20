# src/bugeval/analyze.py
"""Aggregate judge scores into comparison tables and charts."""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path
from typing import Any

import click
import yaml
from pydantic import ValidationError

from bugeval.judge_models import JudgeScore
from bugeval.models import TestCase
from bugeval.pr_eval_models import default_scoring
from bugeval.result_models import NormalizedResult
from bugeval.run_pr_eval import load_cases


def bootstrap_ci(
    values: list[float], n_boot: int = 2000, ci: float = 0.95, seed: int = 42
) -> tuple[float, float]:
    """Return (lower, upper) bootstrap CI for the mean of values."""
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)
    means = [sum(rng.choices(values, k=len(values))) / len(values) for _ in range(n_boot)]
    means.sort()
    lo = int((1 - ci) / 2 * n_boot)
    hi = int((1 + ci) / 2 * n_boot)
    return means[lo], means[hi]


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Apply Benjamini-Hochberg FDR correction to a list of p-values.

    Returns adjusted p-values in the same order as the input.
    Adjusted values are capped at 1.0 and monotonized (non-decreasing in rank).
    """
    m = len(p_values)
    if m == 0:
        return []
    # Sort indices by p-value
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * m
    # Work backwards to enforce monotonicity
    prev = 1.0
    for rank_minus_1 in range(m - 1, -1, -1):
        orig_idx, raw_p = indexed[rank_minus_1]
        rank = rank_minus_1 + 1
        corrected = min(raw_p * m / rank, 1.0)
        corrected = min(corrected, prev)
        adjusted[orig_idx] = corrected
        prev = corrected
    return adjusted


def permutation_p_value(
    a: list[float], b: list[float], n_perm: int = 5000, seed: int = 42
) -> float:
    """Two-sided permutation test: P(|mean_a - mean_b| >= observed) under H0."""
    if not a or not b:
        return 1.0
    rng = random.Random(seed)
    observed = abs(sum(a) / len(a) - sum(b) / len(b))
    combined = a + b
    count = 0
    for _ in range(n_perm):
        shuffled = combined[:]
        rng.shuffle(shuffled)
        diff = abs(sum(shuffled[: len(a)]) / len(a) - sum(shuffled[len(a) :]) / len(b))
        if diff >= observed:
            count += 1
    return count / n_perm


def compute_vote_agreement(scores: list[JudgeScore]) -> float:
    """Mean fraction of votes agreeing with majority across scored cases."""
    if not scores:
        return 0.0
    return sum(s.vote_agreement for s in scores) / len(scores)


def compute_catch_rate(scores: list[JudgeScore]) -> float:
    """Fraction of cases scoring >= catch_threshold (correct-id or better)."""
    if not scores:
        return 0.0
    scoring = default_scoring()
    return sum(1 for s in scores if s.score >= scoring.catch_threshold) / len(scores)


def compute_snr(scores: list[JudgeScore]) -> float:
    """Average signal-to-noise ratio across scores."""
    if not scores:
        return 0.0
    return sum(s.noise.snr for s in scores) / len(scores)


def compute_snr_conservative(scores: list[JudgeScore]) -> float:
    """Average conservative SNR: TP-expected only (ignores TP-novel)."""
    if not scores:
        return 0.0
    values = []
    for s in scores:
        total = s.noise.total_comments
        if total > 0:
            values.append(s.noise.true_positives / total)
        else:
            values.append(0.0)
    return sum(values) / len(values)


def compute_snr_inclusive(scores: list[JudgeScore]) -> float:
    """Average inclusive SNR: (TP-expected + TP-novel) / total. Same as compute_snr."""
    return compute_snr(scores)


def compute_avg_quality_adjusted_precision(scores: list[JudgeScore]) -> float:
    if not scores:
        return 0.0
    return sum(s.noise.quality_adjusted_precision for s in scores) / len(scores)


def compute_avg_weighted_signal(scores: list[JudgeScore]) -> float:
    if not scores:
        return 0.0
    return sum(s.noise.weighted_signal for s in scores) / len(scores)


def compute_avg_actionability_rate(scores: list[JudgeScore]) -> float:
    if not scores:
        return 0.0
    return sum(s.noise.actionability_rate for s in scores) / len(scores)


def compute_avg_noise_ratio(scores: list[JudgeScore]) -> float:
    if not scores:
        return 0.0
    return sum(s.noise.noise_ratio for s in scores) / len(scores)


def compute_avg_precision(scores: list[JudgeScore]) -> float:
    if not scores:
        return 0.0
    return sum(s.noise.precision for s in scores) / len(scores)


def aggregate_scores(scores: list[JudgeScore]) -> dict[str, dict[str, Any]]:
    """Group scores by tool and compute per-tool metrics."""
    by_tool: dict[str, list[JudgeScore]] = {}
    for s in scores:
        by_tool.setdefault(s.tool, []).append(s)

    scoring = default_scoring()
    result = {}
    for tool, tool_scores in sorted(by_tool.items()):
        dist = {i: sum(1 for s in tool_scores if s.score == i) for i in scoring.scale}
        catch_values = [1.0 if s.score >= scoring.catch_threshold else 0.0 for s in tool_scores]
        score_values = [float(s.score) for s in tool_scores]
        catch_rate_ci = bootstrap_ci(catch_values)
        avg_score_ci = bootstrap_ci(score_values)
        qap_values = [s.noise.quality_adjusted_precision for s in tool_scores]
        qap_ci = bootstrap_ci(qap_values)
        result[tool] = {
            "count": len(tool_scores),
            "catch_rate": compute_catch_rate(tool_scores),
            "catch_rate_lo": catch_rate_ci[0],
            "catch_rate_hi": catch_rate_ci[1],
            "avg_snr": compute_snr(tool_scores),
            "avg_snr_conservative": compute_snr_conservative(tool_scores),
            "score_dist": dist,
            "avg_score": sum(s.score for s in tool_scores) / len(tool_scores),
            "avg_score_lo": avg_score_ci[0],
            "avg_score_hi": avg_score_ci[1],
            "vote_agreement": compute_vote_agreement(tool_scores),
            "avg_quality_adjusted_precision": compute_avg_quality_adjusted_precision(tool_scores),
            "avg_qap_lo": qap_ci[0],
            "avg_qap_hi": qap_ci[1],
            "avg_weighted_signal": compute_avg_weighted_signal(tool_scores),
            "avg_actionability_rate": compute_avg_actionability_rate(tool_scores),
            "avg_noise_ratio": compute_avg_noise_ratio(tool_scores),
            "avg_precision": compute_avg_precision(tool_scores),
            "avg_novel_findings": (
                sum(s.noise.novel_findings for s in tool_scores) / len(tool_scores)
            ),
        }
    return result


def generate_markdown(agg: dict[str, dict[str, Any]]) -> str:
    """Produce a markdown comparison table from aggregated scores."""
    lines = [
        "| Tool | Cases | Detection Rate | Avg Score "
        "| QAP | Actionability | Noise Ratio | Precision | SNR (inclusive) | Agreement |",
        "|------|-------|--------------|-----------|"
        "-----|---------------|-------------|-----------|----------------|-----------|",
    ]
    for tool, metrics in agg.items():
        catch_lo = metrics.get("catch_rate_lo", metrics["catch_rate"])
        catch_hi = metrics.get("catch_rate_hi", metrics["catch_rate"])
        score_lo = metrics.get("avg_score_lo", metrics["avg_score"])
        score_hi = metrics.get("avg_score_hi", metrics["avg_score"])
        qap = metrics.get("avg_quality_adjusted_precision", 0.0)
        act_rate = metrics.get("avg_actionability_rate", 0.0)
        noise = metrics.get("avg_noise_ratio", 0.0)
        prec = metrics.get("avg_precision", 0.0)
        lines.append(
            f"| {tool} | {metrics['count']} "
            f"| {metrics['catch_rate']:.1%} [{catch_lo:.1%}–{catch_hi:.1%}] "
            f"| {metrics['avg_score']:.2f} [{score_lo:.2f}–{score_hi:.2f}] "
            f"| {qap:.2f} "
            f"| {act_rate:.0%} "
            f"| {noise:.0%} "
            f"| {prec:.2f} "
            f"| {metrics['avg_snr']:.2f} "
            f"| {metrics.get('vote_agreement', 0.0):.0%} |"
        )
    return "\n".join(lines)


def generate_csv(agg: dict[str, dict[str, Any]], path: Path) -> None:
    """Write aggregated scores to CSV."""
    scoring = default_scoring()
    score_fields = [f"score_{i}" for i in scoring.scale]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "tool",
                "count",
                "catch_rate",
                "catch_rate_lo",
                "catch_rate_hi",
                "avg_score",
                "avg_score_lo",
                "avg_score_hi",
                "avg_quality_adjusted_precision",
                "avg_weighted_signal",
                "avg_actionability_rate",
                "avg_noise_ratio",
                "avg_precision",
                "avg_novel_findings",
                "avg_snr",
                "vote_agreement",
            ]
            + score_fields,
        )
        writer.writeheader()
        for tool, m in agg.items():
            dist = m["score_dist"]
            row: dict[str, Any] = {
                "tool": tool,
                "count": m["count"],
                "catch_rate": round(m["catch_rate"], 4),
                "catch_rate_lo": round(m.get("catch_rate_lo", m["catch_rate"]), 4),
                "catch_rate_hi": round(m.get("catch_rate_hi", m["catch_rate"]), 4),
                "avg_score": round(m["avg_score"], 4),
                "avg_score_lo": round(m.get("avg_score_lo", m["avg_score"]), 4),
                "avg_score_hi": round(m.get("avg_score_hi", m["avg_score"]), 4),
                "avg_quality_adjusted_precision": round(
                    m.get("avg_quality_adjusted_precision", 0.0), 4
                ),
                "avg_weighted_signal": round(m.get("avg_weighted_signal", 0.0), 4),
                "avg_actionability_rate": round(m.get("avg_actionability_rate", 0.0), 4),
                "avg_noise_ratio": round(m.get("avg_noise_ratio", 0.0), 4),
                "avg_precision": round(m.get("avg_precision", 0.0), 4),
                "avg_novel_findings": round(m.get("avg_novel_findings", 0.0), 4),
                "avg_snr": round(m["avg_snr"], 4),
                "vote_agreement": round(m.get("vote_agreement", 0.0), 4),
            }
            for i in scoring.scale:
                row[f"score_{i}"] = dist.get(i, 0)
            writer.writerow(row)


def load_cases_lookup(cases_dir: Path) -> dict[str, TestCase]:
    """Load all test cases from cases_dir into a dict keyed by case ID."""
    if not cases_dir.exists():
        return {}
    cases = load_cases(cases_dir)
    return {c.id: c for c in cases}


def load_normalized_lookup(run_dir: Path) -> dict[tuple[str, str], NormalizedResult]:
    """Load all NormalizedResult YAMLs from run_dir. Keys are (test_case_id, tool)."""
    lookup: dict[tuple[str, str], NormalizedResult] = {}
    for path in run_dir.glob("*.yaml"):
        if path.name == "checkpoint.yaml":
            continue
        data = yaml.safe_load(path.read_text()) or {}
        try:
            r = NormalizedResult(**data)
            lookup[(r.test_case_id, r.tool)] = r
        except ValidationError as exc:
            print(f"Warning: skipping {path.name} in normalized lookup — {exc}", file=sys.stderr)
    return lookup


def slice_scores(
    scores: list[JudgeScore],
    cases: dict[str, TestCase],
    dimension: str,
) -> dict[str, list[JudgeScore]]:
    """Group scores by a TestCase categorical field (e.g. 'difficulty', 'category')."""
    groups: dict[str, list[JudgeScore]] = {}
    for s in scores:
        case = cases.get(s.test_case_id)
        if case is None:
            key = "unknown"
        else:
            raw: Any = getattr(case, dimension, "unknown")
            key = raw.value if hasattr(raw, "value") else str(raw)
        groups.setdefault(key, []).append(s)
    return groups


def slice_scores_by_context(
    scores: list[JudgeScore],
    results: dict[tuple[str, str], NormalizedResult],
) -> dict[str, list[JudgeScore]]:
    """Group scores by context_level from the corresponding NormalizedResult."""
    groups: dict[str, list[JudgeScore]] = {}
    for s in scores:
        r = results.get((s.test_case_id, s.tool))
        key = r.context_level if r else "unknown"
        groups.setdefault(key, []).append(s)
    return groups


def compute_cost_per_tool(
    scores: list[JudgeScore],
    results: dict[tuple[str, str], NormalizedResult],
) -> dict[str, dict[str, float]]:
    """Compute per-tool cost_per_review and cost_per_detection."""
    scoring = default_scoring()
    by_tool: dict[str, list[JudgeScore]] = {}
    for s in scores:
        by_tool.setdefault(s.tool, []).append(s)

    out: dict[str, dict[str, float]] = {}
    for tool, tool_scores in by_tool.items():
        total_cost = sum(
            results[(s.test_case_id, s.tool)].metadata.cost_usd
            for s in tool_scores
            if (s.test_case_id, s.tool) in results
        )
        n = len(tool_scores)
        detections = sum(1 for s in tool_scores if s.score >= scoring.catch_threshold)
        out[tool] = {
            "total_cost_usd": total_cost,
            "cost_per_review": total_cost / n if n > 0 else 0.0,
            "cost_per_detection": total_cost / detections if detections > 0 else 0.0,
        }
    return out


def generate_fp_analysis_markdown(
    scores: list[JudgeScore],
    cases: dict[str, TestCase],
) -> str:
    """Produce a False Positive Analysis section for clean (negative control) cases."""
    clean_scores = [
        s
        for s in scores
        if cases.get(s.test_case_id, None) is not None
        and cases[s.test_case_id].case_type == "clean"
    ]
    if not clean_scores:
        return ""

    by_tool: dict[str, list[JudgeScore]] = {}
    for s in clean_scores:
        by_tool.setdefault(s.tool, []).append(s)

    lines = [
        "## False Positive Analysis (Clean Cases)",
        "",
        "| Tool | Cases | Total Comments | FP | TP-novel | FP Rate |",
        "|------|-------|---------------|-----|----------|---------|",
    ]
    for tool in sorted(by_tool):
        tool_scores = by_tool[tool]
        n_cases = len(tool_scores)
        total_comments = sum(s.noise.total_comments for s in tool_scores)
        fp = sum(s.noise.false_positives for s in tool_scores)
        novel = sum(s.noise.novel_findings for s in tool_scores)
        fp_rate = fp / total_comments if total_comments > 0 else 0.0
        lines.append(f"| {tool} | {n_cases} | {total_comments} | {fp} | {novel} | {fp_rate:.1%} |")

    lines.append("")
    return "\n".join(lines)


def generate_slice_markdown(
    scores: list[JudgeScore],
    cases: dict[str, TestCase],
    dimension: str,
) -> str:
    """Produce a per-dimension breakdown markdown table (value x tool)."""
    groups = slice_scores(scores, cases, dimension)
    lines = [
        f"## By {dimension.replace('_', ' ').title()}",
        "",
        "| Value | Tool | Cases | Catch Rate | Avg Score |",
        "|-------|------|-------|-----------|-----------|",
    ]
    for value in sorted(groups.keys()):
        agg = aggregate_scores(groups[value])
        for tool, metrics in agg.items():
            lines.append(
                f"| {value} | {tool} | {metrics['count']} "
                f"| {metrics['catch_rate']:.1%} "
                f"| {metrics['avg_score']:.2f} |"
            )
    return "\n".join(lines)


def generate_slice_markdown_context(
    scores: list[JudgeScore],
    results: dict[tuple[str, str], NormalizedResult],
) -> str:
    """Produce a context_level breakdown markdown table."""
    groups = slice_scores_by_context(scores, results)
    lines = [
        "## By Context Level",
        "",
        "| Context | Tool | Cases | Catch Rate | Avg Score |",
        "|---------|------|-------|-----------|-----------|",
    ]
    for value in sorted(groups.keys()):
        agg = aggregate_scores(groups[value])
        for tool, metrics in agg.items():
            lines.append(
                f"| {value} | {tool} | {metrics['count']} "
                f"| {metrics['catch_rate']:.1%} "
                f"| {metrics['avg_score']:.2f} |"
            )
    return "\n".join(lines)


def generate_charts(agg: dict[str, dict[str, Any]], out_dir: Path) -> bool:
    """Write catch_rate.png and score_dist.png to out_dir.

    Returns False if matplotlib is unavailable.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    tools = list(agg.keys())

    scoring = default_scoring()

    # Catch rate bar chart
    catch_rates = [agg[t]["catch_rate"] for t in tools]
    fig, ax = plt.subplots()
    ax.bar(tools, catch_rates)
    ax.set_ylabel(f"Detection Rate (score >= {scoring.catch_threshold})")
    ax.set_title("Detection Rate by Tool")
    ax.set_ylim(0, 1)
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    fig.savefig(out_dir / "catch_rate.png", dpi=150)
    plt.close(fig)

    # Score distribution stacked bar
    fig, ax = plt.subplots()
    x = range(len(tools))
    bottoms = [0] * len(tools)
    for i in scoring.scale:
        counts = [agg[t]["score_dist"].get(i, 0) for t in tools]
        label = f"{i} {scoring.labels.get(i, str(i))}"
        ax.bar(x, counts, bottom=bottoms, label=label)
        bottoms = [b + c for b, c in zip(bottoms, counts)]
    ax.set_xticks(list(x))
    ax.set_xticklabels(tools, rotation=30, ha="right")
    ax.set_ylabel("Count")
    ax.set_title("Score Distribution by Tool")
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_dir / "score_dist.png", dpi=150)
    plt.close(fig)

    return True


def generate_confidence_band_markdown(
    scores: list[JudgeScore],
    results: dict[tuple[str, str], NormalizedResult],
) -> str:
    """Produce a 'Score by Confidence Band' table.

    Buckets cases by the max comment confidence into [0.5–0.7), [0.7–0.9), [0.9–1.0].
    Cases with no confidence data are omitted.
    Returns empty string if no confidence data is available.
    """
    bands: dict[str, list[JudgeScore]] = {
        "[0.5-0.7)": [],
        "[0.7-0.9)": [],
        "[0.9-1.0]": [],
    }

    for s in scores:
        r = results.get((s.test_case_id, s.tool))
        if r is None:
            continue
        confidences = [c.confidence for c in r.comments if c.confidence is not None]
        if not confidences:
            continue
        max_conf = max(confidences)
        if max_conf < 0.7:
            bands["[0.5-0.7)"].append(s)
        elif max_conf < 0.9:
            bands["[0.7-0.9)"].append(s)
        else:
            bands["[0.9-1.0]"].append(s)

    if not any(bands.values()):
        return ""

    lines = [
        "## Score by Confidence Band",
        "",
        "| Band | Cases | Catch Rate | Avg Score |",
        "|------|-------|-----------|-----------|",
    ]
    for band, band_scores in bands.items():
        if not band_scores:
            lines.append(f"| {band} | 0 | — | — |")
            continue
        catch_rate = compute_catch_rate(band_scores)
        avg_score = sum(s.score for s in band_scores) / len(band_scores)
        lines.append(f"| {band} | {len(band_scores)} | {catch_rate:.1%} | {avg_score:.2f} |")
    return "\n".join(lines)


def generate_dx_markdown(results: dict[tuple[str, str], NormalizedResult]) -> str:
    """Produce DX assessment table averaging each dimension by tool."""
    dimensions = (
        "actionability",
        "false_positive_burden",
        "integration_friction",
        "response_latency",
    )
    by_tool: dict[str, list] = {}
    for (_, tool), r in results.items():
        if r.dx is not None:
            by_tool.setdefault(tool, []).append(r.dx)

    if not by_tool:
        return ""

    lines = [
        "## DX Assessment",
        "",
        "| Tool | Actionability | FP Burden | Integration | Latency |",
        "|------|--------------|-----------|------------|---------|",
    ]
    for tool in sorted(by_tool.keys()):
        dxs = by_tool[tool]
        avgs = [sum(getattr(d, dim) for d in dxs) / len(dxs) for dim in dimensions]
        lines.append(f"| {tool} | {avgs[0]:.1f} | {avgs[1]:.1f} | {avgs[2]:.1f} | {avgs[3]:.1f} |")
    return "\n".join(lines)


def run_analyze(run_dir: Path, cases_dir: Path, no_charts: bool = False) -> None:
    """Run the full analysis pipeline on a completed run directory."""
    scores_dir = run_dir / "scores"
    if not scores_dir.exists() or not list(scores_dir.glob("*.yaml")):
        click.echo(f"No score files found in {scores_dir}")
        return

    scores: list[JudgeScore] = []
    for path in sorted(scores_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        try:
            scores.append(JudgeScore(**data))
        except ValidationError as exc:
            click.echo(f"Warning: skipping {path.name} — {exc}", err=True)

    agg = aggregate_scores(scores)
    cases = load_cases_lookup(cases_dir)
    results = load_normalized_lookup(run_dir)

    out_dir = run_dir / "analysis"
    out_dir.mkdir(exist_ok=True)

    md_lines = [generate_markdown(agg)]

    # Cost metrics (only if any cost data is available)
    cost = compute_cost_per_tool(scores, results)
    if any(m["total_cost_usd"] > 0 for m in cost.values()):
        cost_lines = [
            "\n## Cost Metrics\n",
            "| Tool | Total Cost | Per Review | Per Detection |",
            "|------|-----------|-----------|--------------|",
        ]
        for tool, m in sorted(cost.items()):
            cost_lines.append(
                f"| {tool} | ${m['total_cost_usd']:.4f} "
                f"| ${m['cost_per_review']:.4f} "
                f"| ${m['cost_per_detection']:.4f} |"
            )
        md_lines.append("\n".join(cost_lines))

    # Dimensional slices by TestCase fields
    if cases:
        for dim in (
            "category",
            "difficulty",
            "severity",
            "pr_size",
            "language",
            "visibility",
            "verified",
            "case_type",
        ):
            md_lines.append(generate_slice_markdown(scores, cases, dim))

    # Context-level slice
    if results:
        md_lines.append(generate_slice_markdown_context(scores, results))

    # Confidence band analysis (only if any comment has confidence data)
    conf_md = generate_confidence_band_markdown(scores, results)
    if conf_md:
        md_lines.append(conf_md)

    # DX assessment (only if any result has dx data)
    dx_md = generate_dx_markdown(results)
    if dx_md:
        md_lines.append(dx_md)

    # False Positive Analysis for clean (negative control) cases
    if cases:
        fp_md = generate_fp_analysis_markdown(scores, cases)
        if fp_md:
            md_lines.append(fp_md)

    # Pairwise permutation p-value table (catch_rate) with BH FDR correction
    tools_list = sorted(agg.keys())
    if len(tools_list) >= 2:
        by_tool_catch: dict[str, list[float]] = {}
        scoring = default_scoring()
        for s in scores:
            val = 1.0 if s.score >= scoring.catch_threshold else 0.0
            by_tool_catch.setdefault(s.tool, []).append(val)

        # Compute raw p-values for all unique pairs
        pair_keys: list[tuple[str, str]] = []
        raw_pvals: list[float] = []
        for i, ta in enumerate(tools_list):
            for tb in tools_list[i + 1 :]:
                p = permutation_p_value(by_tool_catch.get(ta, []), by_tool_catch.get(tb, []))
                pair_keys.append((ta, tb))
                raw_pvals.append(p)

        # Apply Benjamini-Hochberg FDR correction
        adj_pvals = benjamini_hochberg(raw_pvals)
        adj_lookup = {}
        for (ta, tb), adj_p in zip(pair_keys, adj_pvals):
            adj_lookup[(ta, tb)] = adj_p
            adj_lookup[(tb, ta)] = adj_p

        pairwise_lines = [
            "## Pairwise Detection Rate p-values (permutation test, Benjamini-Hochberg corrected)",
            "",
            "| | " + " | ".join(tools_list) + " |",
            "|" + "---|" * (len(tools_list) + 1),
        ]
        for ta in tools_list:
            row_cells = [ta]
            for tb in tools_list:
                if ta == tb:
                    row_cells.append("—")
                else:
                    row_cells.append(f"{adj_lookup[(ta, tb)]:.3f}")
            pairwise_lines.append("| " + " | ".join(row_cells) + " |")
        md_lines.append("\n".join(pairwise_lines))

    # Power note
    n_cases = max((m["count"] for m in agg.values()), default=0)
    md_lines.append(
        f"---\n\n**Power note**: at n={n_cases}, a 20-percentage-point difference in "
        "detection rate is detectable at α=0.10 but not α=0.05. "
        "Recommend n≥50 for definitive conclusions."
    )

    full_report = "\n\n".join(md_lines)
    (out_dir / "report.md").write_text(full_report)
    click.echo(f"Report \u2192 {out_dir / 'report.md'}")

    generate_csv(agg, out_dir / "scores.csv")
    click.echo(f"CSV \u2192 {out_dir / 'scores.csv'}")

    if not no_charts:
        if generate_charts(agg, out_dir):
            click.echo(f"Charts \u2192 {out_dir}/")
        else:
            click.echo("Charts skipped (matplotlib not installed)", err=True)

    click.echo("\n" + generate_markdown(agg))


@click.command("analyze")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Path to run directory (must contain scores/ subdirectory)",
)
@click.option(
    "--cases-dir",
    default="cases/",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Directory containing case YAML files (for dimensional slicing)",
)
@click.option("--no-charts", is_flag=True, default=False, help="Skip matplotlib chart generation")
def analyze(run_dir: str, cases_dir: str, no_charts: bool) -> None:
    """Aggregate judge scores into comparison tables and charts."""
    run_analyze(Path(run_dir), Path(cases_dir), no_charts)


def compare_runs_report(
    run_dirs: list[Path],
    cases_dir: Path,
) -> str:
    """Generate a cross-run comparison table."""
    rows: list[dict[str, object]] = []
    for rd in run_dirs:
        scores_dir = rd / "scores"
        if not scores_dir.exists():
            continue

        scores: list[JudgeScore] = []
        for p in sorted(scores_dir.glob("*.yaml")):
            data = yaml.safe_load(p.read_text()) or {}
            try:
                scores.append(JudgeScore(**data))
            except (ValueError, yaml.YAMLError):
                continue

        if not scores:
            continue

        # Read run metadata for condition label
        meta_path = rd / "run_metadata.json"
        label = rd.name
        if meta_path.exists():
            import json

            meta = json.loads(meta_path.read_text())
            ctx = meta.get("context_level", "")
            blind = meta.get("blind", False)
            label = f"{rd.name} ({ctx}" + (", blind" if blind else "") + ")"

        agg = aggregate_scores(scores)
        for tool, metrics in agg.items():
            rows.append(
                {
                    "run": label,
                    "tool": tool,
                    "cases": metrics["count"],
                    "catch_rate": metrics["catch_rate"],
                    "avg_score": metrics["avg_score"],
                    "avg_snr": metrics["avg_snr"],
                    "qap": metrics.get("avg_quality_adjusted_precision", 0.0),
                }
            )

    if not rows:
        return "No scored runs found."

    # Build markdown table
    lines = [
        "## Cross-Run Comparison",
        "",
        "| Run | Tool | Cases | Detection Rate | Avg Score | SNR | QAP |",
        "|-----|------|-------|--------------|-----------|-----|-----|",
    ]
    for r in rows:
        lines.append(
            f"| {r['run']} | {r['tool']} | {r['cases']} "
            f"| {r['catch_rate']:.1%} "
            f"| {r['avg_score']:.2f} "
            f"| {r['avg_snr']:.2f} "
            f"| {r['qap']:.2f} |"
        )
    return "\n".join(lines)


@click.command("compare-runs")
@click.option(
    "--run-dir",
    "run_dirs",
    multiple=True,
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Run directories to compare (specify multiple times)",
)
@click.option(
    "--cases-dir",
    default="cases/",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Cases directory",
)
def compare_runs(run_dirs: tuple[str, ...], cases_dir: str) -> None:
    """Compare scored results across multiple runs."""
    report = compare_runs_report(
        [Path(d) for d in run_dirs],
        Path(cases_dir),
    )
    click.echo(report)
