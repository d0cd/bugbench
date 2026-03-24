"""Tests for analyze module."""

from __future__ import annotations

from pathlib import Path

from bugeval.analyze import (
    benjamini_hochberg,
    bootstrap_ci,
    build_comparison_table,
    compute_catch_rate,
    cost_per_bug,
    export_csv,
    false_alarm_rate,
    load_scores,
    mechanical_catch_rate,
    median_localization_distance,
    permutation_test,
    run_analysis,
    severity_weighted_catch_rate,
    signal_to_noise,
    signal_to_noise_inclusive,
    slice_scores,
    tolerance_sensitivity,
)
from bugeval.io import save_score
from bugeval.models import BuggyLine, CaseKind, GroundTruth, TestCase
from bugeval.result_models import Comment, ToolResult
from bugeval.score_models import CaseScore, CommentScore, CommentVerdict


def _bug_case(case_id: str, repo: str = "R/a", severity: str = "medium") -> TestCase:
    return TestCase(
        id=case_id,
        repo=repo,
        kind=CaseKind.bug,
        base_commit="abc",
        severity=severity,
        truth=GroundTruth(buggy_lines=[]),
    )


def _clean_case(case_id: str, repo: str = "R/a") -> TestCase:
    return TestCase(id=case_id, repo=repo, kind=CaseKind.clean, base_commit="abc")


def _score(
    case_id: str,
    tool: str = "copilot",
    caught: bool = False,
    loc_dist: int | None = None,
    det: int = 0,
    quality: int = 0,
    tp: int = 0,
    fp: int = 0,
    novel: int = 0,
    false_al: bool = False,
) -> CaseScore:
    scores: list[CommentScore] = []
    for i in range(tp):
        scores.append(CommentScore(comment_index=i, verdict=CommentVerdict.tp))
    for i in range(novel):
        scores.append(CommentScore(comment_index=tp + i, verdict=CommentVerdict.tp_novel))
    for i in range(fp):
        scores.append(CommentScore(comment_index=tp + novel + i, verdict=CommentVerdict.fp))
    return CaseScore(
        case_id=case_id,
        tool=tool,
        caught=caught,
        localization_distance=loc_dist,
        detection_score=det,
        review_quality=quality,
        tp_count=tp,
        fp_count=fp,
        novel_count=novel,
        false_alarm=false_al,
        comment_scores=scores,
    )


class TestComputeCatchRate:
    def test_basic(self) -> None:
        scores = [
            _score("a", det=2),
            _score("b", det=3),
            _score("c", det=2),
            _score("d", det=1),
            _score("e", det=0),
        ]
        assert compute_catch_rate(scores) == 0.6

    def test_empty(self) -> None:
        assert compute_catch_rate([]) == 0.0

    def test_none_detection_score(self) -> None:
        scores = [_score("a", det=0), _score("b", det=0)]
        assert compute_catch_rate(scores) == 0.0


