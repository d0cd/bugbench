"""Statistical analysis, comparison tables, and charts."""

from __future__ import annotations

import csv
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import click

from bugeval.io import load_cases, load_result, load_score
from bugeval.models import CaseKind, TestCase
from bugeval.result_models import ToolResult
from bugeval.score_models import CaseScore

_SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def load_scores(scores_dir: Path) -> list[CaseScore]:
    """Load all CaseScore YAMLs from a directory."""
    scores: list[CaseScore] = []
    for p in sorted(scores_dir.glob("*.yaml")):
        scores.append(load_score(p))
    return scores


def compute_catch_rate(scores: list[CaseScore]) -> float:
    """Fraction of cases where the bug was caught (detection_score >= 2)."""
    if not scores:
        return 0.0
    return sum(1 for s in scores if s.detection_score is not None and s.detection_score >= 2) / len(
        scores
    )


def mechanical_catch_rate(scores: list[CaseScore]) -> float:
    """Fraction of cases where mechanical matcher flagged a catch."""
    if not scores:
        return 0.0
    return sum(1 for s in scores if s.caught) / len(scores)


def bootstrap_ci(
    values: list[float],
    n_bootstrap: int = 10000,
    ci: float = 0.95,
) -> tuple[float, float]:
    """Bootstrap confidence interval on the mean of *values*."""
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    rng = random.Random(42)
    means: list[float] = []
    for _ in range(n_bootstrap):
        sample = [values[rng.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = 1 - ci
    lo_idx = int((alpha / 2) * n_bootstrap)
    hi_idx = int((1 - alpha / 2) * n_bootstrap) - 1
    return (means[lo_idx], means[hi_idx])


def permutation_test(
    group_a: list[float],
    group_b: list[float],
    n_permutations: int = 10000,
) -> float:
    """Two-sided permutation test for difference in means. Returns p-value."""
    combined = group_a + group_b
    na = len(group_a)
    obs_diff = abs(sum(group_a) / max(na, 1) - sum(group_b) / max(len(group_b), 1))
    rng = random.Random(42)
    count = 0
    for _ in range(n_permutations):
        rng.shuffle(combined)
        perm_a = combined[:na]
        perm_b = combined[na:]
        d = abs(sum(perm_a) / max(len(perm_a), 1) - sum(perm_b) / max(len(perm_b), 1))
        if d >= obs_diff:
            count += 1
    return count / n_permutations


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg FDR correction. Returns list of significance bools."""
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    significant = [False] * m
    # Find largest k where p_(k) <= k/m * alpha
    max_k = -1
    for rank_minus_1, (orig_idx, pval) in enumerate(indexed):
        k = rank_minus_1 + 1
        if pval <= (k / m) * alpha:
            max_k = rank_minus_1
    if max_k >= 0:
        for i in range(max_k + 1):
            significant[indexed[i][0]] = True
    return significant


def severity_weighted_catch_rate(scores: list[CaseScore], cases: list[TestCase]) -> float:
    """Catch rate weighted by severity (critical=4, high=3, medium=2, low=1)."""
    case_map = {c.id: c for c in cases}
    weighted_caught = 0.0
    weighted_total = 0.0
    for s in scores:
        c = case_map.get(s.case_id)
        if c is None or c.kind != CaseKind.bug:
            continue
        w = _SEVERITY_WEIGHTS.get(c.severity, 1)
        weighted_total += w
        if s.caught:
            weighted_caught += w
    if weighted_total == 0:
        return 0.0
    return weighted_caught / weighted_total


def median_localization_distance(scores: list[CaseScore]) -> float | None:
    """Median localization_distance for caught cases."""
    dists = [
        s.localization_distance for s in scores if s.caught and s.localization_distance is not None
    ]
    if not dists:
        return None
    return statistics.median(dists)


def false_alarm_rate(scores: list[CaseScore], cases: list[TestCase]) -> float:
    """Fraction of clean cases where false_alarm is True."""
    clean_ids = {c.id for c in cases if c.kind == CaseKind.clean}
    clean_scores = [s for s in scores if s.case_id in clean_ids]
    if not clean_scores:
        return 0.0
    return sum(1 for s in clean_scores if s.false_alarm) / len(clean_scores)


def signal_to_noise(scores: list[CaseScore]) -> float:
    """Useful comments / total comments. Also known as usefulness_rate."""
    useful = 0
    total = 0
    for s in scores:
        total += len(s.comment_scores)
        useful += s.tp_count
    if total == 0:
        return 0.0
    return useful / total


def signal_to_noise_inclusive(scores: list[CaseScore]) -> float:
    """Useful comments (including novel) / total comments."""
    useful = 0
    total = 0
    for s in scores:
        total += len(s.comment_scores)
        useful += s.tp_count + s.novel_count
    if total == 0:
        return 0.0
    return useful / total


def cost_per_bug(scores: list[CaseScore], results: list[ToolResult]) -> float | None:
    """Total cost / number of catches. None if no catches."""
    catches = sum(1 for s in scores if s.caught)
    if catches == 0:
        return None
    total_cost = sum(r.cost_usd for r in results)
    return total_cost / catches


def slice_scores(
    scores: list[CaseScore],
    cases: list[TestCase],
    dimension: str,
    value: str,
) -> list[CaseScore]:
    """Filter scores by a slicing dimension on the corresponding case."""
    case_map = {c.id: c for c in cases}
    result: list[CaseScore] = []
    for s in scores:
        c = case_map.get(s.case_id)
        if c is None:
            continue
        actual = _get_dimension(c, s, dimension)
        if actual == value:
            result.append(s)
    return result


def _get_dimension(case: TestCase, score: CaseScore, dim: str) -> str:
    if dim == "repo":
        return case.repo
    if dim == "category":
        return case.category
    if dim == "difficulty":
        return case.difficulty
    if dim == "severity":
        return case.severity
    if dim == "pr_size":
        return case.pr_size
    if dim == "blame_confidence":
        gt = case.truth
        return (gt.blame_confidence or "") if gt else ""
    if dim == "context_level":
        return score.context_level
    if dim == "issue_linked":
        return "yes" if case.linked_issues else "no"
    return ""


def tolerance_sensitivity(
    all_scores: dict[str, list[CaseScore]],
    all_results: dict[str, list[ToolResult]],
    cases: list[TestCase],
    tolerances: list[int] | None = None,
) -> dict[str, dict[int, float]]:
    """Compute mechanical catch rate at each tolerance level per tool."""
    from bugeval.score import mechanical_catch

    if tolerances is None:
        tolerances = [3, 5, 10, 15, 20]
    out: dict[str, dict[int, float]] = {}
    cases_by_id = {c.id: c for c in cases}
    for tool, results in all_results.items():
        sweep: dict[int, float] = {}
        for tol in tolerances:
            caught = 0
            total = 0
            for r in results:
                c = cases_by_id.get(r.case_id)
                if not c or c.kind != "bug" or not c.truth:
                    continue
                total += 1
                hit, _ = mechanical_catch(r, c.truth, tolerance=tol)
                if hit:
                    caught += 1
            sweep[tol] = caught / total if total else 0.0
        out[tool] = sweep
    return out


def build_comparison_table(
    all_scores: dict[str, list[CaseScore]],
    all_results: dict[str, list[ToolResult]],
    cases: list[TestCase],
) -> list[dict[str, Any]]:
    """Build the primary comparison table across tools."""
    table: list[dict[str, Any]] = []
    for tool, scores in sorted(all_scores.items()):
        results = all_results.get(tool, [])
        bug_scores = [
            s for s in scores if any(c.id == s.case_id and c.kind == CaseKind.bug for c in cases)
        ]
        cr = compute_catch_rate(bug_scores)
        catch_vals = [1.0 if s.caught else 0.0 for s in bug_scores]
        ci_lo, ci_hi = bootstrap_ci(catch_vals) if catch_vals else (0.0, 0.0)
        # For LLM-dependent metrics, exclude judge failures
        valid_scores = [s for s in scores if not s.judge_failed]
        quals = [s.review_quality for s in valid_scores]
        mean_q = sum(quals) / len(quals) if quals else 0.0
        far = false_alarm_rate(scores, cases)
        snr = signal_to_noise(scores)
        snr_incl = signal_to_noise_inclusive(scores)
        total_novel = sum(s.novel_count for s in scores)
        cpb = cost_per_bug(scores, results)
        # Precision: TP / (TP + FP)
        total_tp = sum(s.tp_count for s in scores)
        total_fp = sum(s.fp_count for s in scores)
        prec = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
        swcr = severity_weighted_catch_rate(scores, cases)
        med_loc = median_localization_distance(bug_scores)
        times = [r.time_seconds for r in results if r.time_seconds > 0]
        mean_time = sum(times) / len(times) if times else 0.0
        judge_costs = [s.judge_cost_usd for s in scores]
        total_judge = sum(judge_costs)
        judge_per_case = round(total_judge / len(scores), 6) if scores else 0.0
        total_tool_cost = sum(r.cost_usd for r in results)
        catches = sum(1 for s in bug_scores if s.caught)
        total_cost = total_tool_cost + total_judge
        total_cpb = round(total_cost / catches, 4) if catches else None
        table.append(
            {
                "tool": tool,
                "catch_rate": round(cr, 4),
                "ci_lower": round(ci_lo, 4),
                "ci_upper": round(ci_hi, 4),
                "severity_weighted_catch_rate": round(swcr, 4),
                "median_localization": med_loc,
                "mean_quality": round(mean_q, 2),
                "false_alarm_rate": round(far, 4),
                "precision": round(prec, 4),
                "snr": round(snr, 4),
                "novel_count": total_novel,
                "snr_inclusive": round(snr_incl, 4),
                "cost_per_bug": round(cpb, 4) if cpb is not None else None,
                "judge_cost_per_case": judge_per_case,
                "total_cost_per_bug": total_cpb,
                "mean_time_seconds": round(mean_time, 1),
            }
        )
    return table


def export_csv(table: list[dict[str, Any]], path: Path) -> None:
    """Write a comparison table to CSV."""
    if not table:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(table[0].keys()))
        writer.writeheader()
        writer.writerows(table)


def generate_charts(
    all_scores: dict[str, list[CaseScore]],
    cases: list[TestCase],
    output_dir: Path,
) -> None:
    """Generate matplotlib charts for comparison."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    tools = sorted(all_scores.keys())
    bug_case_ids = {c.id for c in cases if c.kind == CaseKind.bug}

    # 1. Catch rate bar chart
    rates = []
    for t in tools:
        bug_scores = [s for s in all_scores[t] if s.case_id in bug_case_ids]
        rates.append(compute_catch_rate(bug_scores))
    fig, ax = plt.subplots()
    ax.bar(tools, rates)
    ax.set_ylabel("Catch Rate")
    ax.set_title("Bug Catch Rate by Tool")
    ax.set_ylim(0, 1)
    fig.tight_layout()
    fig.savefig(charts_dir / "catch_rate.png", dpi=150)
    plt.close(fig)

    # 2. Detection score distribution (exclude judge failures)
    fig, ax = plt.subplots()
    for t in tools:
        det_scores = [s.detection_score for s in all_scores[t] if not s.judge_failed]
        ax.hist(det_scores, bins=range(5), alpha=0.5, label=t)
    ax.set_xlabel("Detection Score")
    ax.set_ylabel("Count")
    ax.set_title("Detection Score Distribution")
    ax.legend()
    fig.tight_layout()
    fig.savefig(charts_dir / "detection_dist.png", dpi=150)
    plt.close(fig)


def run_analysis(run_dir: Path, cases_dir: Path, no_charts: bool) -> None:
    """Load scores and results, build table, export CSV, optionally chart."""
    scores_dir = run_dir / "scores"
    results_dir = run_dir / "results"

    all_raw_scores = load_scores(scores_dir)
    cases = load_cases(cases_dir)

    # Group by tool
    all_scores: dict[str, list[CaseScore]] = defaultdict(list)
    for s in all_raw_scores:
        all_scores[s.tool].append(s)

    all_results: dict[str, list[ToolResult]] = defaultdict(list)
    if results_dir.exists():
        for p in sorted(results_dir.glob("*.yaml")):
            r = load_result(p)
            all_results[r.tool].append(r)

    table = build_comparison_table(dict(all_scores), dict(all_results), cases)
    export_csv(table, run_dir / "comparison.csv")

    # Print to stdout
    if table:
        header = list(table[0].keys())
        click.echo("\t".join(header))
        for row in table:
            click.echo("\t".join(str(row[k]) for k in header))

    # Judge failure reporting
    all_scores_flat = [s for scores in all_scores.values() for s in scores]
    judge_failures = sum(1 for s in all_scores_flat if s.judge_failed)
    if judge_failures:
        click.echo(f"\nWarning: {judge_failures} cases had LLM judge failures (scored as 0/0)")

    # Contamination reporting
    contaminated = [s for s in all_scores_flat if s.potentially_contaminated]
    if contaminated:
        click.echo(f"\nWarning: {len(contaminated)} potentially contaminated results")
        for s in contaminated:
            click.echo(f"  {s.case_id} ({s.tool})")

    # Per-dimension slice analysis
    cases_by_id = {c.id: c for c in cases}
    dimensions = [
        "repo",
        "category",
        "difficulty",
        "severity",
        "pr_size",
        "blame_confidence",
        "context_level",
        "issue_linked",
    ]
    all_slice_p_values: list[float] = []
    all_slice_labels: list[str] = []
    for dim in dimensions:
        values: set[str] = set()
        for s in all_scores_flat:
            c = cases_by_id.get(s.case_id)
            if c:
                values.add(_get_dimension(c, s, dim))
        sorted_vals = sorted(v for v in values if v)
        sliced_groups: dict[str, list[CaseScore]] = {}
        for val in sorted_vals:
            sliced = slice_scores(all_scores_flat, cases, dim, val)
            if sliced:
                rate = compute_catch_rate(sliced)
                click.echo(f"  {dim}={val}: catch_rate={rate:.2%} (n={len(sliced)})")
                sliced_groups[val] = sliced
        # Pairwise permutation tests within this dimension
        group_keys = sorted(sliced_groups.keys())
        for i, k1 in enumerate(group_keys):
            for k2 in group_keys[i + 1 :]:
                g1 = [1.0 if s.caught else 0.0 for s in sliced_groups[k1]]
                g2 = [1.0 if s.caught else 0.0 for s in sliced_groups[k2]]
                if g1 and g2:
                    p = permutation_test(g1, g2)
                    all_slice_p_values.append(p)
                    all_slice_labels.append(f"{dim}: {k1} vs {k2}")

    if all_slice_p_values:
        sig_flags = benjamini_hochberg(all_slice_p_values)
        sig_pairs = [
            (lbl, pv) for lbl, pv, sf in zip(all_slice_labels, all_slice_p_values, sig_flags) if sf
        ]
        if sig_pairs:
            click.echo("\n--- Significant Slice Differences (BH-corrected) ---")
            for lbl, pv in sig_pairs:
                click.echo(f"  {lbl}: p={pv:.4f} *")

    # High-confidence analysis (tier A/B only)
    high_conf_scores: dict[str, list[CaseScore]] = {}
    if cases:
        for tool, scores in all_scores.items():
            filtered = [
                s
                for s in scores
                if _get_dimension(
                    cases_by_id.get(s.case_id) or cases[0],
                    s,
                    "blame_confidence",
                )
                in ("A", "B")
                and s.case_id in cases_by_id
            ]
            if filtered:
                high_conf_scores[tool] = filtered

    if high_conf_scores:
        hc_results = {t: all_results.get(t, []) for t in high_conf_scores}
        click.echo("\n--- High-Confidence Cases Only (Tier A/B) ---")
        hc_table = build_comparison_table(high_conf_scores, hc_results, cases)
        for row in hc_table:
            click.echo(
                f"  {row['tool']}: catch={row['catch_rate']:.2%}"
                f"  quality={row['mean_quality']}"
                f"  precision={row['precision']:.2%}"
            )

    # Contamination impact analysis
    contaminated = {
        t: [s for s in scores if s.potentially_contaminated] for t, scores in all_scores.items()
    }
    clean = {
        t: [s for s in scores if not s.potentially_contaminated] for t, scores in all_scores.items()
    }
    has_contamination = any(len(v) > 0 for v in contaminated.values())
    if has_contamination:
        click.echo("\n--- Contamination Impact ---")
        for tool in sorted(all_scores):
            c_count = len(contaminated[tool])
            total = len(all_scores[tool])
            if c_count:
                clean_rate = compute_catch_rate(clean[tool])
                full_rate = compute_catch_rate(all_scores[tool])
                click.echo(
                    f"  {tool}: {c_count}/{total} contaminated,"
                    f" catch_rate {full_rate:.0%} (all)"
                    f" vs {clean_rate:.0%} (clean only)"
                )

    # Tolerance sensitivity
    if all_results:
        click.echo("\n--- Tolerance Sensitivity ---")
        sweep = tolerance_sensitivity(dict(all_scores), dict(all_results), cases)
        for tool, rates in sorted(sweep.items()):
            row = "  ".join(f"\u00b1{t}={r:.0%}" for t, r in sorted(rates.items()))
            click.echo(f"  {tool}: {row}")

    # Pairwise tool comparisons
    tool_names = sorted(all_scores.keys())
    if len(tool_names) >= 2:
        click.echo("\n--- Pairwise Comparisons (Permutation Test) ---")
        p_values: list[float] = []
        pairs: list[tuple[str, str]] = []
        for i, t1 in enumerate(tool_names):
            for t2 in tool_names[i + 1 :]:
                catches_1 = [1.0 if s.caught else 0.0 for s in all_scores[t1]]
                catches_2 = [1.0 if s.caught else 0.0 for s in all_scores[t2]]
                p = permutation_test(catches_1, catches_2)
                p_values.append(p)
                pairs.append((t1, t2))

        significant = benjamini_hochberg(p_values)
        for (t1, t2), p, sig in zip(pairs, p_values, significant):
            marker = " *" if sig else ""
            click.echo(f"  {t1} vs {t2}: p={p:.4f}{marker}")

    if not no_charts:
        generate_charts(dict(all_scores), cases, run_dir)
