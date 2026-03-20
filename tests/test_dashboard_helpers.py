"""Tests for dashboard_helpers — pure functions with no Flask dependency."""

from __future__ import annotations

from pathlib import Path

from bugeval.dashboard_helpers import (
    classify_runner_type,
    compute_dataset_stats,
    group_agg_by_runner,
    load_alignment_for_cases,
    md_to_html,
)
from bugeval.models import (
    Category,
    Difficulty,
    ExpectedFinding,
    PRSize,
    Severity,
    TestCase,
)
from bugeval.validate_cases import AlignmentStatus


def _make_case(
    case_id: str = "leo-001",
    repo: str = "leo",
    expected_findings: list[ExpectedFinding] | None = None,
    quality_flags: list[str] | None = None,
    verified: bool = False,
    needs_manual_review: bool = False,
) -> TestCase:
    return TestCase(
        id=case_id,
        repo=repo,
        base_commit="aaa",
        head_commit="bbb",
        fix_commit="ccc",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.medium,
        description="test case",
        expected_findings=expected_findings or [],
        quality_flags=quality_flags or [],
        verified=verified,
        needs_manual_review=needs_manual_review,
    )


# ---------------------------------------------------------------------------
# classify_runner_type
# ---------------------------------------------------------------------------


class TestClassifyRunnerType:
    def test_cli_tool(self) -> None:
        assert classify_runner_type("claude-code-cli") == "CLI"

    def test_cli_prefix(self) -> None:
        assert classify_runner_type("gemini-cli-v2") == "CLI"

    def test_api_tool(self) -> None:
        assert classify_runner_type("anthropic-api") == "API"

    def test_api_prefix(self) -> None:
        assert classify_runner_type("openai-api-gpt4") == "API"

    def test_commercial(self) -> None:
        assert classify_runner_type("greptile") == "Commercial"

    def test_commercial_unknown(self) -> None:
        assert classify_runner_type("coderabbit") == "Commercial"


# ---------------------------------------------------------------------------
# group_agg_by_runner
# ---------------------------------------------------------------------------


class TestGroupAggByRunner:
    def test_groups_by_type(self) -> None:
        agg = {
            "greptile": {"count": 10},
            "claude-code-cli": {"count": 20},
            "anthropic-api": {"count": 15},
        }
        result = group_agg_by_runner(agg)
        assert "Commercial" in result
        assert "CLI" in result
        assert "API" in result
        assert "greptile" in result["Commercial"]
        assert "claude-code-cli" in result["CLI"]
        assert "anthropic-api" in result["API"]

    def test_empty(self) -> None:
        assert group_agg_by_runner({}) == {}


# ---------------------------------------------------------------------------
# load_alignment_for_cases
# ---------------------------------------------------------------------------


class TestLoadAlignmentForCases:
    def test_with_patch_file(self, tmp_path: Path) -> None:
        patches_dir = tmp_path / "patches"
        patches_dir.mkdir()
        patch_text = "diff --git a/src/foo.rs b/src/foo.rs\n@@ -10,3 +10,4 @@\n context\n+added\n"
        (patches_dir / "leo-001.patch").write_text(patch_text)
        case = _make_case(
            expected_findings=[ExpectedFinding(file="src/foo.rs", line=11, summary="bug")]
        )
        result = load_alignment_for_cases([case], patches_dir)
        assert result["leo-001"] == AlignmentStatus.aligned

    def test_fallback_to_quality_flags_verified(self, tmp_path: Path) -> None:
        patches_dir = tmp_path / "patches"
        patches_dir.mkdir()
        case = _make_case(quality_flags=["alignment-verified"])
        result = load_alignment_for_cases([case], patches_dir)
        assert result["leo-001"] == AlignmentStatus.aligned

    def test_fallback_to_quality_flags_failed(self, tmp_path: Path) -> None:
        patches_dir = tmp_path / "patches"
        patches_dir.mkdir()
        case = _make_case(quality_flags=["alignment-failed"])
        result = load_alignment_for_cases([case], patches_dir)
        assert result["leo-001"] == AlignmentStatus.misaligned

    def test_no_patch_no_flags(self, tmp_path: Path) -> None:
        patches_dir = tmp_path / "patches"
        patches_dir.mkdir()
        case = _make_case()
        result = load_alignment_for_cases([case], patches_dir)
        assert result["leo-001"] == AlignmentStatus.aligned


# ---------------------------------------------------------------------------
# md_to_html
# ---------------------------------------------------------------------------


class TestMdToHtml:
    def test_heading(self) -> None:
        html = md_to_html("## Hello World")
        assert "<h2>Hello World</h2>" in html

    def test_bold(self) -> None:
        html = md_to_html("This is **bold** text")
        assert "<strong>bold</strong>" in html

    def test_code(self) -> None:
        html = md_to_html("Use `foo()` here")
        assert "<code>foo()</code>" in html

    def test_table(self) -> None:
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = md_to_html(md)
        assert "<table>" in html
        assert "<th>A</th>" in html
        assert "<td>1</td>" in html

    def test_hr(self) -> None:
        html = md_to_html("---")
        assert "<hr>" in html

    def test_empty(self) -> None:
        assert md_to_html("") == ""


# ---------------------------------------------------------------------------
# compute_dataset_stats
# ---------------------------------------------------------------------------


class TestComputeDatasetStats:
    def test_basic_stats(self) -> None:
        cases = [
            _make_case("leo-001", verified=True),
            _make_case("leo-002", needs_manual_review=True),
        ]
        stats = compute_dataset_stats(cases)
        assert stats["total"] == 2
        assert stats["verified"] == 1
        assert stats["needs_review"] == 1
        assert stats["avg_findings"] == 0.0

    def test_distributions(self) -> None:
        cases = [_make_case("leo-001"), _make_case("leo-002")]
        stats = compute_dataset_stats(cases)
        assert "category" in stats["distributions"]
        assert stats["distributions"]["repo"]["leo"] == 2

    def test_findings_list(self) -> None:
        ef = ExpectedFinding(file="a.rs", line=1, summary="bug")
        cases = [_make_case("leo-001", expected_findings=[ef])]
        stats = compute_dataset_stats(cases)
        assert len(stats["findings_list"]) == 1
        assert stats["findings_list"][0]["case_id"] == "leo-001"

    def test_empty(self) -> None:
        stats = compute_dataset_stats([])
        assert stats["total"] == 0
        assert stats["avg_findings"] == 0.0

    def test_groundedness_flags(self) -> None:
        cases = [
            _make_case("leo-001", quality_flags=["groundedness-failed"]),
            _make_case("leo-002"),
        ]
        stats = compute_dataset_stats(cases)
        assert stats["grounded_pass"] == 1
        assert stats["grounded_fail"] == 1
