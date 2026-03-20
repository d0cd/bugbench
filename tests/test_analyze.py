# tests/test_analyze.py
"""Tests for analyze module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bugeval.analyze import (
    aggregate_scores,
    benjamini_hochberg,
    compute_avg_quality_adjusted_precision,
    compute_catch_rate,
    compute_snr,
    generate_csv,
    generate_markdown,
)
from bugeval.judge_models import JudgeScore, NoiseStats
from bugeval.models import (
    Category,
    Difficulty,
    ExpectedFinding,
    PRSize,
    Severity,
    TestCase,
    Visibility,
)


def _make_scores(data: list[tuple[str, str, int, float]]) -> list[JudgeScore]:
    """Helper: (case_id, tool, score, snr) → list[JudgeScore]."""
    return [
        JudgeScore(
            test_case_id=cid,
            tool=tool,
            score=score,
            votes=[score, score, score],
            reasoning="test",
            noise=NoiseStats(total_comments=4, true_positives=int(snr * 4), snr=snr),
        )
        for cid, tool, score, snr in data
    ]


def test_compute_catch_rate_basic() -> None:
    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 0, 0.0),
            ("c3", "greptile", 3, 1.0),
        ]
    )
    rate = compute_catch_rate(scores)
    assert rate == pytest.approx(2 / 3)


def test_compute_catch_rate_empty() -> None:
    assert compute_catch_rate([]) == 0.0


def test_compute_snr_average() -> None:
    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 1, 0.25),
        ]
    )
    assert compute_snr(scores) == pytest.approx(0.375)


def test_aggregate_scores_groups_by_tool() -> None:
    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "coderabbit", 3, 1.0),
            ("c3", "greptile", 0, 0.0),
        ]
    )
    agg = aggregate_scores(scores)
    assert set(agg.keys()) == {"greptile", "coderabbit"}
    assert agg["greptile"]["catch_rate"] == pytest.approx(0.5)
    assert agg["coderabbit"]["catch_rate"] == pytest.approx(1.0)
    assert agg["greptile"]["count"] == 2


def test_generate_csv(tmp_path: Path) -> None:
    scores = _make_scores([("c1", "greptile", 2, 0.5)])
    agg = aggregate_scores(scores)
    path = tmp_path / "scores.csv"
    generate_csv(agg, path)
    content = path.read_text()
    assert "greptile" in content
    assert "catch_rate" in content


def test_generate_markdown(tmp_path: Path) -> None:
    scores = _make_scores([("c1", "greptile", 2, 0.5)])
    agg = aggregate_scores(scores)
    md = generate_markdown(agg)
    assert "| Tool |" in md
    assert "greptile" in md


# ---- Phase 8 additions: slicing + cost metrics ----


def _make_case(
    case_id: str = "case-001",
    category: str = "logic",
    difficulty: str = "medium",
    severity: str = "high",
    pr_size: str = "small",
    language: str = "rust",
) -> TestCase:
    return TestCase(
        id=case_id,
        repo="org/repo",
        base_commit="abc",
        head_commit="def",
        fix_commit="ghi",
        category=Category(category),
        difficulty=Difficulty(difficulty),
        severity=Severity(severity),
        language=language,
        pr_size=PRSize(pr_size),
        description="test case",
        expected_findings=[ExpectedFinding(file="a.rs", line=1, summary="bug")],
    )


def test_load_cases_lookup(tmp_path: Path) -> None:
    from bugeval.analyze import load_cases_lookup

    case = _make_case("case-001")
    (tmp_path / "case-001.yaml").write_text(
        yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False)
    )
    lookup = load_cases_lookup(tmp_path)
    assert "case-001" in lookup
    assert lookup["case-001"].category.value == "logic"


def test_load_cases_lookup_empty_dir(tmp_path: Path) -> None:
    from bugeval.analyze import load_cases_lookup

    assert load_cases_lookup(tmp_path) == {}


def test_load_normalized_lookup(tmp_path: Path) -> None:
    from bugeval.analyze import load_normalized_lookup
    from bugeval.result_models import NormalizedResult, ResultMetadata

    r = NormalizedResult(
        test_case_id="case-001",
        tool="greptile",
        context_level="diff-only",
        metadata=ResultMetadata(cost_usd=0.05),
    )
    (tmp_path / "case-001-greptile.yaml").write_text(
        yaml.safe_dump(r.model_dump(mode="json"), sort_keys=False)
    )
    lookup = load_normalized_lookup(tmp_path)
    assert ("case-001", "greptile") in lookup
    assert lookup[("case-001", "greptile")].metadata.cost_usd == pytest.approx(0.05)


def test_slice_scores_by_dimension() -> None:
    from bugeval.analyze import slice_scores

    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 0, 0.0),
        ]
    )
    cases = {
        "c1": _make_case("c1", difficulty="easy"),
        "c2": _make_case("c2", difficulty="hard"),
    }
    groups = slice_scores(scores, cases, "difficulty")
    assert set(groups.keys()) == {"easy", "hard"}
    assert len(groups["easy"]) == 1
    assert len(groups["hard"]) == 1


def test_slice_scores_unknown_case() -> None:
    from bugeval.analyze import slice_scores

    scores = _make_scores([("missing-case", "greptile", 2, 0.5)])
    groups = slice_scores(scores, {}, "difficulty")
    assert "unknown" in groups


def test_slice_scores_by_context() -> None:
    from bugeval.analyze import slice_scores_by_context
    from bugeval.result_models import NormalizedResult

    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 0, 0.0),
        ]
    )
    results = {
        ("c1", "greptile"): NormalizedResult(
            test_case_id="c1", tool="greptile", context_level="diff-only"
        ),
        ("c2", "greptile"): NormalizedResult(
            test_case_id="c2", tool="greptile", context_level="diff+repo"
        ),
    }
    groups = slice_scores_by_context(scores, results)
    assert set(groups.keys()) == {"diff-only", "diff+repo"}


def test_compute_cost_per_tool() -> None:
    from bugeval.analyze import compute_cost_per_tool
    from bugeval.result_models import NormalizedResult, ResultMetadata

    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 0, 0.0),
        ]
    )
    results = {
        ("c1", "greptile"): NormalizedResult(
            test_case_id="c1",
            tool="greptile",
            metadata=ResultMetadata(cost_usd=0.10),
        ),
        ("c2", "greptile"): NormalizedResult(
            test_case_id="c2",
            tool="greptile",
            metadata=ResultMetadata(cost_usd=0.05),
        ),
    }
    cost = compute_cost_per_tool(scores, results)
    assert cost["greptile"]["total_cost_usd"] == pytest.approx(0.15)
    assert cost["greptile"]["cost_per_review"] == pytest.approx(0.075)
    assert cost["greptile"]["cost_per_detection"] == pytest.approx(0.15)


def test_slice_scores_by_visibility() -> None:
    from bugeval.analyze import slice_scores

    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 0, 0.0),
        ]
    )
    cases = {
        "c1": TestCase(
            id="c1",
            repo="org/repo",
            base_commit="abc",
            head_commit="def",
            fix_commit="ghi",
            category=Category("logic"),
            difficulty=Difficulty("medium"),
            severity=Severity("high"),
            language="rust",
            pr_size=PRSize("small"),
            description="test",
            expected_findings=[ExpectedFinding(file="a.rs", line=1, summary="bug")],
            visibility=Visibility.public,
        ),
        "c2": TestCase(
            id="c2",
            repo="org/repo",
            base_commit="abc",
            head_commit="def",
            fix_commit="ghi",
            category=Category("logic"),
            difficulty=Difficulty("medium"),
            severity=Severity("high"),
            language="rust",
            pr_size=PRSize("small"),
            description="test",
            expected_findings=[ExpectedFinding(file="a.rs", line=1, summary="bug")],
            visibility=Visibility.private,
        ),
    }
    groups = slice_scores(scores, cases, "visibility")
    assert set(groups.keys()) == {"public", "private"}
    assert len(groups["public"]) == 1
    assert len(groups["private"]) == 1


def test_generate_dx_markdown() -> None:
    from bugeval.analyze import generate_dx_markdown
    from bugeval.result_models import DxAssessment, NormalizedResult

    results = {
        ("c1", "greptile"): NormalizedResult(
            test_case_id="c1",
            tool="greptile",
            dx=DxAssessment(
                actionability=4, false_positive_burden=2, integration_friction=3, response_latency=5
            ),
        ),
    }
    md = generate_dx_markdown(results)
    assert "DX Assessment" in md
    assert "greptile" in md
    assert "4.0" in md


def test_analyze_skips_dx_when_absent() -> None:
    from bugeval.analyze import generate_dx_markdown
    from bugeval.result_models import NormalizedResult

    results = {
        ("c1", "greptile"): NormalizedResult(test_case_id="c1", tool="greptile"),
    }
    md = generate_dx_markdown(results)
    assert md == ""


def test_generate_slice_markdown() -> None:
    from bugeval.analyze import generate_slice_markdown

    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 0, 0.0),
        ]
    )
    cases = {
        "c1": _make_case("c1", difficulty="easy"),
        "c2": _make_case("c2", difficulty="hard"),
    }
    md = generate_slice_markdown(scores, cases, "difficulty")
    assert "difficulty" in md.lower()
    assert "easy" in md
    assert "hard" in md
    assert "greptile" in md


def test_generate_confidence_band_markdown_basic() -> None:
    from bugeval.analyze import generate_confidence_band_markdown
    from bugeval.result_models import Comment, NormalizedResult

    scores = _make_scores(
        [
            ("c1", "anthropic-api", 2, 0.5),
            ("c2", "anthropic-api", 0, 0.0),
            ("c3", "anthropic-api", 3, 1.0),
        ]
    )
    results: dict = {
        ("c1", "anthropic-api"): NormalizedResult(
            test_case_id="c1",
            tool="anthropic-api",
            comments=[Comment(body="b", confidence=0.6)],
        ),
        ("c2", "anthropic-api"): NormalizedResult(
            test_case_id="c2",
            tool="anthropic-api",
            comments=[Comment(body="b", confidence=0.8)],
        ),
        ("c3", "anthropic-api"): NormalizedResult(
            test_case_id="c3",
            tool="anthropic-api",
            comments=[Comment(body="b", confidence=0.95)],
        ),
    }
    md = generate_confidence_band_markdown(scores, results)
    assert "Confidence Band" in md
    assert "0.5" in md or "[0.5" in md


def test_slice_scores_by_verified() -> None:
    from bugeval.analyze import slice_scores

    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 0, 0.0),
        ]
    )
    cases = {
        "c1": TestCase(
            id="c1",
            repo="org/repo",
            base_commit="abc",
            head_commit="def",
            fix_commit="ghi",
            category=Category("logic"),
            difficulty=Difficulty("medium"),
            severity=Severity("high"),
            language="rust",
            pr_size=PRSize("small"),
            description="test",
            expected_findings=[ExpectedFinding(file="a.rs", line=1, summary="bug")],
            verified=True,
        ),
        "c2": TestCase(
            id="c2",
            repo="org/repo",
            base_commit="abc",
            head_commit="def",
            fix_commit="ghi",
            category=Category("logic"),
            difficulty=Difficulty("medium"),
            severity=Severity("high"),
            language="rust",
            pr_size=PRSize("small"),
            description="test",
            expected_findings=[ExpectedFinding(file="a.rs", line=1, summary="bug")],
            verified=False,
        ),
    }
    groups = slice_scores(scores, cases, "verified")
    assert set(groups.keys()) == {"True", "False"}
    assert len(groups["True"]) == 1
    assert len(groups["False"]) == 1


# ---------------------------------------------------------------------------
# Benjamini-Hochberg FDR correction
# ---------------------------------------------------------------------------


def test_benjamini_hochberg_no_pvalues() -> None:
    assert benjamini_hochberg([]) == []


def test_benjamini_hochberg_single() -> None:
    assert benjamini_hochberg([0.05]) == [0.05]


def test_benjamini_hochberg_already_significant() -> None:
    """All p-values well below threshold — should remain significant."""
    raw = [0.001, 0.002, 0.003]
    adjusted = benjamini_hochberg(raw)
    assert len(adjusted) == 3
    for p in adjusted:
        assert p < 0.05


def test_benjamini_hochberg_corrects_upward() -> None:
    """Marginal p-values should be adjusted upward."""
    raw = [0.01, 0.04, 0.05]
    adjusted = benjamini_hochberg(raw)
    # BH: sort, then work backwards enforcing monotonicity
    # rank=3: 0.05 * 3/3 = 0.05
    # rank=2: min(0.04 * 3/2, 0.05) = min(0.06, 0.05) = 0.05
    # rank=1: min(0.01 * 3/1, 0.05) = min(0.03, 0.05) = 0.03
    assert adjusted[0] == pytest.approx(0.03)
    assert adjusted[1] == pytest.approx(0.05)
    assert adjusted[2] == pytest.approx(0.05)


def test_benjamini_hochberg_capped_at_one() -> None:
    """Adjusted p-values should never exceed 1.0."""
    raw = [0.5, 0.8, 0.9]
    adjusted = benjamini_hochberg(raw)
    for p in adjusted:
        assert p <= 1.0


def test_benjamini_hochberg_preserves_order() -> None:
    """Adjusted p-values preserve input order (not sorted order)."""
    raw = [0.05, 0.01, 0.03]
    adjusted = benjamini_hochberg(raw)
    # The smallest raw p-value (0.01 at index 1) should have smallest adjusted
    assert adjusted[1] < adjusted[0]
    assert adjusted[1] < adjusted[2]


def test_generate_confidence_band_markdown_empty_when_no_confidence() -> None:
    """When no comments have confidence data, returns empty string."""
    from bugeval.analyze import generate_confidence_band_markdown
    from bugeval.result_models import Comment, NormalizedResult

    scores = _make_scores([("c1", "greptile", 2, 0.5)])
    results: dict = {
        ("c1", "greptile"): NormalizedResult(
            test_case_id="c1",
            tool="greptile",
            comments=[Comment(body="no confidence here")],
        ),
    }
    md = generate_confidence_band_markdown(scores, results)
    assert md == ""


def test_bootstrap_ci_is_reproducible() -> None:
    """bootstrap_ci must return the same result on repeated calls."""
    from bugeval.analyze import bootstrap_ci

    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    a = bootstrap_ci(values)
    b = bootstrap_ci(values)
    assert a == b, f"Non-deterministic: {a} != {b}"


def test_permutation_p_value_is_reproducible() -> None:
    """permutation_p_value must return the same result on repeated calls."""
    from bugeval.analyze import permutation_p_value

    a_vals = [1.0, 2.0, 3.0]
    b_vals = [4.0, 5.0, 6.0]
    p1 = permutation_p_value(a_vals, b_vals)
    p2 = permutation_p_value(a_vals, b_vals)
    assert p1 == p2, f"Non-deterministic: {p1} != {p2}"


# ---------------------------------------------------------------------------
# Review quality and precision metrics
# ---------------------------------------------------------------------------


def test_aggregate_scores_includes_qap() -> None:
    scores = [
        JudgeScore(
            test_case_id="c1",
            tool="t1",
            score=2,
            votes=[2],
            reasoning="ok",
            noise=NoiseStats(
                total_comments=3,
                true_positives=1,
                novel_findings=1,
                weighted_signal=4.2,
                actionability_rate=0.5,
            ),
        ),
        JudgeScore(
            test_case_id="c2",
            tool="t1",
            score=0,
            votes=[0],
            reasoning="missed",
            noise=NoiseStats(
                total_comments=2,
                true_positives=0,
                novel_findings=1,
                weighted_signal=1.2,
                actionability_rate=0.0,
            ),
        ),
    ]
    agg = aggregate_scores(scores)
    assert "avg_quality_adjusted_precision" in agg["t1"]
    # QAP: (4.2/3 + 1.2/2) / 2 = (1.4 + 0.6) / 2 = 1.0
    assert agg["t1"]["avg_quality_adjusted_precision"] == pytest.approx(1.0)
    assert "avg_weighted_signal" in agg["t1"]
    assert "avg_actionability_rate" in agg["t1"]
    assert "avg_noise_ratio" in agg["t1"]


def test_aggregate_scores_includes_precision() -> None:
    scores = [
        JudgeScore(
            test_case_id="c1",
            tool="t1",
            score=2,
            votes=[2],
            reasoning="ok",
            noise=NoiseStats(
                total_comments=4,
                true_positives=1,
                novel_findings=1,
                false_positives=1,
                low_value=1,
                snr=0.5,
            ),
        ),
    ]
    agg = aggregate_scores(scores)
    assert "avg_precision" in agg["t1"]
    assert agg["t1"]["avg_precision"] == pytest.approx(0.5)


def test_compute_avg_quality_adjusted_precision() -> None:
    scores = [
        JudgeScore(
            test_case_id="c1",
            tool="t1",
            score=0,
            votes=[0],
            reasoning="",
            noise=NoiseStats(total_comments=2, weighted_signal=4.0),
        ),
        JudgeScore(
            test_case_id="c2",
            tool="t1",
            score=0,
            votes=[0],
            reasoning="",
            noise=NoiseStats(total_comments=4, weighted_signal=2.0),
        ),
    ]
    # QAP: (4.0/2 + 2.0/4) / 2 = (2.0 + 0.5) / 2 = 1.25
    assert compute_avg_quality_adjusted_precision(scores) == pytest.approx(1.25)


def test_compute_avg_quality_adjusted_precision_empty() -> None:
    assert compute_avg_quality_adjusted_precision([]) == 0.0


def test_compute_snr_conservative() -> None:
    from bugeval.analyze import compute_snr_conservative

    scores = [
        JudgeScore(
            test_case_id="c1",
            tool="t1",
            score=2,
            votes=[2],
            reasoning="",
            noise=NoiseStats(
                total_comments=10,
                true_positives=3,
                novel_findings=2,
                false_positives=3,
                low_value=2,
            ),
        ),
    ]
    # Conservative: tp_expected / total = 3/10 = 0.3
    assert compute_snr_conservative(scores) == pytest.approx(0.3)


def test_compute_snr_conservative_empty() -> None:
    from bugeval.analyze import compute_snr_conservative

    assert compute_snr_conservative([]) == 0.0


def test_compare_runs_report(tmp_path: Path) -> None:
    from bugeval.analyze import compare_runs_report
    from bugeval.io import save_case

    # Set up a cases dir
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    save_case(_make_case("c1"), cases_dir / "c1.yaml")

    # Set up a run with scores
    run1 = tmp_path / "run-1"
    scores_dir = run1 / "scores"
    scores_dir.mkdir(parents=True)
    score = JudgeScore(
        test_case_id="c1",
        tool="t1",
        score=2,
        votes=[2],
        reasoning="ok",
        noise=NoiseStats(total_comments=5, true_positives=2, snr=0.4),
    )
    (scores_dir / "c1-t1.yaml").write_text(
        yaml.safe_dump(score.model_dump(mode="json"), sort_keys=False)
    )

    report = compare_runs_report([run1], cases_dir)
    assert "Cross-Run Comparison" in report
    assert "t1" in report
    assert "run-1" in report


def test_compare_runs_help() -> None:
    from click.testing import CliRunner

    from bugeval.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["compare-runs", "--help"])
    assert result.exit_code == 0
    assert "--run-dir" in result.output


def test_generate_fp_analysis_markdown_with_clean_cases() -> None:
    from bugeval.analyze import generate_fp_analysis_markdown

    clean = _make_case("clean-001")
    clean = clean.model_copy(update={"expected_findings": [], "case_type": "clean"})
    cases = {"clean-001": clean}
    scores = [
        JudgeScore(
            test_case_id="clean-001",
            tool="tool-a",
            score=0,
            votes=[0],
            reasoning="",
            noise=NoiseStats(
                total_comments=5,
                true_positives=0,
                novel_findings=1,
                false_positives=3,
                low_value=1,
            ),
        ),
    ]
    md = generate_fp_analysis_markdown(scores, cases)
    assert "False Positive Analysis" in md
    assert "tool-a" in md
    assert "60.0%" in md  # 3 FP / 5 total


def test_generate_fp_analysis_markdown_empty_without_clean() -> None:
    from bugeval.analyze import generate_fp_analysis_markdown

    cases = {"fix-001": _make_case("fix-001")}
    scores = [
        JudgeScore(
            test_case_id="fix-001",
            tool="tool-a",
            score=2,
            votes=[2],
            reasoning="",
        ),
    ]
    md = generate_fp_analysis_markdown(scores, cases)
    assert md == ""
