"""Tests for pr_eval_models."""

from pathlib import Path

import pytest
import yaml

from bugeval.pr_eval_models import (
    CaseToolState,
    CaseToolStatus,
    EvalConfig,
    RunState,
    ToolDef,
    ToolType,
    load_eval_config,
)


def test_case_tool_state_defaults() -> None:
    state = CaseToolState(case_id="case-001", tool="coderabbit")
    assert state.status == CaseToolStatus.pending
    assert state.pr_number is None
    assert state.branch_name is None
    assert state.error is None


def test_run_state_get_missing_returns_pending() -> None:
    rs = RunState()
    state = rs.get("case-001", "coderabbit")
    assert state.status == CaseToolStatus.pending
    assert state.case_id == "case-001"
    assert state.tool == "coderabbit"


def test_run_state_set_and_get() -> None:
    rs = RunState()
    state = CaseToolState(case_id="case-001", tool="coderabbit", status=CaseToolStatus.done)
    rs.set(state)
    retrieved = rs.get("case-001", "coderabbit")
    assert retrieved.status == CaseToolStatus.done


def test_run_state_yaml_round_trip(tmp_path: Path) -> None:
    rs = RunState()
    s1 = CaseToolState(
        case_id="case-001",
        tool="coderabbit",
        status=CaseToolStatus.failed,
        pr_number=42,
        branch_name="bugeval/case-001-coderabbit",
        error="something went wrong",
        started_at="2026-03-03T12:00:00+00:00",
        completed_at="2026-03-03T12:05:00+00:00",
    )
    rs.set(s1)

    checkpoint = tmp_path / "checkpoint.yaml"
    rs.save(checkpoint)

    loaded = RunState.load(checkpoint)
    retrieved = loaded.get("case-001", "coderabbit")
    assert retrieved.status == CaseToolStatus.failed
    assert retrieved.pr_number == 42
    assert retrieved.branch_name == "bugeval/case-001-coderabbit"
    assert retrieved.error == "something went wrong"
    assert retrieved.started_at == "2026-03-03T12:00:00+00:00"
    assert retrieved.completed_at == "2026-03-03T12:05:00+00:00"


def test_run_state_load_nonexistent(tmp_path: Path) -> None:
    rs = RunState.load(tmp_path / "nope.yaml")
    assert rs.pairs == {}


def test_tool_def_is_pr_tool() -> None:
    pr_tool = ToolDef(name="coderabbit", type="pr")
    api_tool = ToolDef(name="greptile", type="api")
    cli_tool = ToolDef(name="claude-code-cli", type="cli")
    assert pr_tool.is_pr_tool is True
    assert api_tool.is_pr_tool is False
    assert cli_tool.is_pr_tool is False


def test_tool_def_is_api_tool() -> None:
    pr_tool = ToolDef(name="coderabbit", type="pr")
    api_tool = ToolDef(name="greptile", type="api")
    cli_tool = ToolDef(name="claude-code-cli", type="cli")
    assert pr_tool.is_api_tool is False
    assert api_tool.is_api_tool is True
    assert cli_tool.is_api_tool is False


def test_tool_def_api_fields() -> None:
    tool = ToolDef(
        name="greptile",
        type="api",
        api_endpoint="https://api.greptile.com/v2/review",
        api_key_env="GREPTILE_API_KEY",
    )
    assert tool.api_endpoint == "https://api.greptile.com/v2/review"
    assert tool.api_key_env == "GREPTILE_API_KEY"


def test_tool_def_api_fields_optional() -> None:
    tool = ToolDef(name="greptile", type="api")
    assert tool.api_endpoint is None
    assert tool.api_key_env is None


def test_eval_config_api_tools_filtering() -> None:
    tools = [
        ToolDef(name="coderabbit", type="pr"),
        ToolDef(name="greptile", type="api"),
        ToolDef(name="anthropic-api", type="api"),
    ]
    config = EvalConfig(eval_org="my-org", tools=tools)
    api_tools = config.api_tools
    assert len(api_tools) == 2
    assert {t.name for t in api_tools} == {"greptile", "anthropic-api"}


