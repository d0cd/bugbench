"""Tests for manage_forks CLI."""

from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from bugeval.manage_forks import fork_name, manage_forks


def _make_config(
    tmp_path: Path, eval_org: str = "provable-eval", repos: dict | None = None
) -> Path:
    """Write a minimal config.yaml to tmp_path."""
    if repos is None:
        repos = {"aleo-lang": "provable-org/aleo-lang"}
    data = {
        "github": {"eval_org": eval_org},
        "tools": [
            {
                "name": "coderabbit",
                "type": "pr",
                "github_app": "coderabbit-ai",
                "cooldown_seconds": 30,
            },
            {"name": "greptile", "type": "api", "github_app": "greptile", "cooldown_seconds": 30},
        ],
        "repos": repos,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(data))
    return config_path


def test_fork_name_format() -> None:
    assert fork_name("provable-org/aleo-lang", "coderabbit") == "aleo-lang-coderabbit"
    assert fork_name("org/snarkvm", "bugbot") == "snarkvm-bugbot"


def test_manage_forks_help() -> None:
    runner = CliRunner()
    result = runner.invoke(manage_forks, ["--help"])
    assert result.exit_code == 0
    assert "--action" in result.output
    assert "--tool" in result.output
    assert "--dry-run" in result.output


def test_create_dry_run(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        manage_forks, ["--config", str(config_path), "--action", "create", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "[dry-run]" in result.output
    assert "aleo-lang-coderabbit" in result.output


def test_cleanup_dry_run(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        manage_forks, ["--config", str(config_path), "--action", "cleanup", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "[dry-run]" in result.output
    assert "delete" in result.output


def test_create_calls_gh(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch("bugeval.manage_forks.run_gh") as mock_gh:
        result = runner.invoke(manage_forks, ["--config", str(config_path), "--action", "create"])
    assert result.exit_code == 0
    mock_gh.assert_called_once_with(
        "repo",
        "fork",
        "provable-org/aleo-lang",
        "--org",
        "provable-eval",
        "--fork-name",
        "aleo-lang-coderabbit",
        "--clone=false",
    )


def test_skips_api_tools(tmp_path: Path) -> None:
    """manage-forks should only operate on PR tools, not api/cli tools."""
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch("bugeval.manage_forks.run_gh") as mock_gh:
        result = runner.invoke(manage_forks, ["--config", str(config_path), "--action", "create"])
    assert result.exit_code == 0
    # greptile is type=api, should not appear in calls
    for call_args in mock_gh.call_args_list:
        args = call_args[0]
        assert "greptile" not in " ".join(str(a) for a in args)


def test_tool_filter(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        manage_forks,
        ["--config", str(config_path), "--action", "create", "--tool", "nonexistent", "--dry-run"],
    )
    assert result.exit_code != 0


def test_missing_eval_org_exits(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path, eval_org="")
    runner = CliRunner()
    result = runner.invoke(manage_forks, ["--config", str(config_path), "--action", "create"])
    assert result.exit_code != 0
    assert "eval_org" in result.output


def test_no_repos_configured_exits(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path, repos={})
    runner = CliRunner()
    result = runner.invoke(manage_forks, ["--config", str(config_path), "--action", "create"])
    assert result.exit_code != 0
    assert "repos" in result.output
