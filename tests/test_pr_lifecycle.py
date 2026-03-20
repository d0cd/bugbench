"""Tests for pr_lifecycle helpers."""

from pathlib import Path
from unittest.mock import patch

from bugeval.github_scraper import GhError
from bugeval.models import Category, Difficulty, PRSize, Severity, TestCase
from bugeval.pr_lifecycle import (
    apply_patch_to_branch,
    close_pr_delete_branch,
    make_branch_name,
    open_pr,
    poll_for_review,
    request_review,
    scrape_review_comments,
)


def _make_case(case_id: str = "case-001") -> TestCase:
    return TestCase(
        id=case_id,
        repo="provable-org/aleo-lang",
        base_commit="abc123",
        head_commit="def456",
        fix_commit="def456",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.small,
        description="A test bug case for unit testing",
        expected_findings=[],
    )


def test_make_branch_name_format() -> None:
    branch = make_branch_name("case-001", "coderabbit")
    assert branch.startswith("bugeval/")
    assert "case-001" in branch
    assert "coderabbit" in branch


def test_make_branch_name_sanitization() -> None:
    branch = make_branch_name("case with spaces", "tool@name!")
    assert " " not in branch
    assert "@" not in branch
    assert "!" not in branch
    assert branch.startswith("bugeval/")


def test_make_branch_name_max_length() -> None:
    long_case_id = "a" * 100
    long_tool = "b" * 100
    branch = make_branch_name(long_case_id, long_tool)
    assert len(branch) <= 80


def test_apply_patch_to_branch_calls_git(tmp_path: Path) -> None:
    patch_path = tmp_path / "test.patch"
    patch_path.write_text("--- a/foo.rs\n+++ b/foo.rs\n")
    branch = "bugeval/case-001-coderabbit"

    with patch("bugeval.pr_lifecycle.run_git") as mock_git:
        apply_patch_to_branch(
            branch=branch,
            base_commit="abc123",
            patch_path=patch_path,
            fork_url="git@github.com:eval-org/aleo-lang-coderabbit.git",
            cwd=tmp_path,
        )

    calls = [c[0] for c in mock_git.call_args_list]
    # checkout -b
    assert calls[0][0] == "checkout"
    assert "-b" in calls[0]
    # git apply (not git am)
    apply_args = [c for c in calls if c[0] == "apply"]
    assert len(apply_args) == 1
    # commit with expected message
    commit_args = [c for c in calls if c[0] == "commit"]
    assert len(commit_args) == 1
    commit_flat = " ".join(commit_args[0])
    assert f"bugeval: apply patch for {branch}" in commit_flat
    # push to fork URL
    push_args = [c for c in calls if c[0] == "push"]
    assert len(push_args) == 1
    assert "git@github.com:eval-org/aleo-lang-coderabbit.git" in push_args[0]


def test_open_pr_fallback_when_no_pull_in_url() -> None:
    """When gh pr create output lacks /pull/N, fall back to pr list."""
    case = _make_case()
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        # First call: pr create returns URL without /pull/
        # Second call: pr list returns the number
        mock_gh.side_effect = [
            "https://github.com/eval-org/aleo-lang-coderabbit\n",  # no /pull/
            '[{"number": 99}]',
        ]
        result = open_pr(
            fork_repo="eval-org/aleo-lang-coderabbit",
            upstream_repo="provable-org/aleo-lang",
            branch="bugeval/case-001-coderabbit",
            case=case,
            dry_run=False,
        )
    assert result == 99
    assert mock_gh.call_count == 2


def test_open_pr_dry_run_returns_zero() -> None:
    case = _make_case()
    result = open_pr(
        fork_repo="eval-org/aleo-lang-coderabbit",
        upstream_repo="provable-org/aleo-lang",
        branch="bugeval/case-001-coderabbit",
        case=case,
        dry_run=True,
    )
    assert result == 0


def test_open_pr_extracts_number_from_url() -> None:
    case = _make_case()
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.return_value = "https://github.com/eval-org/aleo-lang-coderabbit/pull/42\n"
        result = open_pr(
            fork_repo="eval-org/aleo-lang-coderabbit",
            upstream_repo="provable-org/aleo-lang",
            branch="bugeval/case-001-coderabbit",
            case=case,
            dry_run=False,
        )
    assert result == 42


def test_open_pr_uses_correct_base_branch() -> None:
    """PR must be opened on the fork repo with --base main (not owner:repo:branch format).

    Tools are installed on fork repos, so intra-fork PRs (bug_branch → main)
    correctly trigger the review tools.
    """
    case = _make_case()
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.return_value = "https://github.com/eval-org/aleo-lang-coderabbit/pull/1\n"
        open_pr(
            fork_repo="eval-org/aleo-lang-coderabbit",
            upstream_repo="provable-org/aleo-lang",
            branch="bugeval/case-001-coderabbit",
            case=case,
            dry_run=False,
        )
    call_args = mock_gh.call_args[0]
    # Verify --repo is the fork (not upstream)
    assert "eval-org/aleo-lang-coderabbit" in call_args
    # Verify --base is just "main", not "provable-org:aleo-lang:main"
    base_idx = list(call_args).index("--base")
    assert call_args[base_idx + 1] == "main"


