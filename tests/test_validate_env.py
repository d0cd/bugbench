"""Tests for validate-env command."""

from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from bugeval.cli import cli
from bugeval.pr_eval_models import load_eval_config
from bugeval.validate_env import check_env


def _make_config(tmp_path: Path, tools=None, eval_org="", repos=None) -> Path:
    config_data = {
        "github": {"eval_org": eval_org},
        "tools": tools or [],
        "repos": repos or {},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(config_data))
    return p


def test_check_env_missing_api_key(tmp_path):
    config = load_eval_config(_make_config(tmp_path))
    with patch.dict("os.environ", {}, clear=True):
        result = check_env(config)
    assert not result.passed
    assert any("ANTHROPIC_API_KEY" in e for e in result.errors)
    assert any("GITHUB_TOKEN" in e for e in result.errors)


def test_check_env_all_present(tmp_path):
    config = load_eval_config(_make_config(tmp_path, repos={"repo": "org/repo"}))
    env = {"ANTHROPIC_API_KEY": "sk-ant-x", "GITHUB_TOKEN": "ghp_x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config)
    assert result.passed
    assert any("ANTHROPIC_API_KEY" in m for m in result.ok)


def test_check_env_pr_tool_missing_org(tmp_path):
    tools = [{"name": "coderabbit", "type": "pr", "cooldown_seconds": 0}]
    config = load_eval_config(_make_config(tmp_path, tools=tools, eval_org=""))
    env = {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config)
    assert not result.passed
    assert any("eval_org" in e for e in result.errors)


def test_check_env_tool_api_key_missing(tmp_path):
    tools = [
        {
            "name": "greptile",
            "type": "api",
            "api_endpoint": "https://api.greptile.com",
            "api_key_env": "GREPTILE_API_KEY",
            "cooldown_seconds": 0,
        }
    ]
    config = load_eval_config(_make_config(tmp_path, tools=tools))
    env = {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config)
    assert not result.passed
    assert any("GREPTILE_API_KEY" in e for e in result.errors)


def test_check_env_warns_empty_repos(tmp_path):
    config = load_eval_config(_make_config(tmp_path, repos={}))
    env = {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config)
    assert result.passed  # warnings don't fail
    assert any("repos" in w for w in result.warnings)


def test_check_env_cases_dir_empty(tmp_path):
    config = load_eval_config(_make_config(tmp_path))
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    env = {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config, cases_dir=cases_dir)
    assert any("No case" in w for w in result.warnings)


def test_validate_env_cli_exits_nonzero_on_failure(tmp_path):
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch.dict("os.environ", {}, clear=True):
        result = runner.invoke(cli, ["validate-env", "--config", str(config_path)])
    assert result.exit_code != 0