def test_case_tool_status_new_values() -> None:
    assert CaseToolStatus.submitting == "submitting"
    assert CaseToolStatus.collecting == "collecting"


def test_case_tool_status_yaml_round_trip(tmp_path: Path) -> None:
    rs = RunState()
    state = CaseToolState(case_id="case-001", tool="greptile", status=CaseToolStatus.submitting)
    rs.set(state)
    checkpoint = tmp_path / "checkpoint.yaml"
    rs.save(checkpoint)
    loaded = RunState.load(checkpoint)
    retrieved = loaded.get("case-001", "greptile")
    assert retrieved.status == CaseToolStatus.submitting


def test_load_eval_config_with_api_fields(tmp_path: Path) -> None:
    config_data = {
        "github": {"eval_org": "provable-eval"},
        "tools": [
            {
                "name": "greptile",
                "type": "api",
                "api_endpoint": "https://api.greptile.com/v2/review",
                "api_key_env": "GREPTILE_API_KEY",
                "cooldown_seconds": 30,
            },
        ],
        "repos": {},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config_data))

    config = load_eval_config(config_path)
    assert len(config.api_tools) == 1
    tool = config.api_tools[0]
    assert tool.api_endpoint == "https://api.greptile.com/v2/review"
    assert tool.api_key_env == "GREPTILE_API_KEY"


def test_tool_type_enum_values() -> None:
    assert ToolType.pr == "pr"
    assert ToolType.api == "api"
    assert ToolType.cli == "cli"
    assert ToolType.agent == "agent"


def test_case_tool_status_agent_values() -> None:
    assert CaseToolStatus.cloning == "cloning"
    assert CaseToolStatus.running == "running"


def test_tool_def_is_agent_tool() -> None:
    agent_tool = ToolDef(name="claude-code-cli", type="agent")
    pr_tool = ToolDef(name="coderabbit", type="pr")
    assert agent_tool.is_agent_tool is True
    assert pr_tool.is_agent_tool is False


def test_eval_config_agent_tools_filtering() -> None:
    tools = [
        ToolDef(name="coderabbit", type="pr"),
        ToolDef(name="greptile", type="api"),
        ToolDef(name="claude-code-cli", type="agent"),
        ToolDef(name="anthropic-api", type="agent"),
    ]
    config = EvalConfig(eval_org="my-org", tools=tools)
    agent_tools = config.agent_tools
    assert len(agent_tools) == 2
    assert {t.name for t in agent_tools} == {"claude-code-cli", "anthropic-api"}


def test_tool_def_rejects_invalid_type() -> None:
    with pytest.raises(Exception):
        ToolDef(name="bad", type="unknown")


def test_eval_config_pr_tools_filtering() -> None:
    tools = [
        ToolDef(name="coderabbit", type="pr"),
        ToolDef(name="greptile", type="api"),
        ToolDef(name="bugbot", type="pr"),
    ]
    config = EvalConfig(eval_org="my-org", tools=tools)
    pr_tools = config.pr_tools
    assert len(pr_tools) == 2
    assert {t.name for t in pr_tools} == {"coderabbit", "bugbot"}


def test_load_eval_config(tmp_path: Path) -> None:
    config_data = {
        "github": {"eval_org": "provable-eval"},
        "tools": [
            {
                "name": "coderabbit",
                "type": "pr",
                "github_app": "coderabbit-ai",
                "cooldown_seconds": 30,
            },
            {
                "name": "greptile",
                "type": "api",
                "github_app": "greptile",
                "cooldown_seconds": 30,
            },
        ],
        "repos": {"aleo-lang": "provable-org/aleo-lang"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config_data))

    config = load_eval_config(config_path)
    assert config.eval_org == "provable-eval"
    assert len(config.tools) == 2
    assert config.repos == {"aleo-lang": "provable-org/aleo-lang"}
    assert len(config.pr_tools) == 1
    assert config.pr_tools[0].name == "coderabbit"
