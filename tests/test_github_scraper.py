"""Tests for GitHub scraper: scoring, linking, and utility functions. No gh calls."""

from datetime import datetime
from pathlib import Path

from bugeval.github_scraper import (
    _extract_issue_numbers,
    compute_pr_size,
    detect_language,
    extract_expected_findings,
    filter_already_processed,
    link_issues_to_prs,
    load_scrape_state,
    save_scrape_state,
    score_candidate,
)
from bugeval.models import PRSize, ScrapeState


def make_issue(
    number: int = 1,
    labels: list[str] | None = None,
    title: str = "",
    body: str = "",
) -> dict[str, object]:
    if labels is None:
        labels = ["bug"]
    return {
        "number": number,
        "title": title or f"Bug in feature #{number}",
        "body": body or f"Description of bug {number}",
        "labels": [{"name": lbl} for lbl in labels],
        "closedAt": "2024-01-01T00:00:00Z",
    }


def make_pr(
    number: int = 100,
    title: str = "Fix bug in feature",
    body: str = "",
    closed_issues: list[int] | None = None,
    additions: int = 10,
    deletions: int = 5,
    labels: list[str] | None = None,
) -> dict[str, object]:
    ci = closed_issues or []
    return {
        "number": number,
        "title": title,
        "body": body or (f"Fixes #{ci[0]}" if ci else ""),
        "labels": [{"name": lbl} for lbl in (labels or [])],
        "mergeCommit": {"oid": "abc123def456abc123def456abc123def456abc1"},
        "baseRefName": "main",
        "headRefName": "fix/bug",
        "files": [{"path": "src/main.rs"}],
        "additions": additions,
        "deletions": deletions,
        "changedFiles": 1,
        "closedIssues": [{"number": n} for n in ci],
    }


class TestScoreCandidate:
    def test_high_confidence_with_bug_label_and_reference(self) -> None:
        issue = make_issue(1, labels=["bug"])
        pr = make_pr(100, title="Fix bug #1", closed_issues=[1], additions=20, deletions=10)
        confidence, signals = score_candidate(issue, pr)
        assert confidence > 0.5
        assert "has_bug_label" in signals
        assert "pr_references_issue" in signals

    def test_low_confidence_no_bug_label_no_reference_large_diff(self) -> None:
        issue = make_issue(1, labels=["enhancement"])
        pr = make_pr(
            100,
            title="Add feature",
            body="Unrelated PR",
            closed_issues=[],
            additions=300,
            deletions=200,
        )
        confidence, signals = score_candidate(issue, pr)
        assert confidence < 0.5
        assert "has_bug_label" not in signals

    def test_small_diff_adds_signal(self) -> None:
        issue = make_issue(1, labels=[])
        pr = make_pr(100, body="", closed_issues=[], additions=5, deletions=3)
        _, signals = score_candidate(issue, pr)
        assert "small_diff" in signals

    def test_confidence_capped_at_1(self) -> None:
        issue = make_issue(1, labels=["bug"])
        pr = make_pr(100, title="Fix bug", closed_issues=[1], additions=5, deletions=3)
        confidence, _ = score_candidate(issue, pr)
        assert confidence <= 1.0

    def test_fix_keywords_in_title(self) -> None:
        issue = make_issue(1, labels=[])
        pr = make_pr(100, title="fix: resolve the regression", body="", closed_issues=[])
        _, signals = score_candidate(issue, pr)
        assert "fix_keywords_in_title" in signals

    def test_regression_label_counts_as_bug(self) -> None:
        issue = make_issue(1, labels=["regression"])
        pr = make_pr(100, body="", closed_issues=[])
        _, signals = score_candidate(issue, pr)
        assert "has_bug_label" in signals

    def test_has_linked_issue_not_double_counted(self) -> None:
        # When pr_references_issue fires for this issue, has_linked_issue must NOT also fire
        issue = make_issue(1, labels=["bug"])
        pr = make_pr(100, title="Fix bug", closed_issues=[1])
        _, signals = score_candidate(issue, pr)
        assert "pr_references_issue" in signals
        assert "has_linked_issue" not in signals

    def test_has_linked_issue_fires_for_unrelated_link(self) -> None:
        # PR links to issue #99 but is being scored against issue #1 (no direct link)
        issue = make_issue(1, labels=["bug"])
        pr = make_pr(100, title="Add feature", body="Fixes #99", closed_issues=[99])
        _, signals = score_candidate(issue, pr)
        assert "pr_references_issue" not in signals
        assert "has_linked_issue" in signals


class TestLinkIssuesToPrs:
    def test_link_via_closed_issues(self) -> None:
        issue = make_issue(1)
        pr = make_pr(100, closed_issues=[1])
        pairs = link_issues_to_prs([issue], [pr])
        assert len(pairs) == 1
        assert pairs[0][0]["number"] == 1
        assert pairs[0][1]["number"] == 100

    def test_link_via_body_reference(self) -> None:
        issue = make_issue(42)
        pr = make_pr(200, body="This closes #42 by fixing the race condition", closed_issues=[])
        pairs = link_issues_to_prs([issue], [pr])
        assert len(pairs) == 1
        assert pairs[0][0]["number"] == 42

    def test_link_via_title_reference(self) -> None:
        issue = make_issue(7)
        pr = make_pr(300, title="Fixes #7: off-by-one error", body="", closed_issues=[])
        pairs = link_issues_to_prs([issue], [pr])
        assert len(pairs) == 1

    def test_no_match(self) -> None:
        issue = make_issue(99)
        pr = make_pr(100, body="Unrelated PR", closed_issues=[])
        pairs = link_issues_to_prs([issue], [pr])
        assert len(pairs) == 0

    def test_multiple_issues_prs(self) -> None:
        issues = [make_issue(1), make_issue(2), make_issue(3)]
        prs = [make_pr(10, closed_issues=[1]), make_pr(20, closed_issues=[2])]
        pairs = link_issues_to_prs(issues, prs)
        assert len(pairs) == 2

    def test_same_issue_not_matched_twice(self) -> None:
        issue = make_issue(1)
        pr1 = make_pr(10, closed_issues=[1])
        pr2 = make_pr(20, body="Also fixes #1", closed_issues=[])
        pairs = link_issues_to_prs([issue], [pr1, pr2])
        # Issue 1 should only appear once
        linked_issues = [p[0]["number"] for p in pairs]
        assert linked_issues.count(1) == 1