def test_poll_for_review_gh_error_continues() -> None:
    """GhError during polling is swallowed; loop continues until timeout."""
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.side_effect = GhError(["gh", "api", "..."], "rate limited")
        result = poll_for_review(
            fork_repo="eval-org/aleo-lang-coderabbit",
            pr_number=42,
            timeout_seconds=1,
            poll_interval=2,
        )
    assert result is False


def test_poll_for_review_timeout() -> None:
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.return_value = "[]"
        result = poll_for_review(
            fork_repo="eval-org/aleo-lang-coderabbit",
            pr_number=42,
            timeout_seconds=1,
            poll_interval=2,
        )
    assert result is False


def test_poll_for_review_success() -> None:
    review_data = '[{"id": 1, "state": "COMMENTED", "body": "looks wrong"}]'
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.return_value = review_data
        result = poll_for_review(
            fork_repo="eval-org/aleo-lang-coderabbit",
            pr_number=42,
            timeout_seconds=60,
            poll_interval=1,
        )
    assert result is True


def test_scrape_review_comments_partial_failure() -> None:
    """If reviews endpoint fails, inline and issue comments are still returned."""
    inline = '[{"id": 2, "body": "wrong line"}]'
    issue_comments = '[{"id": 3, "body": "tool finding"}]'
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.side_effect = [GhError(["gh", "api", "..."], "not found"), inline, issue_comments]
        result = scrape_review_comments("eval-org/aleo-lang-coderabbit", 42)
    assert len(result) == 2
    sources = {r["source"] for r in result}
    assert sources == {"inline_comment", "issue_comment"}


def test_scrape_review_comments_combines_sources() -> None:
    reviews = '[{"id": 1, "body": "issue found", "state": "COMMENTED"}]'
    inline = '[{"id": 2, "body": "wrong line", "path": "src/main.rs"}]'
    issue_comments = '[{"id": 3, "body": "tool comment on PR thread"}]'

    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.side_effect = [reviews, inline, issue_comments]
        result = scrape_review_comments("eval-org/aleo-lang-coderabbit", 42)

    assert len(result) == 3
    sources = {r["source"] for r in result}
    assert sources == {"review", "inline_comment", "issue_comment"}


def test_scrape_review_comments_issue_comment_failure_continues() -> None:
    """If issue comments endpoint fails, reviews and inline comments still returned."""
    reviews = '[{"id": 1, "body": "review body"}]'
    inline = '[{"id": 2, "body": "inline body", "path": "src/lib.rs"}]'
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.side_effect = [reviews, inline, GhError(["gh", "api", "..."], "forbidden")]
        result = scrape_review_comments("eval-org/aleo-lang-coderabbit", 42)
    assert len(result) == 2
    sources = {r["source"] for r in result}
    assert sources == {"review", "inline_comment"}


def test_open_pr_returns_zero_when_fallback_empty() -> None:
    """When gh pr create output has no /pull/ and pr list returns empty, return 0."""
    case = _make_case()
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.side_effect = [
            "https://github.com/eval-org/aleo-lang-coderabbit\n",  # no /pull/
            "[]",  # empty pr list
        ]
        result = open_pr(
            fork_repo="eval-org/aleo-lang-coderabbit",
            upstream_repo="provable-org/aleo-lang",
            branch="bugeval/case-001-coderabbit",
            case=case,
            dry_run=False,
        )
    assert result == 0


def test_scrape_inline_comment_failure_continues() -> None:
    """If inline comments endpoint fails, reviews and issue comments still returned."""
    reviews = '[{"id": 1, "body": "review body"}]'
    issue_comments = '[{"id": 3, "body": "pr thread comment"}]'
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.side_effect = [reviews, GhError(["gh", "api", "..."], "not found"), issue_comments]
        result = scrape_review_comments("eval-org/aleo-lang-coderabbit", 42)
    assert len(result) == 2
    sources = {r["source"] for r in result}
    assert sources == {"review", "issue_comment"}


def test_close_pr_dry_run_no_call() -> None:
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        close_pr_delete_branch(
            fork_repo="eval-org/aleo-lang-coderabbit",
            pr_number=42,
            branch="bugeval/case-001-coderabbit",
            dry_run=True,
        )
    mock_gh.assert_not_called()


def test_close_pr_real_calls_gh() -> None:
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        close_pr_delete_branch(
            fork_repo="eval-org/aleo-lang-coderabbit",
            pr_number=42,
            branch="bugeval/case-001-coderabbit",
            dry_run=False,
        )
    mock_gh.assert_called_once_with(
        "pr", "close", "42", "--repo", "eval-org/aleo-lang-coderabbit", "--delete-branch"
    )


def test_request_review_calls_gh() -> None:
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        request_review(
            fork_repo="eval-org/aleo-lang-copilot",
            pr_number=7,
            reviewer="copilot",
        )
    mock_gh.assert_called_once_with(
        "api",
        "repos/eval-org/aleo-lang-copilot/pulls/7/requested_reviewers",
        "--method", "POST",
        "-f", "reviewers[]=copilot",
    )


def test_request_review_dry_run_no_call() -> None:
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        request_review(
            fork_repo="eval-org/aleo-lang-copilot",
            pr_number=7,
            reviewer="copilot",
            dry_run=True,
        )
    mock_gh.assert_not_called()
