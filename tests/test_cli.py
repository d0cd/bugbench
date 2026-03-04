"""Smoke tests for CLI entry point."""

from click.testing import CliRunner

from bugeval.cli import cli


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "bugeval" in result.output


def test_scrape_github_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["scrape-github", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output


def test_validate_cases_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["validate-cases", "--help"])
    assert result.exit_code == 0
    assert "--repo-dir" in result.output


def test_extract_patch_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["extract-patch", "--help"])
    assert result.exit_code == 0
    assert "--case" in result.output


def test_manage_forks_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["manage-forks", "--help"])
    assert result.exit_code == 0
    assert "--action" in result.output
    assert "--dry-run" in result.output


def test_run_pr_eval_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["run-pr-eval", "--help"])
    assert result.exit_code == 0
    assert "--cases-dir" in result.output
    assert "--dry-run" in result.output


def test_run_api_eval_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["run-api-eval", "--help"])
    assert result.exit_code == 0
    assert "--context-level" in result.output
    assert "--dry-run" in result.output


def test_run_agent_eval_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["run-agent-eval", "--help"])
    assert result.exit_code == 0
    assert "--context-level" in result.output
    assert "--max-turns" in result.output
    assert "--dry-run" in result.output
