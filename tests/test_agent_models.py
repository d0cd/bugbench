"""Tests for agent_models."""

from bugeval.agent_models import AgentResult


def test_agent_result_defaults() -> None:
    result = AgentResult()
    assert result.findings == []
    assert result.conversation == []
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.token_count == 0
    assert result.cost_usd == 0.0
    assert result.wall_time_seconds == 0.0
    assert result.turns == 0
    assert result.model == ""
    assert result.context_level == ""
    assert result.error is None


def test_agent_result_full_population() -> None:
    result = AgentResult(
        findings=[{"file": "src/main.rs", "line": 42, "summary": "off-by-one"}],
        conversation=[{"role": "user", "content": "review this"}],
        stdout="Found 1 issue",
        stderr="",
        token_count=1500,
        cost_usd=0.05,
        wall_time_seconds=12.3,
        turns=3,
        model="claude-sonnet-4-6",
        context_level="diff-only",
        error=None,
    )
    assert result.findings[0]["file"] == "src/main.rs"
    assert result.token_count == 1500
    assert result.model == "claude-sonnet-4-6"


def test_agent_result_model_dump_round_trip() -> None:
    result = AgentResult(
        findings=[{"file": "a.rs", "line": 1, "summary": "bug"}],
        token_count=100,
        error="something failed",
    )
    data = result.model_dump(mode="json")
    restored = AgentResult(**data)
    assert restored.findings == result.findings
    assert restored.token_count == 100
    assert restored.error == "something failed"
