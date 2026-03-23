"""Tests for curation pass: auto-detect and exclude bad test cases."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from bugeval.curate import (
    FEATURE_KEYWORDS,
    MAX_BUGGY_LINES,
    REASON_ALL_NON_CODE,
    REASON_CI_FIX,
    REASON_CLIPPY_LINT,
    REASON_DEPENDENCY_BUMP,
    REASON_DEPRECATION,
    REASON_DOC_FIX,
    REASON_FEATURE_NOT_FIX,
    REASON_LLM_NOT_A_BUG,
    REASON_MERGED_SIBLING,
    REASON_NO_BUGGY_LINES,
    REASON_NOT_VALIDATED,
    REASON_PERF_OPTIMIZATION,
    REASON_RELEASE_VERSION,
    REASON_TYPO_ONLY,
    _find_case_path,
    auto_curate_case,
    compute_quality_flags,
    curate_cases,
    find_duplicate_introducing,
    llm_classify_case,
)
from bugeval.io import save_case
from bugeval.models import (
    BuggyLine,
    CaseKind,
    GroundTruth,
    TestCase,
    Validation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_case(
    case_id: str = "test-001",
    *,
    truth: GroundTruth | None = None,
    fix_pr_title: str = "",
    introducing_pr_number: int | None = None,
    excluded: bool = False,
    excluded_reason: str = "",
) -> TestCase:
    return TestCase(
        id=case_id,
        repo="ProvableHQ/snarkVM",
        kind=CaseKind.bug,
        base_commit="abc123",
        fix_commit="def456",
        fix_pr_title=fix_pr_title,
        introducing_pr_number=introducing_pr_number,
        truth=truth,
        excluded=excluded,
        excluded_reason=excluded_reason,
    )


def _default_truth(num_buggy_lines: int = 1) -> GroundTruth:
    return GroundTruth(
        introducing_commit="abc123",
        blame_confidence="A",
        buggy_lines=[
            BuggyLine(file="src/lib.rs", line=i + 1, content=f"line {i}")
            for i in range(num_buggy_lines)
        ],
        fix_summary="Fixed the issue",
    )


# ---------------------------------------------------------------------------
# auto_curate_case
# ---------------------------------------------------------------------------


class TestAutoCurateCase:
    def test_no_buggy_lines(self) -> None:
        case = _make_case(truth=GroundTruth(buggy_lines=[]))
        assert auto_curate_case(case) == REASON_NO_BUGGY_LINES

    def test_many_buggy_lines_not_excluded(self) -> None:
        """Cases with many buggy lines are kept for LLM judge evaluation."""
        case = _make_case(truth=_default_truth(MAX_BUGGY_LINES + 1))
        assert auto_curate_case(case) is None

    def test_dependency_bump_excluded(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Bump foo from 1.0 to 2.0")
        assert auto_curate_case(case) == REASON_DEPENDENCY_BUMP

    def test_ci_fix_excluded(self) -> None:
        case = _make_case(truth=GroundTruth(buggy_lines=[]), fix_pr_title="Fix CI")
        assert auto_curate_case(case) == REASON_CI_FIX

    def test_feature_keyword_excludes(self) -> None:
        for kw in FEATURE_KEYWORDS:
            case = _make_case(
                truth=_default_truth(),
                fix_pr_title=f"{kw} add new widget",
            )
            assert auto_curate_case(case) == REASON_FEATURE_NOT_FIX

    def test_feature_keyword_case_insensitive(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="FEAT: add new widget",
        )
        assert auto_curate_case(case) == REASON_FEATURE_NOT_FIX

    def test_feature_keyword_with_fix_passes(self) -> None:
        """If title has both a feature keyword and 'fix', it passes."""
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="feat: fix broken widget",
        )
        assert auto_curate_case(case) is None

    def test_truth_none_passes(self) -> None:
        case = _make_case(truth=None)
        assert auto_curate_case(case) is None

    def test_valid_case_passes(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="Fix off-by-one in loop",
        )
        assert auto_curate_case(case) is None

    def test_feature_takes_priority_over_no_buggy_lines(self) -> None:
        """Feature check runs before no-buggy-lines (early, cheap filter)."""
        case = _make_case(
            truth=GroundTruth(buggy_lines=[]),
            fix_pr_title="feat: add widget",
        )
        assert auto_curate_case(case) == REASON_FEATURE_NOT_FIX


# ---------------------------------------------------------------------------
# find_duplicate_introducing
# ---------------------------------------------------------------------------


class TestFindDuplicateIntroducing:
    def test_no_duplicates(self) -> None:
        cases = [
            _make_case("c-001", introducing_pr_number=1),
            _make_case("c-002", introducing_pr_number=2),
            _make_case("c-003", introducing_pr_number=3),
        ]
        assert find_duplicate_introducing(cases) == set()

    def test_duplicate_found(self) -> None:
        cases = [
            _make_case("c-001", introducing_pr_number=10),
            _make_case("c-002", introducing_pr_number=10),
        ]
        dups = find_duplicate_introducing(cases)
        # First case kept, second marked as duplicate
        assert dups == {"c-002"}

    def test_multiple_duplicates(self) -> None:
        cases = [
            _make_case("c-001", introducing_pr_number=10),
            _make_case("c-002", introducing_pr_number=10),
            _make_case("c-003", introducing_pr_number=10),
        ]
        dups = find_duplicate_introducing(cases)
        assert dups == {"c-002", "c-003"}

    def test_none_introducing_pr_ignored(self) -> None:
        cases = [
            _make_case("c-001", introducing_pr_number=None),
            _make_case("c-002", introducing_pr_number=None),
        ]
        assert find_duplicate_introducing(cases) == set()

    def test_empty_list(self) -> None:
        assert find_duplicate_introducing([]) == set()

    def test_single_case(self) -> None:
        cases = [_make_case("c-001", introducing_pr_number=5)]
        assert find_duplicate_introducing(cases) == set()

    def test_mixed_none_and_duplicate(self) -> None:
        cases = [
            _make_case("c-001", introducing_pr_number=None),
            _make_case("c-002", introducing_pr_number=7),
            _make_case("c-003", introducing_pr_number=7),
            _make_case("c-004", introducing_pr_number=None),
        ]
        assert find_duplicate_introducing(cases) == {"c-003"}


# ---------------------------------------------------------------------------
# _find_case_path
# ---------------------------------------------------------------------------


class TestFindCasePath:
    def test_found_in_root(self, tmp_path: Path) -> None:
        case_file = tmp_path / "test-001.yaml"
        case_file.write_text("id: test-001\n")
        result = _find_case_path(tmp_path, "test-001")
        assert result == case_file

    def test_found_in_subdirectory(self, tmp_path: Path) -> None:
        sub = tmp_path / "repo"
        sub.mkdir()
        case_file = sub / "test-001.yaml"
        case_file.write_text("id: test-001\n")
        result = _find_case_path(tmp_path, "test-001")
        assert result == case_file

    def test_not_found(self, tmp_path: Path) -> None:
        result = _find_case_path(tmp_path, "nonexistent")
        assert result is None

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = _find_case_path(tmp_path, "test-001")
        assert result is None

    def test_ignores_non_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "test-001.json").write_text("{}")
        result = _find_case_path(tmp_path, "test-001")
        assert result is None


# ---------------------------------------------------------------------------
# curate_cases
# ---------------------------------------------------------------------------


class TestCurateCases:
    def _setup_cases(
        self,
        tmp_path: Path,
        cases: list[TestCase],
    ) -> Path:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        for case in cases:
            save_case(case, cases_dir / f"{case.id}.yaml")
        return cases_dir

    def test_dry_run_no_files_modified(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-001",
            truth=GroundTruth(buggy_lines=[]),
        )
        cases_dir = self._setup_cases(tmp_path, [case])

        results = curate_cases(cases_dir, dry_run=True)

        assert REASON_NO_BUGGY_LINES in results
        assert "c-001" in results[REASON_NO_BUGGY_LINES]

        # File should NOT be modified (still not excluded)
        from bugeval.io import load_case

        reloaded = load_case(cases_dir / "c-001.yaml")
        assert reloaded.excluded is False

    def test_non_dry_run_modifies_files(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-001",
            truth=GroundTruth(buggy_lines=[]),
        )
        cases_dir = self._setup_cases(tmp_path, [case])

        results = curate_cases(cases_dir, dry_run=False)

        assert REASON_NO_BUGGY_LINES in results

        from bugeval.io import load_case

        reloaded = load_case(cases_dir / "c-001.yaml")
        assert reloaded.excluded is True
        assert reloaded.excluded_reason == REASON_NO_BUGGY_LINES

    def test_already_excluded_skipped(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-001",
            truth=GroundTruth(buggy_lines=[]),
            excluded=True,
            excluded_reason="manual",
        )
        cases_dir = self._setup_cases(tmp_path, [case])

        results = curate_cases(cases_dir, dry_run=False)

        assert "already-excluded" in results
        assert "c-001" in results["already-excluded"]
        assert REASON_NO_BUGGY_LINES not in results

    def test_duplicate_introducing_excluded(self, tmp_path: Path) -> None:
        case1 = _make_case(
            "c-001",
            truth=_default_truth(),
            introducing_pr_number=10,
        )
        case2 = _make_case(
            "c-002",
            truth=_default_truth(),
            introducing_pr_number=10,
        )
        cases_dir = self._setup_cases(tmp_path, [case1, case2])

        results = curate_cases(cases_dir, dry_run=False)

        assert REASON_MERGED_SIBLING in results
        assert "c-002" in results[REASON_MERGED_SIBLING]
        assert "c-001" not in results.get(REASON_MERGED_SIBLING, [])

    def test_valid_cases_not_excluded(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-001",
            truth=_default_truth(),
            fix_pr_title="Fix off-by-one",
        )
        cases_dir = self._setup_cases(tmp_path, [case])

        results = curate_cases(cases_dir, dry_run=False)

        assert results == {}

    def test_reset_clears_exclusions(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-001",
            truth=GroundTruth(buggy_lines=[]),
            excluded=True,
            excluded_reason=REASON_NO_BUGGY_LINES,
        )
        cases_dir = self._setup_cases(tmp_path, [case])

        results = curate_cases(cases_dir, reset=True)

        assert results == {}

        from bugeval.io import load_case

        reloaded = load_case(cases_dir / "c-001.yaml")
        assert reloaded.excluded is False
        assert reloaded.excluded_reason == ""

    def test_reset_dry_run_no_modify(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-001",
            truth=GroundTruth(buggy_lines=[]),
            excluded=True,
            excluded_reason=REASON_NO_BUGGY_LINES,
        )
        cases_dir = self._setup_cases(tmp_path, [case])

        curate_cases(cases_dir, reset=True, dry_run=True)

        from bugeval.io import load_case

        reloaded = load_case(cases_dir / "c-001.yaml")
        assert reloaded.excluded is True
        assert reloaded.excluded_reason == REASON_NO_BUGGY_LINES

    def test_multiple_reasons(self, tmp_path: Path) -> None:
        cases = [
            _make_case(
                "c-001",
                truth=GroundTruth(buggy_lines=[]),
            ),
            _make_case(
                "c-002",
                truth=_default_truth(MAX_BUGGY_LINES + 1),
            ),
            _make_case(
                "c-003",
                truth=_default_truth(),
                fix_pr_title="[Feature] add new thing",
            ),
        ]
        cases_dir = self._setup_cases(tmp_path, cases)

        results = curate_cases(cases_dir, dry_run=True)

        assert "c-001" in results[REASON_NO_BUGGY_LINES]
        # c-002 has many buggy lines but is NOT excluded (kept for LLM judge)
        assert "c-003" in results[REASON_FEATURE_NOT_FIX]

    def test_cases_in_subdirectory(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        sub = cases_dir / "repo"
        sub.mkdir(parents=True)
        case = _make_case(
            "c-001",
            truth=GroundTruth(buggy_lines=[]),
        )
        save_case(case, sub / "c-001.yaml")

        results = curate_cases(cases_dir, dry_run=False)

        assert REASON_NO_BUGGY_LINES in results

        from bugeval.io import load_case

        reloaded = load_case(sub / "c-001.yaml")
        assert reloaded.excluded is True

    def test_empty_cases_dir(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        results = curate_cases(cases_dir, dry_run=False)
        assert results == {}


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


class TestCurateCli:
    def test_curate_cli_dry_run(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from bugeval.curate import curate

        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        case = _make_case("c-001", truth=GroundTruth(buggy_lines=[]))
        save_case(case, cases_dir / "c-001.yaml")

        runner = CliRunner()
        result = runner.invoke(
            curate,
            ["--cases-dir", str(cases_dir), "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Would exclude" in result.output

    def test_curate_cli_reset(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from bugeval.curate import curate

        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        case = _make_case(
            "c-001",
            truth=GroundTruth(buggy_lines=[]),
            excluded=True,
            excluded_reason=REASON_NO_BUGGY_LINES,
        )
        save_case(case, cases_dir / "c-001.yaml")

        runner = CliRunner()
        result = runner.invoke(
            curate,
            ["--cases-dir", str(cases_dir), "--reset"],
        )
        assert result.exit_code == 0
        assert "Reset all exclusions" in result.output


# ---------------------------------------------------------------------------
# Expanded curation filters (Task 2)
# ---------------------------------------------------------------------------


class TestExpandedCurationFilters:
    def test_clippy_excluded(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix Clippy Errors")
        assert auto_curate_case(case) == REASON_CLIPPY_LINT

    def test_lint_excluded(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="chore: fix lint errors")
        assert auto_curate_case(case) == REASON_CLIPPY_LINT

    def test_typo_excluded(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="fix: typos in panic function and comments",
        )
        assert auto_curate_case(case) == REASON_TYPO_ONLY

    def test_doc_expanded_fix_some_doc(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix some doc.")
        assert auto_curate_case(case) == REASON_DOC_FIX

    def test_doc_expanded_help_message(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="Fix help messages for command line options",
        )
        assert auto_curate_case(case) == REASON_DOC_FIX

    def test_release_bracket_excluded(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="[Release] Leo v3.3.1",
        )
        assert auto_curate_case(case) == REASON_RELEASE_VERSION

    def test_version_bump_excluded(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="Leo v3.3.1 patch release",
        )
        assert auto_curate_case(case) == REASON_RELEASE_VERSION

    def test_perf_no_issue_excluded(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="avoid extra allocation when building record members",
        )
        assert auto_curate_case(case) == REASON_PERF_OPTIMIZATION

    def test_deprecation_excluded(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="[Fix] Remove deprecation warning for `leo build`",
        )
        assert auto_curate_case(case) == REASON_DEPRECATION

    def test_real_bug_not_excluded(self) -> None:
        case = _make_case(
            truth=_default_truth(),
            fix_pr_title="Fix off-by-one in loop counter",
        )
        assert auto_curate_case(case) is None


# ---------------------------------------------------------------------------
# Non-code line exclusion (Task 3)
# ---------------------------------------------------------------------------


class TestNonCodeLineExclusion:
    def test_many_non_code_lines_excluded(self) -> None:
        """3+ non-test, non-code lines triggers exclusion."""
        truth = GroundTruth(
            introducing_commit="abc123",
            buggy_lines=[
                BuggyLine(file="src/lib.rs", line=1, content="// a comment"),
                BuggyLine(file="src/lib.rs", line=2, content="/// doc comment"),
                BuggyLine(file="src/lib.rs", line=3, content="use foo::bar;"),
            ],
        )
        case = _make_case(truth=truth, fix_pr_title="Fix some things")
        assert auto_curate_case(case) == REASON_ALL_NON_CODE

    def test_few_non_code_lines_not_excluded(self) -> None:
        """<3 non-test, non-code lines: weak ground truth, don't exclude."""
        truth = GroundTruth(
            introducing_commit="abc123",
            buggy_lines=[
                BuggyLine(file="src/lib.rs", line=1, content="use foo::bar;"),
                BuggyLine(file="src/lib.rs", line=2, content="mod baz;"),
            ],
        )
        case = _make_case(truth=truth, fix_pr_title="Fix some things")
        assert auto_curate_case(case) is None

    def test_mixed_code_and_comments_passes(self) -> None:
        truth = GroundTruth(
            introducing_commit="abc123",
            buggy_lines=[
                BuggyLine(file="src/lib.rs", line=1, content="// a comment"),
                BuggyLine(file="src/lib.rs", line=2, content="let x = foo();"),
            ],
        )
        case = _make_case(truth=truth, fix_pr_title="Fix some things")
        assert auto_curate_case(case) is None

    def test_single_config_line_not_excluded(self) -> None:
        """Single non-code line is too few to confidently exclude."""
        truth = GroundTruth(
            introducing_commit="abc123",
            buggy_lines=[
                BuggyLine(
                    file="Cargo.toml",
                    line=1,
                    content='version = "3.3.0"',
                ),
            ],
        )
        case = _make_case(truth=truth, fix_pr_title="Fix version")
        assert auto_curate_case(case) is None

    def test_many_imports_excluded(self) -> None:
        """7 import lines = clearly non-code, exclude."""
        truth = GroundTruth(
            introducing_commit="abc123",
            buggy_lines=[
                BuggyLine(file="src/mod.rs", line=i, content=f"mod item{i};") for i in range(7)
            ],
        )
        case = _make_case(truth=truth, fix_pr_title="Fix something")
        assert auto_curate_case(case) == REASON_ALL_NON_CODE


