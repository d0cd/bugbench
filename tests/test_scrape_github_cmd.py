"""Tests for the scrape-github CLI command."""

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from bugeval.scrape_github_cmd import scrape_github


def _make_issue(number: int = 1) -> dict[str, object]:
    return {
        "number": number,
        "title": f"Bug #{number}",
        "body": f"Some bug {number}",
        "labels": [{"name": "bug"}],
        "closedAt": "2024-01-01T00:00:00Z",
    }


def _make_pr(number: int = 100, issue_num: int = 1) -> dict[str, object]:
    return {
        "number": number,
        "title": f"Fix bug #{issue_num}",
        "body": f"Fixes #{issue_num}",
        "labels": [],
        "mergeCommit": {"oid": "abc123def456abc123def456abc123def456abc1"},
        "baseRefName": "main",
        "headRefName": "fix/bug",
        "files": [{"path": "src/main.rs"}],
        "additions": 10,
        "deletions": 5,
        "changedFiles": 1,
        "closedIssues": [{"number": issue_num}],
    }


class TestScrapeGithubHelp:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(scrape_github, ["--help"])
        assert result.exit_code == 0
        assert "--repo" in result.output
        assert "--limit" in result.output
        assert "--min-confidence" in result.output
        assert "--dry-run" in result.output

    def test_missing_repo_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(scrape_github, [])
        assert result.exit_code != 0


class TestScrapeGithubDryRun:
    def _make_issue(self, number: int = 1) -> dict[str, object]:
        return {
            "number": number,
            "title": f"Bug #{number}",
            "body": f"Some bug {number}",
            "labels": [{"name": "bug"}],
            "closedAt": "2024-01-01T00:00:00Z",
        }

    def _make_pr(self, number: int = 100, issue_num: int = 1) -> dict[str, object]:
        return {
            "number": number,
            "title": f"Fix bug #{issue_num}",
            "body": f"Fixes #{issue_num}",
            "labels": [],
            "mergeCommit": {"oid": "abc123def456abc123def456abc123def456abc1"},
            "baseRefName": "main",
            "headRefName": "fix/bug",
            "files": [{"path": "src/main.rs"}],
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "closedIssues": [{"number": issue_num}],
        }

    def test_dry_run_prints_to_stdout(self, tmp_path: Path) -> None:
        runner = CliRunner()
        issues = [self._make_issue(1)]
        prs = [self._make_pr(100, 1)]

        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=issues),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=prs),
            patch("bugeval.scrape_github_cmd.fetch_pr_diff", return_value=[]),
        ):
            result = runner.invoke(
                scrape_github,
                [
                    "--repo",
                    "foo/bar",
                    "--dry-run",
                    "--output-dir",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        assert "foo/bar" in result.output

    def test_dry_run_writes_no_files(self, tmp_path: Path) -> None:
        runner = CliRunner()
        issues = [self._make_issue(1)]
        prs = [self._make_pr(100, 1)]

        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=issues),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=prs),
            patch("bugeval.scrape_github_cmd.fetch_pr_diff", return_value=[]),
        ):
            runner.invoke(
                scrape_github,
                ["--repo", "foo/bar", "--dry-run", "--output-dir", str(tmp_path)],
            )
        yaml_files = list(tmp_path.glob("*.yaml"))
        assert len(yaml_files) == 0

    def test_writes_candidates_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        issues = [self._make_issue(1)]
        prs = [self._make_pr(100, 1)]

        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=issues),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=prs),
            patch("bugeval.scrape_github_cmd.fetch_pr_diff", return_value=[]),
        ):
            result = runner.invoke(
                scrape_github,
                ["--repo", "foo/bar", "--output-dir", str(tmp_path)],
            )
        assert result.exit_code == 0
        yaml_files = list(tmp_path.glob("*.yaml"))
        # At least the candidates file
        candidate_files = [f for f in yaml_files if "bar" in f.name]
        assert len(candidate_files) >= 1

    def test_min_confidence_filters(self, tmp_path: Path) -> None:
        runner = CliRunner()
        # Issue with no bug label → low confidence
        issue = {
            "number": 1,
            "title": "Unrelated",
            "body": "",
            "labels": [],
            "closedAt": "2024-01-01T00:00:00Z",
        }
        pr = self._make_pr(100, 1)
        pr["closedIssues"] = [{"number": 1}]

        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=[issue]),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=[pr]),
            patch("bugeval.scrape_github_cmd.fetch_pr_diff", return_value=[]),
        ):
            result = runner.invoke(
                scrape_github,
                [
                    "--repo",
                    "foo/bar",
                    "--min-confidence",
                    "0.9",
                    "--dry-run",
                    "--output-dir",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        # High threshold → fewer/no candidates printed
        assert "0 candidates" in result.output or "No candidates" in result.output


class TestScrapeGithubSince:
    def test_since_forwarded_to_fetch_issues(self, tmp_path: Path) -> None:
        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=[]) as mock_issues,
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=[]),
        ):
            runner = CliRunner()
            runner.invoke(
                scrape_github,
                [
                    "--repo",
                    "foo/bar",
                    "--since",
                    "2024-01-01",
                    "--dry-run",
                    "--output-dir",
                    str(tmp_path),
                ],
            )
        assert mock_issues.call_args.kwargs.get("since") == "2024-01-01"

    def test_since_forwarded_to_fetch_prs(self, tmp_path: Path) -> None:
        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=[]),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=[]) as mock_prs,
        ):
            runner = CliRunner()
            runner.invoke(
                scrape_github,
                [
                    "--repo",
                    "foo/bar",
                    "--since",
                    "2024-06-01",
                    "--dry-run",
                    "--output-dir",
                    str(tmp_path),
                ],
            )
        assert mock_prs.call_args.kwargs.get("since") == "2024-06-01"


class TestScrapeGithubStateFile:
    def test_state_file_is_per_repo(self, tmp_path: Path) -> None:
        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=[_make_issue(1)]),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=[_make_pr(100, 1)]),
            patch("bugeval.scrape_github_cmd.fetch_pr_diff", return_value=[]),
        ):
            runner = CliRunner()
            runner.invoke(
                scrape_github,
                ["--repo", "foo/bar", "--output-dir", str(tmp_path)],
            )
        # State file must be named after repo slug, not .scrape_state.yaml
        assert (tmp_path / "foo-bar.state.yaml").exists()
        assert not (tmp_path / ".scrape_state.yaml").exists()

    def test_corrupted_state_handled_gracefully(self, tmp_path: Path) -> None:
        # Create a corrupted state file
        (tmp_path / "foo-bar.state.yaml").write_text("not: valid: yaml: [unclosed")

        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=[]),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=[]),
        ):
            runner = CliRunner()
            result = runner.invoke(
                scrape_github,
                ["--repo", "foo/bar", "--dry-run", "--output-dir", str(tmp_path)],
            )
        # Must not crash with a stack trace
        assert result.exit_code == 0
