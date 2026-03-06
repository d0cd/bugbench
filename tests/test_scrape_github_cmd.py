"""Tests for the scrape-github CLI command."""

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from bugeval.scrape_github_cmd import scrape_benchmark, scrape_github


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


class TestFetchByLabelFlag:
    def test_fetch_by_label_calls_fetch_prs_by_label(self, tmp_path: Path) -> None:
        """--fetch-by-label should invoke fetch_prs_by_label."""
        runner = CliRunner()

        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=[]),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=[]),
            patch(
                "bugeval.scrape_github_cmd.fetch_prs_by_label", return_value=[]
            ) as mock_label,
        ):
            result = runner.invoke(
                scrape_github,
                [
                    "--repo", "foo/bar",
                    "--fetch-by-label",
                    "--dry-run",
                    "--output-dir", str(tmp_path),
                ],
            )

        assert result.exit_code == 0
        mock_label.assert_called_once()

    def test_without_flag_does_not_call_fetch_prs_by_label(self, tmp_path: Path) -> None:
        """Without --fetch-by-label, fetch_prs_by_label should NOT be called."""
        runner = CliRunner()

        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=[]),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=[]),
            patch(
                "bugeval.scrape_github_cmd.fetch_prs_by_label", return_value=[]
            ) as mock_label,
        ):
            runner.invoke(
                scrape_github,
                [
                    "--repo", "foo/bar",
                    "--dry-run",
                    "--output-dir", str(tmp_path),
                ],
            )

        mock_label.assert_not_called()

    def test_label_candidates_merged_with_existing(self, tmp_path: Path) -> None:
        """Label-based candidates should be merged (deduplicated) with existing candidates."""
        issue = {
            "number": 1,
            "title": "Bug #1",
            "body": "Some bug 1",
            "labels": [{"name": "bug"}],
            "closedAt": "2024-01-01T00:00:00Z",
        }
        pr = {
            "number": 100,
            "title": "Fix bug #1",
            "body": "Fixes #1",
            "labels": [],
            "mergeCommit": {"oid": "abc123def456abc123def456abc123def456abc1"},
            "baseRefName": "main",
            "headRefName": "fix/bug",
            "files": [{"path": "src/main.rs"}],
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "closedIssues": [{"number": 1}],
        }
        # A labeled PR with a NEW number (not 100) should be added
        labeled_pr = {
            "number": 200,
            "title": "fix: regression in auth",
            "body": "",
            "labels": [{"name": "bug"}],
            "mergeCommit": {"oid": "def456" * 6 + "de"},
            "additions": 20,
            "deletions": 10,
            "changedFiles": 2,
            "files": [{"path": "src/auth.rs"}],
        }

        runner = CliRunner()
        with (
            patch("bugeval.scrape_github_cmd.fetch_bug_issues", return_value=[issue]),
            patch("bugeval.scrape_github_cmd.fetch_fix_prs", return_value=[pr]),
            patch("bugeval.scrape_github_cmd.fetch_prs_by_label", return_value=[labeled_pr]),
            patch("bugeval.scrape_github_cmd.fetch_pr_diff", return_value=[]),
        ):
            result = runner.invoke(
                scrape_github,
                [
                    "--repo", "foo/bar",
                    "--fetch-by-label",
                    "--min-confidence", "0.0",
                    "--dry-run",
                    "--output-dir", str(tmp_path),
                ],
            )

        assert result.exit_code == 0
        # Both PR#100 and PR#200 should appear
        assert "100" in result.output
        assert "200" in result.output


class TestScrapeBenchmarkCommand:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(scrape_benchmark, ["--help"])
        assert result.exit_code == 0
        assert "--output-dir" in result.output
        assert "--dry-run" in result.output

    def test_dry_run_prints_candidates_no_files(self, tmp_path: Path) -> None:
        import json

        from bugeval.github_scraper import GhError as GhErr

        fork_pr = {
            "number": 42,
            "title": "fix: null deref in auth",
            "body": "Some body",
            "additions": 15,
            "deletions": 5,
            "files": [{"path": "src/auth.py"}],
            "mergeCommit": {"oid": "abc" * 13 + "d"},
            "labels": [{"name": "bug"}],
        }
        runner = CliRunner()
        with patch(
            "bugeval.scrape_github_cmd.run_gh",
            side_effect=[
                json.dumps([fork_pr]),  # fork PR list for first fork
                json.dumps([]),          # title search in original repo
                # remaining forks raise GhError → skipped
                *([GhErr(["gh"], "not found")] * 10),
            ],
        ):
            result = runner.invoke(
                scrape_benchmark,
                ["--dry-run", "--output-dir", str(tmp_path)],
            )

        assert result.exit_code == 0
        # Should not write any files
        assert not list(tmp_path.glob("*.yaml"))
        # Should mention benchmark candidates total
        assert "benchmark candidates" in result.output.lower()

    def test_writes_yaml_file(self, tmp_path: Path) -> None:
        import json

        fork_pr = {
            "number": 5,
            "title": "fix: crash on empty input",
            "body": "",
            "additions": 8,
            "deletions": 2,
            "files": [{"path": "src/lib.rs"}],
            "mergeCommit": {"oid": "aaa" * 13 + "a"},
            "labels": [],
        }
        runner = CliRunner()

        def fake_run_gh(*args: str) -> str:
            # fork PR list
            if "--limit" in args and "12" in args:
                return json.dumps([fork_pr])
            # title search in original repo → no match
            if "--limit" in args and "3" in args:
                return json.dumps([])
            raise Exception("unexpected call")

        with patch("bugeval.scrape_github_cmd.run_gh", side_effect=fake_run_gh):
            result = runner.invoke(
                scrape_benchmark,
                ["--output-dir", str(tmp_path)],
            )

        # On success for at least one fork, a YAML is written
        assert result.exit_code == 0
        yaml_files = list(tmp_path.glob("*.yaml"))
        assert any("greptile-benchmark" in f.name for f in yaml_files)

    def test_gh_error_for_fork_is_skipped(self, tmp_path: Path) -> None:
        """GhError when fetching a fork's PRs should be skipped gracefully."""
        from bugeval.github_scraper import GhError as GhErr

        runner = CliRunner()
        with patch(
            "bugeval.scrape_github_cmd.run_gh",
            side_effect=GhErr(["gh"], "not found"),
        ):
            result = runner.invoke(
                scrape_benchmark,
                ["--dry-run", "--output-dir", str(tmp_path)],
            )

        assert result.exit_code == 0
        assert "0" in result.output  # 0 total candidates


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