# ---------------------------------------------------------------------------
# LLM classification gate (Task 4)
# ---------------------------------------------------------------------------


class TestLlmClassifyCase:
    def test_confirmed_bug_passes(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix off-by-one")
        case.fix_pr_body = "Fixes an off-by-one error in the main loop."
        with patch("bugeval.llm.call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text='{"classification": "bug", "reasoning": "Real bug"}',
                error="",
            )
            result = llm_classify_case(case)
        assert result is None

    def test_not_a_bug_excluded(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix some doc.")
        case.fix_pr_body = "Fixes some documentation."
        with patch("bugeval.llm.call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=('{"classification": "docs", "reasoning": "Documentation only"}'),
                error="",
            )
            result = llm_classify_case(case)
        assert result == REASON_LLM_NOT_A_BUG

    def test_feature_excluded(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Add empty arrays")
        case.fix_pr_body = "Adds support for empty array expressions."
        with patch("bugeval.llm.call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=('{"classification": "feature", "reasoning": "New feature"}'),
                error="",
            )
            result = llm_classify_case(case)
        assert result == REASON_LLM_NOT_A_BUG

    def test_llm_error_returns_none(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix something")
        with patch("bugeval.llm.call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(text="", error="API error")
            result = llm_classify_case(case)
        assert result is None

    def test_ambiguous_returns_none(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix something")
        with patch("bugeval.llm.call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=('{"classification": "ambiguous", "reasoning": "Unclear"}'),
                error="",
            )
            result = llm_classify_case(case)
        assert result is None

    def test_json_in_code_fences(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix something")
        with patch("bugeval.llm.call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=('```json\n{"classification": "style", "reasoning": "Typo"}\n```'),
                error="",
            )
            result = llm_classify_case(case)
        assert result == REASON_LLM_NOT_A_BUG

    def test_unparseable_json_returns_none(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix something")
        with patch("bugeval.llm.call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(text="not json at all", error="")
            result = llm_classify_case(case)
        assert result is None


# -------------------------------------------------------------------
# Validation enforcement (Task 5)
# -------------------------------------------------------------------


class TestValidationEnforcement:
    def test_no_validation_flagged(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix bug")
        case.validation = None
        result = auto_curate_case(case, require_validation=True)
        assert result == REASON_NOT_VALIDATED

    def test_disputed_validation_flagged(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix bug")
        case.validation = Validation(
            claude_verdict="disputed",
            test_validated=False,
        )
        result = auto_curate_case(case, require_validation=True)
        assert result == REASON_NOT_VALIDATED

    def test_validated_passes(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix bug")
        case.validation = Validation(
            claude_verdict="confirmed",
            test_validated=True,
        )
        result = auto_curate_case(case, require_validation=True)
        assert result is None

    def test_no_enforcement_by_default(self) -> None:
        case = _make_case(truth=_default_truth(), fix_pr_title="Fix bug")
        case.validation = None
        result = auto_curate_case(case)
        assert result is None


# -------------------------------------------------------------------
# Quality flags (Task 8)
# -------------------------------------------------------------------


class TestQualityFlags:
    def test_many_buggy_lines_flagged(self) -> None:
        case = _make_case(truth=_default_truth(MAX_BUGGY_LINES + 1))
        flags = compute_quality_flags(case)
        assert "many-buggy-lines" in flags

    def test_normal_case_no_flags(self) -> None:
        case = _make_case(truth=_default_truth(5))
        flags = compute_quality_flags(case)
        assert flags == []

    def test_low_blame_confidence_flagged(self) -> None:
        truth = GroundTruth(
            introducing_commit="abc123",
            blame_confidence="C",
            buggy_lines=[BuggyLine(file="src/lib.rs", line=1, content="code")],
        )
        case = _make_case(truth=truth)
        flags = compute_quality_flags(case)
        assert "low-blame-confidence" in flags

    def test_blame_confidence_d_flagged(self) -> None:
        truth = GroundTruth(
            introducing_commit="abc123",
            blame_confidence="D",
            buggy_lines=[BuggyLine(file="src/lib.rs", line=1, content="code")],
        )
        case = _make_case(truth=truth)
        flags = compute_quality_flags(case)
        assert "low-blame-confidence" in flags

    def test_blame_confidence_a_not_flagged(self) -> None:
        truth = GroundTruth(
            introducing_commit="abc123",
            blame_confidence="A",
            buggy_lines=[BuggyLine(file="src/lib.rs", line=1, content="code")],
        )
        case = _make_case(truth=truth)
        flags = compute_quality_flags(case)
        assert "low-blame-confidence" not in flags

    def test_both_flags(self) -> None:
        truth = GroundTruth(
            introducing_commit="abc123",
            blame_confidence="C",
            buggy_lines=[
                BuggyLine(
                    file="src/lib.rs",
                    line=i,
                    content=f"line {i}",
                )
                for i in range(MAX_BUGGY_LINES + 1)
            ],
        )
        case = _make_case(truth=truth)
        flags = compute_quality_flags(case)
        assert "many-buggy-lines" in flags
        assert "low-blame-confidence" in flags

    def test_no_truth_no_flags(self) -> None:
        case = _make_case(truth=None)
        flags = compute_quality_flags(case)
        assert flags == []


# ---------------------------------------------------------------------------
# Status transition in curation
# ---------------------------------------------------------------------------


class TestStatusTransitionInCuration:
    def test_curated_status_set(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-001",
            truth=_default_truth(),
            fix_pr_title="Fix off-by-one",
        )
        case.status = "ground-truth"
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        save_case(case, cases_dir / "c-001.yaml")
        curate_cases(cases_dir, dry_run=False)
        from bugeval.io import load_case

        reloaded = load_case(cases_dir / "c-001.yaml")
        assert reloaded.status == "curated"

    def test_curated_status_not_set_on_dry_run(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-002",
            truth=_default_truth(),
            fix_pr_title="Fix off-by-one",
        )
        case.status = "ground-truth"
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        save_case(case, cases_dir / "c-002.yaml")
        curate_cases(cases_dir, dry_run=True)
        from bugeval.io import load_case

        reloaded = load_case(cases_dir / "c-002.yaml")
        assert reloaded.status == "ground-truth"

    def test_excluded_case_status_not_changed(self, tmp_path: Path) -> None:
        case = _make_case(
            "c-003",
            truth=GroundTruth(buggy_lines=[]),
            fix_pr_title="Fix something",
        )
        case.status = "ground-truth"
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        save_case(case, cases_dir / "c-003.yaml")
        curate_cases(cases_dir, dry_run=False)
        from bugeval.io import load_case

        reloaded = load_case(cases_dir / "c-003.yaml")
        assert reloaded.status == "ground-truth"


# ---------------------------------------------------------------------------
# Merged sibling reason constant
# ---------------------------------------------------------------------------


class TestMergedSibling:
    def test_merged_sibling_reason_exists(self) -> None:
        from bugeval.curate import REASON_MERGED_SIBLING

        assert REASON_MERGED_SIBLING == "merged-sibling"