class TestExtractIssueNumbers:
    def test_fixes_pattern(self) -> None:
        assert _extract_issue_numbers("fixes #123") == {123}

    def test_closes_pattern(self) -> None:
        assert _extract_issue_numbers("closes #456") == {456}

    def test_fix_pattern(self) -> None:
        assert _extract_issue_numbers("fix #7") == {7}

    def test_multiple_references(self) -> None:
        assert _extract_issue_numbers("fixes #1, closes #2") == {1, 2}

    def test_no_references(self) -> None:
        assert _extract_issue_numbers("unrelated text #not-a-number") == set()

    def test_case_insensitive(self) -> None:
        assert _extract_issue_numbers("Fixes #10") == {10}


class TestDetectLanguage:
    def test_rust_files(self) -> None:
        assert detect_language(["src/main.rs", "src/lib.rs", "src/parser.rs"]) == "rust"

    def test_python_files(self) -> None:
        assert detect_language(["main.py", "utils.py"]) == "python"

    def test_typescript_files(self) -> None:
        assert detect_language(["app.ts", "component.tsx"]) == "typescript"

    def test_mixed_files_dominant(self) -> None:
        result = detect_language(["a.rs", "b.rs", "c.py"])
        assert result == "rust"

    def test_unknown_extensions(self) -> None:
        assert detect_language(["readme.md", "config.toml"]) == "unknown"

    def test_empty_list(self) -> None:
        assert detect_language([]) == "unknown"


class TestComputePRSize:
    def test_tiny(self) -> None:
        assert compute_pr_size(3, 5) == PRSize.tiny

    def test_small(self) -> None:
        assert compute_pr_size(30, 15) == PRSize.small

    def test_medium(self) -> None:
        assert compute_pr_size(100, 80) == PRSize.medium

    def test_large(self) -> None:
        assert compute_pr_size(300, 150) == PRSize.large

    def test_xl(self) -> None:
        assert compute_pr_size(400, 200) == PRSize.xl

    def test_boundary_tiny_small(self) -> None:
        assert compute_pr_size(5, 4) == PRSize.tiny  # 9 = tiny
        assert compute_pr_size(5, 5) == PRSize.small  # 10 = small


class TestFilterAlreadyProcessed:
    def test_no_state_returns_all(self) -> None:
        prs = [make_pr(1), make_pr(2)]
        result = filter_already_processed(prs, None)
        assert len(result) == 2

    def test_filter_removes_processed(self) -> None:
        state = ScrapeState(
            repo="foo/bar",
            last_scraped_at=datetime.now(),
            processed_pr_numbers=[1, 2],
        )
        prs = [make_pr(1), make_pr(2), make_pr(3)]
        result = filter_already_processed(prs, state)
        assert len(result) == 1
        assert result[0]["number"] == 3

    def test_empty_state_returns_all(self) -> None:
        state = ScrapeState(
            repo="foo/bar",
            last_scraped_at=datetime.now(),
            processed_pr_numbers=[],
        )
        prs = [make_pr(1), make_pr(2)]
        result = filter_already_processed(prs, state)
        assert len(result) == 2


class TestExtractExpectedFindings:
    def test_extracts_from_patch(self) -> None:
        pr_diff_files = [
            {
                "filename": "src/main.rs",
                "patch": "@@ -1,3 +1,4 @@\n context\n+added line\n context\n",
            }
        ]
        findings = extract_expected_findings(pr_diff_files)
        assert len(findings) >= 1
        assert findings[0].file == "src/main.rs"
        assert findings[0].line == 1

    def test_empty_patch_skipped(self) -> None:
        findings = extract_expected_findings([{"filename": "x.rs", "patch": ""}])
        assert findings == []

    def test_missing_patch_skipped(self) -> None:
        findings = extract_expected_findings([{"filename": "x.rs"}])
        assert findings == []

    def test_summary_starts_with_auto(self) -> None:
        pr_diff_files = [
            {
                "filename": "lib.rs",
                "patch": "@@ -5,3 +5,4 @@\n ctx\n+new line\n ctx\n",
            }
        ]
        findings = extract_expected_findings(pr_diff_files)
        assert findings[0].summary.startswith("[auto]")


class TestScrapeStateRoundTrip:
    def test_round_trip(self, tmp_path: Path) -> None:
        state = ScrapeState(
            repo="foo/bar",
            last_scraped_at=datetime(2024, 1, 15, 12, 0, 0),
            processed_pr_numbers=[1, 2, 3],
        )
        path = tmp_path / "foo-bar.state.yaml"
        save_scrape_state(state, path)

        loaded = load_scrape_state(path)
        assert loaded is not None
        assert loaded.repo == "foo/bar"
        assert loaded.processed_pr_numbers == [1, 2, 3]

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        result = load_scrape_state(tmp_path / "nonexistent.yaml")
        assert result is None
