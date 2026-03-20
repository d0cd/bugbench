"""Tests for gen_clean_cases module."""

from __future__ import annotations

from bugeval.gen_clean_cases import _has_fix_signal, _pr_to_case


class TestHasFixSignal:
    def test_fix_keyword_in_title(self) -> None:
        assert _has_fix_signal("Fix off-by-one in parser", [])

    def test_bug_keyword_in_title(self) -> None:
        assert _has_fix_signal("bug: null pointer crash", [])

    def test_revert_in_title(self) -> None:
        assert _has_fix_signal("Revert broken change", [])

    def test_clean_title_no_labels(self) -> None:
        assert not _has_fix_signal("Add user profile feature", [])

    def test_refactor_title(self) -> None:
        assert not _has_fix_signal("refactor: extract helper module", [])

    def test_bug_label(self) -> None:
        assert _has_fix_signal("Update config", [{"name": "bug"}])

    def test_feature_label_only(self) -> None:
        assert not _has_fix_signal("Add feature", [{"name": "enhancement"}])

    def test_string_labels(self) -> None:
        assert _has_fix_signal("Update config", ["bug"])


class TestPrToCase:
    def test_basic_conversion(self) -> None:
        pr = {
            "number": 42,
            "title": "Add user profile page",
            "body": "This PR adds a new profile page",
            "labels": [{"name": "feature"}],
            "mergeCommit": {"oid": "abc123def456abc123def456abc123def456abc123"},
            "files": [{"path": "src/profile.rs"}],
            "additions": 50,
            "deletions": 10,
            "changedFiles": 2,
        }
        case = _pr_to_case(pr, "org/repo", "repo-clean-001")
        assert case is not None
        assert case.id == "repo-clean-001"
        assert case.case_type == "clean"
        assert case.expected_findings == []
        assert case.pr_number == 42
        assert case.language == "rust"
        assert case.stats is not None
        assert case.stats.lines_added == 50

    def test_no_merge_commit_returns_none(self) -> None:
        pr = {
            "number": 1,
            "title": "test",
            "body": "",
            "labels": [],
            "mergeCommit": None,
            "files": [],
            "additions": 0,
            "deletions": 0,
        }
        assert _pr_to_case(pr, "org/repo", "clean-001") is None


def test_gen_clean_cases_help() -> None:
    from click.testing import CliRunner

    from bugeval.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["gen-clean-cases", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output
    assert "--dry-run" in result.output
