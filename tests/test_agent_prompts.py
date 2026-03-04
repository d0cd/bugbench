"""Tests for agent_prompts."""

from pathlib import Path

from bugeval.agent_prompts import _DEFAULT_SYSTEM_PROMPT, build_user_prompt, load_agent_prompt
from bugeval.models import Category, Difficulty, PRSize, Severity, TestCase


def _make_case() -> TestCase:
    return TestCase(
        id="aleo-lang-001",
        repo="provable-org/aleo-lang",
        base_commit="abc123",
        head_commit="def456",
        fix_commit="def456",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.small,
        description="Off-by-one in loop bound",
        expected_findings=[],
    )


def test_load_agent_prompt_from_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "agent_prompt.md"
    prompt_file.write_text("Custom system prompt")
    result = load_agent_prompt(prompt_file)
    assert result == "Custom system prompt"


def test_load_agent_prompt_falls_back_to_default(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.md"
    result = load_agent_prompt(missing)
    assert result == _DEFAULT_SYSTEM_PROMPT


def test_build_user_prompt_diff_only() -> None:
    case = _make_case()
    prompt = build_user_prompt(case, "- old\n+ new\n", "diff-only")
    assert "- old" in prompt
    assert "+ new" in prompt
    assert "repository" not in prompt.lower()
    assert "domain" not in prompt.lower()


def test_build_user_prompt_diff_plus_repo() -> None:
    case = _make_case()
    prompt = build_user_prompt(case, "patch content", "diff+repo")
    assert "patch content" in prompt
    assert "working directory" in prompt.lower()
    assert "Domain Context" not in prompt


def test_build_user_prompt_diff_plus_repo_plus_domain() -> None:
    case = _make_case()
    prompt = build_user_prompt(case, "patch content", "diff+repo+domain")
    assert "patch content" in prompt
    assert "working directory" in prompt.lower()
    assert "Domain Context" in prompt
    assert "logic" in prompt
    assert "high" in prompt
    assert "rust" in prompt
    assert "Off-by-one" in prompt
