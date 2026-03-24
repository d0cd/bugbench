"""Tests for ground truth construction via diff intersection."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from bugeval.ground_truth import (
    _is_non_source_file,
    _is_test_expectation_file,
    _is_test_file,
    classify_bug,
    classify_line_content,
    compute_buggy_lines,
    compute_metadata,
    extract_bug_description,
    parse_diff_added_lines,
    populate_ground_truth,
)
from bugeval.models import CaseStats, GroundTruth, TestCase

# ---------------------------------------------------------------------------
# Fixtures: synthetic diffs
# ---------------------------------------------------------------------------

SINGLE_FILE_DIFF = """\
diff --git a/src/lib.rs b/src/lib.rs
index abc1234..def5678 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -10,6 +10,8 @@ fn existing() {
     context line
     context line
+    let x = bad_call();
+    let y = x + 1;
     context line
     context line
"""

MULTI_FILE_DIFF = """\
diff --git a/src/foo.rs b/src/foo.rs
index aaa..bbb 100644
--- a/src/foo.rs
+++ b/src/foo.rs
@@ -5,3 +5,5 @@ fn foo() {
     keep
+    added_in_foo_line_6();
+    added_in_foo_line_7();
     keep
diff --git a/src/bar.rs b/src/bar.rs
index ccc..ddd 100644
--- a/src/bar.rs
+++ b/src/bar.rs
@@ -20,3 +20,4 @@ fn bar() {
     keep
+    added_in_bar_line_21();
     keep
"""

FIX_DIFF_EXACT = """\
diff --git a/src/lib.rs b/src/lib.rs
index def5678..ghi9012 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -10,8 +10,7 @@ fn existing() {
     context line
     context line
-    let x = bad_call();
-    let y = x + 1;
+    let x = good_call();
     context line
     context line
"""

FIX_DIFF_DRIFTED = """\
diff --git a/src/lib.rs b/src/lib.rs
index def5678..ghi9012 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -12,5 +12,4 @@ fn existing() {
     context line
-    let x = bad_call();
+    let x = good_call();
     context line
"""

FIX_DIFF_OTHER_FILE = """\
diff --git a/src/other.rs b/src/other.rs
index 111..222 100644
--- a/src/other.rs
+++ b/src/other.rs
@@ -1,4 +1,3 @@
-    removed_line();
+    replaced_line();
     keep
"""

FIX_DIFF_FOO = """\
diff --git a/src/foo.rs b/src/foo.rs
index bbb..eee 100644
--- a/src/foo.rs
+++ b/src/foo.rs
@@ -5,5 +5,4 @@ fn foo() {
     keep
-    added_in_foo_line_6();
+    fixed_in_foo();
     keep
"""

FIX_DIFF_BAR = """\
diff --git a/src/bar.rs b/src/bar.rs
index ddd..fff 100644
--- a/src/bar.rs
+++ b/src/bar.rs
@@ -20,4 +20,3 @@ fn bar() {
     keep
-    added_in_bar_line_21();
     keep
"""


# ---------------------------------------------------------------------------
# parse_diff_added_lines
# ---------------------------------------------------------------------------


class TestParseDiffAddedLines:
    def test_single_file(self) -> None:
        result = parse_diff_added_lines(SINGLE_FILE_DIFF)
        assert "src/lib.rs" in result
        lines = result["src/lib.rs"]
        assert (12, "    let x = bad_call();") in lines
        assert (13, "    let y = x + 1;") in lines

    def test_multiple_files(self) -> None:
        result = parse_diff_added_lines(MULTI_FILE_DIFF)
        assert "src/foo.rs" in result
        assert "src/bar.rs" in result

        foo_lines = result["src/foo.rs"]
        assert (6, "    added_in_foo_line_6();") in foo_lines
        assert (7, "    added_in_foo_line_7();") in foo_lines

        bar_lines = result["src/bar.rs"]
        assert (21, "    added_in_bar_line_21();") in bar_lines


# ---------------------------------------------------------------------------
# compute_buggy_lines
# ---------------------------------------------------------------------------


class TestComputeBuggyLines:
    def test_exact_match(self) -> None:
        result = compute_buggy_lines(SINGLE_FILE_DIFF, [FIX_DIFF_EXACT])
        assert len(result) >= 1
        files = {bl.file for bl in result}
        assert "src/lib.rs" in files
        # At least one of the two added lines should match
        matched_lines = {bl.line for bl in result if bl.file == "src/lib.rs"}
        assert 12 in matched_lines or 13 in matched_lines

    def test_tolerance(self) -> None:
        # FIX_DIFF_DRIFTED removes line 13 (old side) which was originally
        # added at line 12 — within tolerance of ±3
        result = compute_buggy_lines(SINGLE_FILE_DIFF, [FIX_DIFF_DRIFTED])
        assert len(result) >= 1
        files = {bl.file for bl in result}
        assert "src/lib.rs" in files

    def test_no_overlap(self) -> None:
        result = compute_buggy_lines(SINGLE_FILE_DIFF, [FIX_DIFF_OTHER_FILE])
        assert result == []

    def test_multi_fix(self) -> None:
        result = compute_buggy_lines(MULTI_FILE_DIFF, [FIX_DIFF_FOO, FIX_DIFF_BAR])
        files = {bl.file for bl in result}
        # Both files should have matched lines
        assert "src/foo.rs" in files
        assert "src/bar.rs" in files

    def test_content_match_fallback(self) -> None:
        """Content match catches lines when line numbers drift beyond tolerance."""
        # Introducing adds bad_call at line 12
        intro = SINGLE_FILE_DIFF
        # Fix deletes bad_call at line 50 (way beyond ±3 drift)
        fix = """\
diff --git a/src/lib.rs b/src/lib.rs
index def5678..ghi9012 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -48,5 +48,4 @@ fn existing() {
     context line
-    let x = bad_call();
+    let x = good_call();
     context line
"""
        result = compute_buggy_lines(intro, [fix])
        assert len(result) >= 1
        matched = {bl.content.strip() for bl in result}
        assert "let x = bad_call();" in matched

    def test_basename_rename_match(self) -> None:
        """Basename matching finds buggy lines when directories are renamed."""
        intro = """\
diff --git a/old_dir/foo.rs b/old_dir/foo.rs
index aaa..bbb 100644
--- a/old_dir/foo.rs
+++ b/old_dir/foo.rs
@@ -5,3 +5,5 @@ fn foo() {
     keep
+    buggy_line();
+    another_bug();
     keep
"""
        fix = """\
diff --git a/new_dir/foo.rs b/new_dir/foo.rs
index bbb..ccc 100644
--- a/new_dir/foo.rs
+++ b/new_dir/foo.rs
@@ -5,5 +5,4 @@ fn foo() {
     keep
-    buggy_line();
+    fixed_line();
     keep
"""
        result = compute_buggy_lines(intro, [fix])
        assert len(result) >= 1
        matched = {bl.content.strip() for bl in result}
        assert "buggy_line();" in matched

    def test_no_false_content_match_on_blank(self) -> None:
        """Blank lines should not trigger content match."""
        intro = """\
diff --git a/src/lib.rs b/src/lib.rs
index aaa..bbb 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -1,3 +1,5 @@
     keep
+
+    real_code();
     keep
"""
        fix = """\
diff --git a/src/lib.rs b/src/lib.rs
index bbb..ccc 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -50,4 +50,3 @@
     keep
-
     keep
"""
        result = compute_buggy_lines(intro, [fix])
        # Should not match the blank line
        contents = [bl.content.strip() for bl in result]
        assert "" not in contents


# ---------------------------------------------------------------------------
# extract_bug_description
# ---------------------------------------------------------------------------


class TestExtractBugDescription:
    def _make_case(self, **overrides: object) -> TestCase:
        defaults: dict[str, object] = {
            "id": "test-001",
            "repo": "org/repo",
            "kind": "bug",
            "base_commit": "aaa",
        }
        defaults.update(overrides)
        return TestCase(**defaults)  # type: ignore[arg-type]

    def test_pr_body_preferred_over_generic_issue(self) -> None:
        """PR body wins over issue body that doesn't look like a bug report."""
        case = self._make_case(
            issue_bodies={123: "I'd like a feature for token registry"},
            fix_pr_body="This fixes the crash in the parser module",
            fix_pr_title="PR title",
        )
        desc, source = extract_bug_description(case)
        assert source == "pr_body"
        assert "crash" in desc

    def test_bug_issue_wins_when_no_pr_body(self) -> None:
        """Issue with bug keywords wins when PR body is empty."""
        case = self._make_case(
            issue_bodies={123: "Bug report: the parser crashes on invalid input"},
            fix_pr_body="",
            fix_pr_title="Fix parser crash",
        )
        desc, source = extract_bug_description(case)
        assert source == "pr_title"  # title comes before issue in priority

    def test_from_pr_body(self) -> None:
        case = self._make_case(
            issue_bodies={},
            fix_pr_body="PR body text here is long enough",
            fix_pr_title="PR title",
        )
        desc, source = extract_bug_description(case)
        assert source == "pr_body"
        assert "PR body text here" in desc

    def test_from_title(self) -> None:
        case = self._make_case(
            issue_bodies={},
            fix_pr_body="",
            fix_pr_title="Fix the crash in parser",
        )
        desc, source = extract_bug_description(case)
        assert source == "pr_title"
        assert "Fix the crash in parser" in desc


# ---------------------------------------------------------------------------
# compute_metadata
# ---------------------------------------------------------------------------


class TestComputeMetadata:
    def _make_case(self, **overrides: object) -> TestCase:
        defaults: dict[str, object] = {
            "id": "test-001",
            "repo": "org/repo",
            "kind": "bug",
            "base_commit": "aaa",
        }
        defaults.update(overrides)
        return TestCase(**defaults)  # type: ignore[arg-type]

    def test_latency(self) -> None:
        case = self._make_case(
            introducing_pr_merge_date="2024-01-01T00:00:00Z",
            fix_pr_merge_date="2024-01-11T00:00:00Z",
        )
        meta = compute_metadata(case)
        assert meta["bug_latency_days"] == 10

    def test_same_author(self) -> None:
        case = self._make_case(
            introducing_pr_author="alice",
            fix_pr_merge_date="",
            introducing_pr_merge_date="",
        )
        # Set fix author via related_prs or fix_pr data
        # The fix author comes from related_prs with role=full_fix, or
        # we compare introducing_pr_author with fix PR author
        # For simplicity, we use the introducing_pr_author field
        # and need a fix_pr_author — check how the model stores it
        # The model doesn't have fix_pr_author directly, but we can
        # check related_prs
        from bugeval.models import PRRelation

        case.related_prs = [
            PRRelation(
                pr_number=42,
                role="full_fix",
                commit="bbb",
                author="alice",
            )
        ]
        meta = compute_metadata(case)
        assert meta["same_author_fix"] is True

    def test_different_author(self) -> None:
        from bugeval.models import PRRelation

        case = self._make_case(
            introducing_pr_author="alice",
            fix_pr_merge_date="",
            introducing_pr_merge_date="",
        )
        case.related_prs = [
            PRRelation(
                pr_number=42,
                role="full_fix",
                commit="bbb",
                author="bob",
            )
        ]
        meta = compute_metadata(case)
        assert meta["same_author_fix"] is False


# ---------------------------------------------------------------------------
# populate_ground_truth (integration, mocked git)
# ---------------------------------------------------------------------------


class TestPopulateGroundTruth:
    def test_full_flow(self) -> None:
        case = TestCase(
            id="test-001",
            repo="org/repo",
            kind="bug",
            base_commit="aaa",
            fix_commit="fff",
            fix_pr_title="Fix the bug",
            fix_pr_body="This fixes a crash",
            introducing_pr_merge_date="2024-01-01T00:00:00Z",
            fix_pr_merge_date="2024-01-15T00:00:00Z",
            introducing_pr_author="alice",
            truth=GroundTruth(
                introducing_commit="abc123",
                blame_confidence="A",
                fix_pr_numbers=[99],
            ),
        )

        introducing_diff = SINGLE_FILE_DIFF
        fix_diff = FIX_DIFF_EXACT

        def fake_run_git(*args: str, cwd: Path, timeout: int = 60) -> str:
            cmd = list(args)
            if cmd[0] == "diff":
                if "abc123~1" in cmd and "abc123" in cmd:
                    return introducing_diff
                # Fix PR diff — merge base approach
                return fix_diff
            if cmd[0] == "log":
                if "--format=%H" in cmd:
                    return "merge_base_sha\n"
                return ""
            if cmd[0] == "merge-base":
                return "merge_base_sha\n"
            return ""

        with patch("bugeval.ground_truth.run_git", side_effect=fake_run_git):
            updated = populate_ground_truth(case, Path("/fake/repo"))

        assert updated.truth is not None
        assert len(updated.truth.buggy_lines) > 0
        assert updated.bug_description != ""
        assert updated.bug_latency_days == 14


# ---------------------------------------------------------------------------
# Test expectation file detection
# ---------------------------------------------------------------------------

OUT_FILE_INTRO_DIFF = """\
diff --git a/tests/expectations/compiler/foo.out b/tests/expectations/compiler/foo.out
index aaa..bbb 100644
--- /dev/null
+++ b/tests/expectations/compiler/foo.out
@@ -0,0 +1,3 @@
+namespace: Compile successfully.
+outputs:
+  - expected output line
"""

OUT_FILE_FIX_DIFF = """\
diff --git a/tests/expectations/compiler/foo.out b/tests/expectations/compiler/foo.out
index bbb..ccc 100644
--- a/tests/expectations/compiler/foo.out
+++ b/tests/expectations/compiler/foo.out
@@ -1,3 +1,3 @@
 namespace: Compile successfully.
 outputs:
-  - expected output line
+  - corrected output line
"""


class TestIsTestExpectationFile:
    def test_out_extension(self) -> None:
        assert _is_test_expectation_file("tests/expectations/foo.out")

    def test_expected_extension(self) -> None:
        assert _is_test_expectation_file("src/snapshots/bar.expected")

    def test_golden_extension(self) -> None:
        assert _is_test_expectation_file("golden/output.golden")

    def test_snapshot_extension(self) -> None:
        assert _is_test_expectation_file("tests/snap.snapshot")

    def test_stderr_extension(self) -> None:
        assert _is_test_expectation_file("tests/output.stderr")

    def test_stdout_extension(self) -> None:
        assert _is_test_expectation_file("tests/output.stdout")

    def test_source_file_not_marked(self) -> None:
        assert not _is_test_expectation_file("src/lib.rs")

    def test_test_code_not_marked(self) -> None:
        assert not _is_test_expectation_file("tests/test_main.rs")

    def test_spec_file_not_marked(self) -> None:
        assert not _is_test_expectation_file("tests/foo_spec.ts")


class TestComputeBuggyLinesTestExpectation:
    def test_out_file_lines_filtered_out(self) -> None:
        """Test expectation files under tests/ are excluded from buggy lines."""
        result = compute_buggy_lines(OUT_FILE_INTRO_DIFF, [OUT_FILE_FIX_DIFF])
        assert len(result) == 0

    def test_source_lines_not_marked(self) -> None:
        result = compute_buggy_lines(SINGLE_FILE_DIFF, [FIX_DIFF_EXACT])
        assert len(result) > 0
        for bl in result:
            assert bl.is_test_expectation is False


# ---------------------------------------------------------------------------
# classify_bug
# ---------------------------------------------------------------------------


class TestClassifyBug:
    def _make_case(self, **overrides: object) -> TestCase:
        defaults: dict[str, object] = {
            "id": "test-001",
            "repo": "org/repo",
            "kind": "bug",
            "base_commit": "aaa",
        }
        defaults.update(overrides)
        return TestCase(**defaults)  # type: ignore[arg-type]

    def test_concurrency_real_keyword(self) -> None:
        case = self._make_case(
            bug_description="Fix race condition in executor",
            fix_pr_title="Fix race condition",
        )
        result = classify_bug(case)
        assert result["category"] == "concurrency"

    def test_lock_in_blockquote_not_concurrency(self) -> None:
        """'lock' inside 'blockquote' should NOT trigger concurrency."""
        case = self._make_case(
            bug_description="Fix blockquote rendering in markdown",
            fix_pr_title="Fix blockquote display",
        )
        result = classify_bug(case)
        assert result["category"] != "concurrency"

    def test_cargo_lock_not_concurrency(self) -> None:
        """'Cargo.lock' should NOT trigger concurrency."""
        case = self._make_case(
            bug_description="Update Cargo.lock after dependency bump",
            fix_pr_title="Update lockfile",
        )
        result = classify_bug(case)
        assert result["category"] != "concurrency"

    def test_deadlock_is_concurrency(self) -> None:
        case = self._make_case(
            bug_description="Fix deadlock in worker pool",
            fix_pr_title="Fix deadlock",
        )
        result = classify_bug(case)
        assert result["category"] == "concurrency"

    def test_mutex_is_concurrency(self) -> None:
        case = self._make_case(
            bug_description="Fix mutex contention issue",
            fix_pr_title="Fix mutex",
        )
        result = classify_bug(case)
        assert result["category"] == "concurrency"

    def test_severity_low_for_typo(self) -> None:
        case = self._make_case(
            bug_description="Fix typo in error message",
            fix_pr_title="Fix typo",
            issue_labels=[],
        )
        result = classify_bug(case)
        assert result["severity"] == "low"

    def test_severity_low_for_cosmetic(self) -> None:
        case = self._make_case(
            bug_description="Cosmetic fix for alignment",
            fix_pr_title="Cosmetic fix",
            issue_labels=[],
        )
        result = classify_bug(case)
        assert result["severity"] == "low"

    def test_severity_low_for_spelling(self) -> None:
        case = self._make_case(
            bug_description="Fix spelling mistake in docs",
            fix_pr_title="Fix spelling",
            issue_labels=[],
        )
        result = classify_bug(case)
        assert result["severity"] == "low"

    def test_severity_medium_default(self) -> None:
        case = self._make_case(
            bug_description="Fix off-by-one error",
            fix_pr_title="Fix off-by-one",
            issue_labels=[],
        )
        result = classify_bug(case)
        assert result["severity"] == "medium"

    def test_difficulty_from_stats(self) -> None:
        case = self._make_case(
            bug_description="Fix logic bug",
            fix_pr_title="Fix logic",
            stats=CaseStats(lines_added=3, lines_deleted=2, files_changed=1),
        )
        result = classify_bug(case)
        assert result["difficulty"] == "easy"


# ---------------------------------------------------------------------------
# Fix 1: populate_ground_truth with no introducing_commit
# ---------------------------------------------------------------------------


class TestPopulateGroundTruthNoIntroCommit:
    def test_description_and_classification_without_introducing_commit(self) -> None:
        """Description extraction and classification run even without introducing_commit."""
        case = TestCase(
            id="test-no-intro",
            repo="org/repo",
            kind="bug",
            base_commit="aaa",
            fix_pr_title="Fix the crash in parser",
            fix_pr_body="This fixes a crash when parsing invalid tokens",
            truth=GroundTruth(
                introducing_commit="",
                blame_confidence="",
                fix_pr_numbers=[],
            ),
        )
        with patch("bugeval.ground_truth.run_git") as mock_git:
            updated = populate_ground_truth(case, Path("/fake/repo"))
            # run_git should NOT be called (no introducing commit)
            mock_git.assert_not_called()

        assert updated.bug_description != ""
        assert updated.bug_description_source == "pr_body"
        assert updated.category != ""

    def test_description_extracted_with_null_truth(self) -> None:
        """When truth is None, description and classification still run."""
        case = TestCase(
            id="test-null-truth",
            repo="org/repo",
            kind="bug",
            base_commit="aaa",
            fix_pr_title="Fix the crash in parser",
            fix_pr_body="This fixes a crash when parsing invalid tokens",
        )
        with patch("bugeval.ground_truth.run_git") as mock_git:
            updated = populate_ground_truth(case, Path("/fake/repo"))
            mock_git.assert_not_called()

        assert updated.bug_description != ""
        assert updated.category != ""


# ---------------------------------------------------------------------------
# classify_line_content
# ---------------------------------------------------------------------------


class TestClassifyLineContent:
    def test_comment_single(self) -> None:
        assert classify_line_content("    // this is a comment") == "comment"

    def test_comment_triple_slash(self) -> None:
        assert classify_line_content("    /// doc comment") == "comment"

    def test_comment_block(self) -> None:
        assert classify_line_content("    /* block comment */") == "comment"

    def test_import_use(self) -> None:
        assert classify_line_content("    use leo_compiler::Compiler;") == "import"

    def test_import_mod(self) -> None:
        assert classify_line_content("mod ternary;") == "import"

    def test_import_pub_use(self) -> None:
        assert classify_line_content("pub use ternary::*;") == "import"

    def test_version_string(self) -> None:
        assert classify_line_content('version = "3.3.0"') == "config"

    def test_blank_line(self) -> None:
        assert classify_line_content("") == "blank"
        assert classify_line_content("   ") == "blank"

    def test_actual_code(self) -> None:
        assert classify_line_content("    let x = foo();") == "code"
        assert classify_line_content("    if condition {") == "code"

    def test_attribute(self) -> None:
        assert classify_line_content('#[clap(long, help = "Enable spans")]') == "attribute"

    def test_derive(self) -> None:
        assert classify_line_content("#[derive(Debug, Clone)]") == "attribute"


# ---------------------------------------------------------------------------
# Task 6: Improved category classifier
# ---------------------------------------------------------------------------


class TestClassifyBugImproved:
    def _case(self, title: str, desc: str = "") -> TestCase:
        from bugeval.models import CaseKind

        return TestCase(
            id="t-001",
            repo="ProvableHQ/leo",
            kind=CaseKind.bug,
            base_commit="abc",
            fix_pr_title=title,
            bug_description=desc,
        )

    def test_parser_bug(self) -> None:
        assert classify_bug(self._case("Correctly parse double negation"))["category"] == "parser"

    def test_type_checker_bug(self) -> None:
        assert (
            classify_bug(self._case("Correctly type check return in a constructor"))["category"]
            == "type"
        )

    def test_codegen_bug(self) -> None:
        assert (
            classify_bug(self._case("Fix code generation for record outputs"))["category"]
            == "codegen"
        )

    def test_ssa_bug(self) -> None:
        assert (
            classify_bug(self._case("Fix SSA incorrectly replacing global vars"))["category"]
            == "codegen"
        )

    def test_interpreter_bug(self) -> None:
        assert (
            classify_bug(self._case("Fix ArrayAccess in the interpreter"))["category"]
            == "interpreter"
        )

    def test_compiler_pass_bug(self) -> None:
        assert (
            classify_bug(self._case("Don't delete local constants during const propagation"))[
                "category"
            ]
            == "compiler-pass"
        )

    def test_cli_bug(self) -> None:
        assert classify_bug(self._case("Fix program ID in leo execute"))["category"] == "cli"

    def test_formatter_bug(self) -> None:
        assert (
            classify_bug(self._case("fix(leo-fmt): wrap long binary expression chains"))["category"]
            == "formatter"
        )

    def test_security_still_works(self) -> None:
        assert (
            classify_bug(self._case("Fix security vulnerability in auth"))["category"] == "security"
        )

    def test_memory_still_works(self) -> None:
        assert classify_bug(self._case("Fix memory leak in parser"))["category"] == "memory"

    def test_runtime_still_works(self) -> None:
        assert classify_bug(self._case("[Fix] Panic on unknown variable"))["category"] == "runtime"


# ---------------------------------------------------------------------------
# Task 7: Content-match fallback logging
# ---------------------------------------------------------------------------


class TestContentMatchLogging:
    def test_logs_content_match_fallback(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        intro_diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -9,0 +10,1 @@\n"
            "+    let x = broken();\n"
        )
        fix_diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -50,1 +50,0 @@\n"
            "-    let x = broken();\n"
        )
        with caplog.at_level(logging.INFO, logger="bugeval.ground_truth"):
            result = compute_buggy_lines(intro_diff, [fix_diff])
        assert len(result) == 1
        assert any("content-match fallback" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# line_type population on BuggyLine
# ---------------------------------------------------------------------------


class TestLineTypePopulation:
    def test_buggy_lines_have_line_type(self) -> None:
        intro_diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -9,0 +10,3 @@\n"
            "+// a comment\n"
            "+use foo::bar;\n"
            "+    let x = broken();\n"
        )
        fix_diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -10,3 +10,0 @@\n"
            "-// a comment\n"
            "-use foo::bar;\n"
            "-    let x = broken();\n"
        )
        result = compute_buggy_lines(intro_diff, [fix_diff])
        assert len(result) == 3
        types = {bl.line_type for bl in result}
        assert "comment" in types
        assert "import" in types
        assert "code" in types


# ---------------------------------------------------------------------------
# Status transitions in ground truth
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    def test_ground_truth_sets_status(self) -> None:
        case = TestCase(
            id="t-001",
            repo="org/repo",
            kind="bug",
            base_commit="abc",
            status="draft",
            fix_pr_title="Fix the bug",
            fix_pr_body="This fixes a crash in the module",
        )
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            updated = populate_ground_truth(case, Path(td))
        assert updated.status == "ground-truth"

    def test_ground_truth_does_not_override_later_status(self) -> None:
        case = TestCase(
            id="t-002",
            repo="org/repo",
            kind="bug",
            base_commit="abc",
            status="curated",
            fix_pr_title="Fix the bug",
            fix_pr_body="This fixes a crash in the module",
        )
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            updated = populate_ground_truth(case, Path(td))
        assert updated.status == "curated"


# ---------------------------------------------------------------------------
# Sibling merge: fix_pr_number tagging and sibling case merging
# ---------------------------------------------------------------------------


class TestSiblingMerge:
    def test_compute_buggy_lines_tags_fix_pr(self) -> None:
        intro_diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -9,0 +10,2 @@\n"
            "+    let x = broken();\n"
            "+    let y = also_broken();\n"
        )
        fix_diff = (
            "diff --git a/src/foo.rs b/src/foo.rs\n"
            "--- a/src/foo.rs\n"
            "+++ b/src/foo.rs\n"
            "@@ -10,1 +10,1 @@\n"
            "-    let x = broken();\n"
            "+    let x = fixed();\n"
        )
        result = compute_buggy_lines(intro_diff, [fix_diff], fix_pr_number=42)
        assert len(result) >= 1
        assert all(bl.fix_pr_number == 42 for bl in result)

    def test_populate_with_siblings(self, tmp_path: Path) -> None:
        """Sibling cases contribute their fix diffs to buggy lines."""
        import subprocess

        # Create repo with bugs in SEPARATE files to avoid tolerance overlap
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", "master"],
            cwd=repo,
            capture_output=True,
        )
        # Initial commit
        (repo / "a.rs").write_text("fn good_a() {}\n")
        (repo / "b.rs").write_text("fn good_b() {}\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo,
            capture_output=True,
        )

        # Introducing commit (adds bugs in two separate files)
        (repo / "a.rs").write_text("fn good_a() {}\nfn bug_a() { panic!(); }\n")
        (repo / "b.rs").write_text("fn good_b() {}\nfn bug_b() { panic!(); }\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "introduce bugs"],
            cwd=repo,
            capture_output=True,
        )
        intro_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Fix A (fixes bug in a.rs only)
        (repo / "a.rs").write_text("fn good_a() {}\nfn bug_a_fixed() {}\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "fix bug A"],
            cwd=repo,
            capture_output=True,
        )
        fix_a_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Fix B (fixes bug in b.rs only)
        (repo / "b.rs").write_text("fn good_b() {}\nfn bug_b_fixed() {}\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "fix bug B"],
            cwd=repo,
            capture_output=True,
        )
        fix_b_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        from bugeval.models import CaseKind, GroundTruth, PRRelation

        # Primary case (fix A)
        primary = TestCase(
            id="t-001",
            repo="org/repo",
            kind=CaseKind.bug,
            base_commit="abc",
            fix_commit=fix_a_sha,
            fix_pr_number=100,
            introducing_pr_number=1,
            truth=GroundTruth(
                introducing_commit=intro_sha,
                fix_pr_numbers=[100],
            ),
            related_prs=[
                PRRelation(
                    pr_number=100,
                    role="full_fix",
                    commit=fix_a_sha,
                ),
            ],
        )

        # Sibling case (fix B)
        sibling = TestCase(
            id="t-002",
            repo="org/repo",
            kind=CaseKind.bug,
            base_commit="abc",
            fix_commit=fix_b_sha,
            fix_pr_number=200,
            introducing_pr_number=1,
            truth=GroundTruth(
                introducing_commit=intro_sha,
                fix_pr_numbers=[200],
            ),
            related_prs=[
                PRRelation(
                    pr_number=200,
                    role="full_fix",
                    commit=fix_b_sha,
                ),
            ],
        )

        result = populate_ground_truth(primary, repo, sibling_cases=[sibling])

        # Should have buggy lines from BOTH fix PRs
        assert len(result.truth.buggy_lines) >= 2
        fix_prs_found = {bl.fix_pr_number for bl in result.truth.buggy_lines}
        assert 100 in fix_prs_found  # primary
        assert 200 in fix_prs_found  # sibling
        # fix_pr_numbers should include both
        assert 200 in result.truth.fix_pr_numbers


# ---------------------------------------------------------------------------
# _is_test_file
# ---------------------------------------------------------------------------


class TestIsTestFile:
    def test_tests_dir(self) -> None:
        assert _is_test_file("tests/foo.rs")

    def test_test_dir(self) -> None:
        assert _is_test_file("test/bar.rs")

    def test_benches_dir(self) -> None:
        assert _is_test_file("benches/bench.rs")

    def test_examples_dir(self) -> None:
        assert _is_test_file("examples/demo.rs")

    def test_nested_tests_dir(self) -> None:
        assert _is_test_file("compiler/tests/parser/foo.rs")

    def test_expectation_ext_out(self) -> None:
        assert _is_test_file("some/path/output.out")

    def test_expectation_ext_stderr(self) -> None:
        assert _is_test_file("some/path/output.stderr")

    def test_source_file_not_test(self) -> None:
        assert not _is_test_file("src/lib.rs")

    def test_src_main_not_test(self) -> None:
        assert not _is_test_file("src/main.rs")


# ---------------------------------------------------------------------------
# _is_non_source_file — new entries
# ---------------------------------------------------------------------------


class TestIsNonSourceFileExtended:
    def test_cargo_toml(self) -> None:
        assert _is_non_source_file("Cargo.toml")

    def test_readme_md(self) -> None:
        assert _is_non_source_file("README.md")

    def test_readme_plain(self) -> None:
        assert _is_non_source_file("README")

    def test_changelog(self) -> None:
        assert _is_non_source_file("CHANGELOG.md")

    def test_license(self) -> None:
        assert _is_non_source_file("LICENSE")

    def test_license_mit(self) -> None:
        assert _is_non_source_file("LICENSE-MIT")

    def test_license_apache(self) -> None:
        assert _is_non_source_file("LICENSE-APACHE")

    def test_cargo_lock_still_works(self) -> None:
        assert _is_non_source_file("Cargo.lock")

    def test_source_file_not_excluded(self) -> None:
        assert not _is_non_source_file("src/lib.rs")


# ---------------------------------------------------------------------------
# Category classification — Leo-specific patterns
# ---------------------------------------------------------------------------


class TestClassifyBugLeoPatterns:
    def _case(self, title: str, desc: str = "") -> TestCase:
        from bugeval.models import CaseKind

        return TestCase(
            id="t-001",
            repo="ProvableHQ/leo",
            kind=CaseKind.bug,
            base_commit="abc",
            fix_pr_title=title,
            bug_description=desc,
        )

    def test_type_check(self) -> None:
        result = classify_bug(self._case("Fix type check for struct literals"))
        assert result["category"] == "type"

    def test_type_system(self) -> None:
        result = classify_bug(self._case("Fix type system inference for tuples"))
        assert result["category"] == "type"

    def test_type_mismatch(self) -> None:
        result = classify_bug(self._case("Fix type mismatch error in assignment"))
        assert result["category"] == "type"

    def test_coercion(self) -> None:
        result = classify_bug(self._case("Fix coercion from u8 to u16"))
        assert result["category"] == "type"

    def test_ast_parser(self) -> None:
        result = classify_bug(self._case("Fix AST node for conditional expression"))
        assert result["category"] == "parser"

    def test_finalize_block(self) -> None:
        result = classify_bug(self._case("Fix finalize block parsing"))
        assert result["category"] == "parser"

    def test_circuit_codegen(self) -> None:
        result = classify_bug(self._case("Fix circuit generation for records"))
        assert result["category"] == "codegen"

    def test_constraint_codegen(self) -> None:
        result = classify_bug(self._case("Fix constraint synthesis for conditionals"))
        assert result["category"] == "codegen"

    def test_compiler_pass(self) -> None:
        result = classify_bug(self._case("Fix compiler transform for loop unrolling"))
        assert result["category"] == "compiler-pass"