class TestBootstrapCI:
    def test_returns_interval(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        lo, hi = bootstrap_ci(values, n_bootstrap=5000)
        mean = sum(values) / len(values)
        assert lo <= mean <= hi

    def test_all_same(self) -> None:
        lo, hi = bootstrap_ci([1.0, 1.0, 1.0, 1.0], n_bootstrap=1000)
        assert lo == 1.0
        assert hi == 1.0


class TestPermutationTest:
    def test_identical_groups(self) -> None:
        g = [1.0, 2.0, 3.0, 4.0, 5.0]
        p = permutation_test(g, g, n_permutations=5000)
        assert p > 0.05

    def test_different_groups(self) -> None:
        a = [100.0, 101.0, 102.0, 103.0, 104.0]
        b = [0.0, 1.0, 2.0, 3.0, 4.0]
        p = permutation_test(a, b, n_permutations=5000)
        assert p < 0.05


class TestBenjaminiHochberg:
    def test_known(self) -> None:
        pvals = [0.001, 0.01, 0.04, 0.06, 0.5]
        sig = benjamini_hochberg(pvals, alpha=0.05)
        # Rank 1: 0.001 <= 0.01, Rank 2: 0.01 <= 0.02, Rank 3: 0.04 > 0.03
        assert sig[0] is True
        assert sig[1] is True
        assert sig[2] is False
        assert sig[3] is False
        assert sig[4] is False


class TestSeverityWeightedCatchRate:
    def test_weighting(self) -> None:
        cases = [
            _bug_case("a", severity="critical"),
            _bug_case("b", severity="low"),
        ]
        scores = [
            _score("a", caught=True),  # weight=4
            _score("b", caught=False),  # weight=1
        ]
        rate = severity_weighted_catch_rate(scores, cases)
        # 4 / (4+1) = 0.8
        assert abs(rate - 0.8) < 1e-9


class TestMedianLocalizationDistance:
    def test_basic(self) -> None:
        scores = [
            _score("a", caught=True, loc_dist=2),
            _score("b", caught=True, loc_dist=4),
            _score("c", caught=True, loc_dist=6),
            _score("d", caught=False),
        ]
        assert median_localization_distance(scores) == 4.0

    def test_no_catches(self) -> None:
        assert median_localization_distance([_score("a")]) is None


class TestUsefulnessRate:
    def test_ratio(self) -> None:
        scores = [
            _score("a", tp=2, novel=1, fp=2),  # 2 useful / 5 total
            _score("b", tp=1, fp=1),  # 1 useful / 2 total
        ]
        # 3 useful / 7 total (novel excluded from signal_to_noise)
        assert abs(signal_to_noise(scores) - 3 / 7) < 1e-9


class TestFalseAlarmRate:
    def test_rate(self) -> None:
        # We need cases to know which are clean, but false_alarm_rate uses
        # the false_alarm field on CaseScore. We'll mark some as false_alarm.
        scores = [
            _score("clean-1", false_al=True),
            _score("clean-2", false_al=False),
            _score("clean-3", false_al=True),
            _score("bug-1"),  # bug case, not counted
        ]
        cases = [
            _clean_case("clean-1"),
            _clean_case("clean-2"),
            _clean_case("clean-3"),
            _bug_case("bug-1"),
        ]
        # 2/3 clean cases with false alarm
        rate = false_alarm_rate(scores, cases)
        assert abs(rate - 2 / 3) < 1e-9


class TestSignalToNoise:
    def test_ratio(self) -> None:
        scores = [
            _score("a", tp=3, novel=1, fp=2),  # 3 useful / 6
            _score("b", tp=0, fp=2),  # 0 useful / 2
        ]
        # 3 / 8 (novel excluded)
        assert abs(signal_to_noise(scores) - 3 / 8) < 1e-9


class TestSignalToNoiseInclusive:
    def test_includes_novel(self) -> None:
        scores = [
            _score("a", tp=3, novel=1, fp=2),  # 4 useful / 6
            _score("b", tp=0, fp=2),  # 0 useful / 2
        ]
        # 4 / 8 (novel included)
        assert abs(signal_to_noise_inclusive(scores) - 0.5) < 1e-9

    def test_empty(self) -> None:
        assert signal_to_noise_inclusive([]) == 0.0


class TestMechanicalCatchRate:
    def test_basic(self) -> None:
        scores = [
            _score("a", caught=True),
            _score("b", caught=True),
            _score("c", caught=True),
            _score("d"),
            _score("e"),
        ]
        assert mechanical_catch_rate(scores) == 0.6

    def test_empty(self) -> None:
        assert mechanical_catch_rate([]) == 0.0


class TestCostPerBug:
    def test_basic(self) -> None:
        scores = [
            _score("a", caught=True),
            _score("b", caught=True),
            _score("c"),
        ]
        results = [
            ToolResult(case_id="a", tool="t", cost_usd=10.0),
            ToolResult(case_id="b", tool="t", cost_usd=20.0),
            ToolResult(case_id="c", tool="t", cost_usd=30.0),
        ]
        cpb = cost_per_bug(scores, results)
        assert cpb is not None
        assert abs(cpb - 30.0) < 1e-9  # 60/2

    def test_no_catches(self) -> None:
        assert cost_per_bug([_score("a")], [ToolResult(case_id="a", tool="t")]) is None


class TestSliceScores:
    def test_by_repo(self) -> None:
        cases = [_bug_case("a", repo="R/x"), _bug_case("b", repo="R/y")]
        scores = [_score("a"), _score("b")]
        sliced = slice_scores(scores, cases, "repo", "R/x")
        assert len(sliced) == 1
        assert sliced[0].case_id == "a"


class TestBuildComparisonTable:
    def test_columns(self) -> None:
        cases = [_bug_case("a"), _clean_case("b")]
        all_scores = {
            "copilot": [
                _score("a", tool="copilot", caught=True, tp=1, quality=3),
                _score("b", tool="copilot"),
            ],
        }
        all_results = {
            "copilot": [
                ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
                ToolResult(case_id="b", tool="copilot", cost_usd=1.0),
            ],
        }
        table = build_comparison_table(all_scores, all_results, cases)
        assert len(table) == 1
        row = table[0]
        assert row["tool"] == "copilot"
        assert "catch_rate" in row
        assert "ci_lower" in row
        assert "ci_upper" in row
        assert "mean_quality" in row
        assert "false_alarm_rate" in row
        assert "precision" in row
        assert "snr" in row
        assert "novel_count" in row
        assert "snr_inclusive" in row
        assert "cost_per_bug" in row
        assert "judge_cost_per_case" in row
        assert "total_cost_per_bug" in row

    def test_judge_cost_aggregation(self) -> None:
        """judge_cost_per_case and total_cost_per_bug are computed."""
        cases = [_bug_case("a"), _bug_case("b")]
        s1 = _score("a", tool="copilot", caught=True, tp=1)
        s1.judge_cost_usd = 0.002
        s2 = _score("b", tool="copilot", caught=True, tp=1)
        s2.judge_cost_usd = 0.004
        all_scores = {"copilot": [s1, s2]}
        all_results = {
            "copilot": [
                ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
                ToolResult(case_id="b", tool="copilot", cost_usd=1.0),
            ],
        }
        table = build_comparison_table(all_scores, all_results, cases)
        row = table[0]
        # judge_cost_per_case = (0.002 + 0.004) / 2 = 0.003
        assert abs(row["judge_cost_per_case"] - 0.003) < 1e-6
        # total_cost_per_bug = (2.0 tool + 0.006 judge) / 2 catches
        assert abs(row["total_cost_per_bug"] - 1.003) < 1e-4

    def test_total_cost_per_bug_none_when_no_catches(self) -> None:
        cases = [_bug_case("a")]
        s = _score("a", tool="copilot", caught=False)
        s.judge_cost_usd = 0.001
        all_scores = {"copilot": [s]}
        all_results = {
            "copilot": [
                ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
            ],
        }
        table = build_comparison_table(all_scores, all_results, cases)
        assert table[0]["total_cost_per_bug"] is None


class TestJudgeFailedExcludedFromQualityMetrics:
    def test_judge_failed_excluded(self) -> None:
        """Judge-failed scores should not affect mean_quality."""
        cases = [_bug_case("a"), _bug_case("b")]
        good = _score("a", tool="copilot", caught=True, tp=1, quality=3)
        failed = _score("b", tool="copilot", caught=False, quality=0)
        failed.judge_failed = True

        all_scores = {"copilot": [good, failed]}
        all_results = {
            "copilot": [
                ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
                ToolResult(case_id="b", tool="copilot", cost_usd=1.0),
            ],
        }
        table = build_comparison_table(all_scores, all_results, cases)
        row = table[0]
        # mean_quality should only include the non-failed score (quality=3)
        assert row["mean_quality"] == 3.0

    def test_caught_metric_still_includes_all(self) -> None:
        """The detection_score catch rate should include all cases."""
        cases = [_bug_case("a"), _bug_case("b")]
        good = _score("a", tool="copilot", caught=True, det=2, tp=1, quality=3)
        failed = _score("b", tool="copilot", caught=False, det=0, quality=0)
        failed.judge_failed = True

        all_scores = {"copilot": [good, failed]}
        all_results = {
            "copilot": [
                ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
                ToolResult(case_id="b", tool="copilot", cost_usd=1.0),
            ],
        }
        table = build_comparison_table(all_scores, all_results, cases)
        row = table[0]
        # catch_rate = 1/2 (detection_score >= 2 for one of two bug cases)
        assert row["catch_rate"] == 0.5


class TestExportCSV:
    def test_writes_file(self, tmp_path: Path) -> None:
        table = [{"tool": "copilot", "catch_rate": 0.5}]
        out = tmp_path / "out.csv"
        export_csv(table, out)
        text = out.read_text()
        assert "tool" in text
        assert "copilot" in text


class TestLoadScores:
    def test_loads(self, tmp_path: Path) -> None:
        s = _score("a", tool="copilot")
        save_score(s, tmp_path / "a__copilot.yaml")
        loaded = load_scores(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].case_id == "a"


class TestRunAnalysis:
    def test_no_charts(self, tmp_path: Path) -> None:
        # Set up minimal directory structure
        scores_dir = tmp_path / "scores"
        scores_dir.mkdir()
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        s = _score("a", tool="copilot", caught=True, tp=1)
        save_score(s, scores_dir / "a__copilot.yaml")

        from bugeval.io import save_case, save_result

        save_case(_bug_case("a"), cases_dir / "a.yaml")
        save_result(
            ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
            results_dir / "a__copilot.yaml",
        )

        run_analysis(tmp_path, cases_dir, no_charts=True)
        assert (tmp_path / "comparison.csv").exists()


class TestRunAnalysisPairwiseComparisons:
    def test_pairwise_output(self, tmp_path: Path, capsys: object) -> None:
        """Verify permutation test runs when 2+ tools are present."""
        scores_dir = tmp_path / "scores"
        scores_dir.mkdir()
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        from bugeval.io import save_case, save_result

        save_case(_bug_case("a"), cases_dir / "a.yaml")

        # Two tools with different catch results
        s1 = _score("a", tool="copilot", caught=True, tp=1)
        s2 = _score("a", tool="agent", caught=False, fp=1)
        save_score(s1, scores_dir / "a__copilot.yaml")
        save_score(s2, scores_dir / "a__agent.yaml")

        save_result(
            ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
            results_dir / "a__copilot.yaml",
        )
        save_result(
            ToolResult(case_id="a", tool="agent", cost_usd=1.0),
            results_dir / "a__agent.yaml",
        )

        run_analysis(tmp_path, cases_dir, no_charts=True)

        captured = capsys.readouterr()  # type: ignore[union-attr]
        assert "Pairwise Comparisons" in captured.err or "Pairwise Comparisons" in captured.out
        assert "agent vs copilot" in captured.err or "agent vs copilot" in captured.out


class TestSeverityWeightedInTable:
    def test_column_present(self) -> None:
        cases = [_bug_case("a", severity="critical")]
        all_scores = {
            "copilot": [_score("a", tool="copilot", caught=True, tp=1)],
        }
        all_results = {
            "copilot": [ToolResult(case_id="a", tool="copilot", cost_usd=1.0)],
        }
        table = build_comparison_table(all_scores, all_results, cases)
        assert len(table) == 1
        assert "severity_weighted_catch_rate" in table[0]
        assert table[0]["severity_weighted_catch_rate"] == 1.0


class TestContextLevelSlice:
    def test_slice_by_context_level(self) -> None:
        cases = [_bug_case("a"), _bug_case("b")]
        s1 = CaseScore(
            case_id="a",
            tool="t",
            caught=True,
            context_level="diff-only",
        )
        s2 = CaseScore(
            case_id="b",
            tool="t",
            caught=False,
            context_level="diff+repo",
        )
        sliced = slice_scores([s1, s2], cases, "context_level", "diff-only")
        assert len(sliced) == 1
        assert sliced[0].case_id == "a"


class TestLocalizationDistanceInTable:
    def test_present_when_caught(self) -> None:
        cases = [_bug_case("a"), _bug_case("b")]
        all_scores = {
            "copilot": [
                _score("a", tool="copilot", caught=True, loc_dist=3),
                _score("b", tool="copilot", caught=True, loc_dist=7),
            ],
        }
        all_results = {
            "copilot": [
                ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
                ToolResult(case_id="b", tool="copilot", cost_usd=1.0),
            ],
        }
        table = build_comparison_table(all_scores, all_results, cases)
        assert len(table) == 1
        assert "median_localization" in table[0]
        assert table[0]["median_localization"] == 5.0

    def test_none_when_no_catches(self) -> None:
        cases = [_bug_case("a")]
        all_scores = {
            "copilot": [_score("a", tool="copilot", caught=False)],
        }
        all_results = {
            "copilot": [ToolResult(case_id="a", tool="copilot", cost_usd=1.0)],
        }
        table = build_comparison_table(all_scores, all_results, cases)
        assert table[0]["median_localization"] is None


class TestJudgeFailedReportedInAnalysis:
    def test_warning_output(self, tmp_path: Path, capsys: object) -> None:
        scores_dir = tmp_path / "scores"
        scores_dir.mkdir()
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        from bugeval.io import save_case, save_result

        save_case(_bug_case("a"), cases_dir / "a.yaml")

        s = _score("a", tool="copilot", caught=False)
        s.judge_failed = True
        save_score(s, scores_dir / "a__copilot.yaml")

        save_result(
            ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
            results_dir / "a__copilot.yaml",
        )

        run_analysis(tmp_path, cases_dir, no_charts=True)

        captured = capsys.readouterr()  # type: ignore[union-attr]
        combined = captured.err + captured.out
        assert "judge failures" in combined
        assert "scored as 0/0" in combined


class TestToleranceSensitivity:
    def test_tolerance_sensitivity_returns_dict(self) -> None:
        """Verify structure: tool -> tolerance -> float rate."""
        cases = [
            TestCase(
                id="a",
                repo="R/x",
                kind=CaseKind.bug,
                base_commit="abc",
                truth=GroundTruth(
                    buggy_lines=[
                        BuggyLine(file="src/lib.rs", line=10, content="x"),
                    ],
                ),
            ),
        ]
        all_scores = {"copilot": [_score("a", tool="copilot", caught=True)]}
        all_results = {
            "copilot": [
                ToolResult(
                    case_id="a",
                    tool="copilot",
                    comments=[Comment(file="src/lib.rs", line=12, body="bug here")],
                ),
            ],
        }
        result = tolerance_sensitivity(all_scores, all_results, cases)
        assert "copilot" in result
        assert isinstance(result["copilot"], dict)
        # Default tolerances are [3, 5, 10, 15, 20]
        assert set(result["copilot"].keys()) == {3, 5, 10, 15, 20}
        for rate in result["copilot"].values():
            assert 0.0 <= rate <= 1.0
        # line 12 vs line 10 = distance 2, should be caught at tol>=3
        assert result["copilot"][3] == 1.0
        # tol=3 catches dist=2, so all larger tolerances also catch it
        assert result["copilot"][10] == 1.0

    def test_tolerance_sensitivity_custom_tolerances(self) -> None:
        cases = [
            TestCase(
                id="a",
                repo="R/x",
                kind=CaseKind.bug,
                base_commit="abc",
                truth=GroundTruth(
                    buggy_lines=[
                        BuggyLine(file="src/lib.rs", line=10, content="x"),
                    ],
                ),
            ),
        ]
        all_scores = {"t": [_score("a", tool="t")]}
        all_results = {
            "t": [
                ToolResult(
                    case_id="a",
                    tool="t",
                    comments=[Comment(file="src/lib.rs", line=20, body="issue")],
                ),
            ],
        }
        result = tolerance_sensitivity(all_scores, all_results, cases, tolerances=[5, 10, 15])
        assert set(result["t"].keys()) == {5, 10, 15}
        # distance=10, tol=5 misses, tol=10 catches
        assert result["t"][5] == 0.0
        assert result["t"][10] == 1.0

    def test_tolerance_sensitivity_empty(self) -> None:
        result = tolerance_sensitivity({}, {}, [])
        assert result == {}


class TestContaminationReporting:
    def test_contamination_reporting(self, tmp_path: Path, capsys: object) -> None:
        """Verify contaminated vs clean split is reported."""
        scores_dir = tmp_path / "scores"
        scores_dir.mkdir()
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        from bugeval.io import save_case, save_result

        save_case(_bug_case("a"), cases_dir / "a.yaml")
        save_case(_bug_case("b"), cases_dir / "b.yaml")

        s1 = _score("a", tool="copilot", caught=True, det=2, tp=1)
        s1.potentially_contaminated = True
        s2 = _score("b", tool="copilot", caught=False)
        save_score(s1, scores_dir / "a__copilot.yaml")
        save_score(s2, scores_dir / "b__copilot.yaml")

        save_result(
            ToolResult(case_id="a", tool="copilot", cost_usd=1.0),
            results_dir / "a__copilot.yaml",
        )
        save_result(
            ToolResult(case_id="b", tool="copilot", cost_usd=1.0),
            results_dir / "b__copilot.yaml",
        )

        run_analysis(tmp_path, cases_dir, no_charts=True)

        captured = capsys.readouterr()  # type: ignore[union-attr]
        combined = captured.err + captured.out
        assert "Contamination Impact" in combined
        assert "1/2 contaminated" in combined
        assert "clean only" in combined


# ---------------------------------------------------------------------------
# signal_to_noise_inclusive — edge cases
# ---------------------------------------------------------------------------


class TestSignalToNoiseInclusiveExtended:
    def test_no_comments(self) -> None:
        """Scores with no comment_scores yield 0.0."""
        scores = [_score("a", tp=0, fp=0, novel=0)]
        assert signal_to_noise_inclusive(scores) == 0.0

    def test_all_novel(self) -> None:
        """All novel comments count as useful."""
        scores = [_score("a", tp=0, novel=3, fp=0)]
        assert abs(signal_to_noise_inclusive(scores) - 1.0) < 1e-9

    def test_mixed_tp_and_novel(self) -> None:
        scores = [_score("a", tp=2, novel=1, fp=3)]
        # 3 useful / 6 total = 0.5
        assert abs(signal_to_noise_inclusive(scores) - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# mechanical_catch_rate — edge cases
# ---------------------------------------------------------------------------


class TestMechanicalCatchRateExtended:
    def test_all_caught(self) -> None:
        scores = [_score("a", caught=True), _score("b", caught=True)]
        assert mechanical_catch_rate(scores) == 1.0

    def test_none_caught(self) -> None:
        scores = [_score("a", caught=False), _score("b", caught=False)]
        assert mechanical_catch_rate(scores) == 0.0

    def test_single_score(self) -> None:
        assert mechanical_catch_rate([_score("a", caught=True)]) == 1.0


# ---------------------------------------------------------------------------
# permutation_test — extended
# ---------------------------------------------------------------------------


class TestPermutationTestExtended:
    def test_empty_groups(self) -> None:
        """Empty groups produce p=1.0 (no difference)."""
        p = permutation_test([], [], n_permutations=100)
        # With empty groups, all permutations produce diff=0, obs=0, so count==n
        assert p == 1.0

    def test_single_element_groups(self) -> None:
        p = permutation_test([10.0], [10.0], n_permutations=1000)
        assert p > 0.05  # identical values: not significant


# ---------------------------------------------------------------------------
# benjamini_hochberg — extended
# ---------------------------------------------------------------------------


class TestBenjaminiHochbergExtended:
    def test_empty(self) -> None:
        assert benjamini_hochberg([]) == []

    def test_all_significant(self) -> None:
        pvals = [0.001, 0.002, 0.003]
        sig = benjamini_hochberg(pvals, alpha=0.05)
        assert all(sig)

    def test_none_significant(self) -> None:
        pvals = [0.5, 0.6, 0.7]
        sig = benjamini_hochberg(pvals, alpha=0.05)
        assert not any(sig)

    def test_single_pvalue(self) -> None:
        assert benjamini_hochberg([0.01], alpha=0.05) == [True]
        assert benjamini_hochberg([0.10], alpha=0.05) == [False]

    def test_preserves_original_order(self) -> None:
        """Output indices correspond to input indices, not sorted order."""
        pvals = [0.5, 0.001, 0.3]
        sig = benjamini_hochberg(pvals, alpha=0.05)
        # Only p=0.001 (index 1) is significant: 0.001 <= 1/3 * 0.05 = 0.0167
        assert sig[0] is False
        assert sig[1] is True
        assert sig[2] is False


# ---------------------------------------------------------------------------
# tolerance_sensitivity — single tool edge case
# ---------------------------------------------------------------------------


class TestToleranceSensitivitySingleTool:
    def test_no_bug_cases_skipped(self) -> None:
        """Clean cases should be skipped in tolerance sweep."""
        cases = [_clean_case("a")]
        all_scores = {"t": [_score("a", tool="t")]}
        all_results = {
            "t": [
                ToolResult(
                    case_id="a",
                    tool="t",
                    comments=[Comment(file="f.rs", line=1, body="issue")],
                ),
            ],
        }
        result = tolerance_sensitivity(all_scores, all_results, cases)
        # Clean case has no truth, so total=0 -> rate=0.0 for all tolerances
        assert all(v == 0.0 for v in result["t"].values())
