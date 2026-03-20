# tests/test_judge.py
"""Tests for the judge module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic
import pytest
import yaml

from bugeval.judge import (
    _build_judge_prompt,
    _extract_judge_json,
    judge_case,
    judge_normalized_results,
    load_judge_prompt,
)
from bugeval.result_models import Comment, NormalizedResult


def _make_result(case_id: str = "case-001", tool: str = "greptile") -> NormalizedResult:
    return NormalizedResult(
        test_case_id=case_id,
        tool=tool,
        comments=[Comment(body="off by one", file="a.rs", line=10)],
    )


# --- load_judge_prompt ---


def test_load_judge_prompt_from_file(tmp_path: Path) -> None:
    p = tmp_path / "judge_prompt.md"
    p.write_text("Custom judge prompt")
    assert load_judge_prompt(p) == "Custom judge prompt"


def test_load_judge_prompt_falls_back_to_default(tmp_path: Path) -> None:
    prompt = load_judge_prompt(tmp_path / "nonexistent.md")
    assert "rubric" in prompt.lower() or "score" in prompt.lower()


# --- _extract_judge_json ---


def test_extract_judge_json_valid() -> None:
    text = '```json\n{"score": 2, "reasoning": "ok", "comment_judgments": []}\n```'
    data = _extract_judge_json(text)
    assert data is not None
    assert data["score"] == 2


def test_extract_judge_json_bare_object() -> None:
    text = '{"score": 3, "reasoning": "great", "comment_judgments": []}'
    data = _extract_judge_json(text)
    assert data is not None
    assert data["score"] == 3


def test_extract_judge_json_invalid_returns_none() -> None:
    assert _extract_judge_json("not json at all") is None


def test_extract_judge_json_multiline_fence() -> None:
    text = '```json\n{"score": 2,\n "reasoning": "ok",\n "comment_judgments": []}\n```'
    data = _extract_judge_json(text)
    assert data is not None
    assert data["score"] == 2


# --- judge_case ---


def _make_mock_response(score: int) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock()]
    response.content[0].type = "text"
    response.content[0].text = json.dumps(
        {
            "score": score,
            "reasoning": f"Score is {score}",
            "comment_judgments": [
                {
                    "id": 0,
                    "classification": "TP-expected",
                    "severity": "high",
                    "actionability": "actionable",
                    "relevance": "direct",
                }
            ],
        }
    )
    return response


def test_judge_case_majority_vote(tmp_path: Path) -> None:
    """3 votes [2, 2, 3] → score 2."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _make_mock_response(2),
        _make_mock_response(2),
        _make_mock_response(3),
    ]

    with patch("bugeval.judge.Anthropic", return_value=mock_client):
        score = judge_case(case, result, system_prompt="judge this")

    assert score.score == 2
    assert score.votes == [2, 2, 3]
    assert len(score.comment_judgments) > 0
    # New assertions:
    assert score.noise.total_comments == 1  # _make_result() has 1 comment
    assert score.noise.snr == pytest.approx(1.0)  # all judgments are TP


def test_judge_case_reasoning_captured(tmp_path: Path) -> None:
    """judge_case reasoning comes from the LLM response, not just vote counts."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _make_mock_response(2),
        _make_mock_response(2),
        _make_mock_response(2),
    ]

    with patch("bugeval.judge.Anthropic", return_value=mock_client):
        score = judge_case(case, result, system_prompt="judge this")

    assert score.score == 2
    # Reasoning should contain the LLM's text, not just "Votes: ..."
    assert "Score is 2" in score.reasoning


def test_judge_case_dry_run(tmp_path: Path) -> None:
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    with patch("bugeval.judge.Anthropic") as mock_anthropic_cls:
        score = judge_case(case, result, system_prompt="p", dry_run=True)

    mock_anthropic_cls.assert_not_called()
    assert score.score == 0
    assert score.reasoning == "dry-run"


def test_judge_case_all_parse_failures() -> None:
    """When all votes fail to parse, score=0 with failure count in reasoning."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    bad_response = MagicMock()
    bad_response.content = [MagicMock()]
    bad_response.content[0].type = "text"
    bad_response.content[0].text = "I cannot score this."

    mock_client = MagicMock()
    mock_client.messages.create.return_value = bad_response

    with patch("bugeval.judge.Anthropic", return_value=mock_client):
        score = judge_case(case, result, system_prompt="judge this")

    assert score.votes == [0, 0, 0]
    assert score.score == 0
    assert "failed to parse" in score.reasoning


# --- CLI smoke test ---


def test_judge_help() -> None:
    from click.testing import CliRunner

    from bugeval.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["judge", "--help"])
    assert result.exit_code == 0
    assert "--run-dir" in result.output
    assert "--dry-run" in result.output


def test_judge_no_results(tmp_path: Path) -> None:
    """Exits cleanly when no normalized results found."""
    from click.testing import CliRunner

    from bugeval.cli import cli

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "judge",
            "--run-dir",
            str(tmp_path),
            "--cases-dir",
            str(cases_dir),
        ],
    )
    assert result.exit_code == 0
    assert "No normalized results" in result.output


# --- judge_normalized_results ---


def _write_case_yaml(cases_dir: Path, case_id: str = "case-001") -> None:
    """Write a minimal TestCase YAML into cases_dir."""
    from bugeval.io import save_case
    from tests.conftest import make_case

    case = make_case(id=case_id)
    save_case(case, cases_dir / f"{case_id}.yaml")


def _write_normalized_yaml(
    run_dir: Path, case_id: str = "case-001", tool: str = "greptile"
) -> Path:
    """Write a minimal NormalizedResult YAML into run_dir."""
    result = NormalizedResult(
        test_case_id=case_id,
        tool=tool,
        comments=[Comment(body="suspicious line", file="src/main.rs", line=42)],
    )
    out = run_dir / f"{case_id}-{tool}.yaml"
    out.write_text(yaml.safe_dump(result.model_dump(mode="json"), sort_keys=False))
    return out


def test_judge_normalized_results_dry_run(tmp_path: Path) -> None:
    """dry_run=True: scores all results without making LLM calls; score YAMLs are still written."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()

    _write_case_yaml(cases_dir)
    _write_normalized_yaml(tmp_path)

    with patch("bugeval.judge.Anthropic") as mock_anthropic_cls:
        count = judge_normalized_results(tmp_path, cases_dir, dry_run=True)

    mock_anthropic_cls.assert_not_called()
    assert count == 1
    scores_dir = tmp_path / "scores"
    # scores/ dir is created and score file is written; dry_run only skips the LLM API call
    assert scores_dir.exists()
    assert len(list(scores_dir.glob("*.yaml"))) == 1


# --- _build_judge_prompt ---


def test_build_judge_prompt_includes_suggested_fix() -> None:
    """suggested_fix on enriched Comment should appear in the judge prompt."""
    from tests.conftest import make_case

    case = make_case()
    result = NormalizedResult(
        test_case_id="case-001",
        tool="anthropic-api",
        comments=[
            Comment(body="off by one", file="a.rs", line=10, suggested_fix="Add bounds check")
        ],
    )
    prompt = _build_judge_prompt(case, result)
    assert "Add bounds check" in prompt
    assert "Fix:" in prompt


def test_build_judge_prompt_no_fix_no_section() -> None:
    """When no suggested_fix, the Fix: line should not appear."""
    from tests.conftest import make_case

    case = make_case()
    result = NormalizedResult(
        test_case_id="case-001",
        tool="greptile",
        comments=[Comment(body="off by one", file="a.rs", line=10)],
    )
    prompt = _build_judge_prompt(case, result)
    assert "Fix:" not in prompt


def test_judge_case_uses_temperature_zero() -> None:
    """judge_case must pass temperature=0 to the LLM for reproducibility."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(2)

    with patch("bugeval.judge.Anthropic", return_value=mock_client):
        judge_case(case, result, system_prompt="judge this")

    call_kwargs = mock_client.messages.create.call_args_list[0][1]
    assert call_kwargs.get("temperature") == 0


def test_judge_normalized_results_returns_count(tmp_path: Path) -> None:
    """Returns the count of results that were scored."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()

    _write_case_yaml(cases_dir)
    _write_normalized_yaml(tmp_path)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(2)

    with patch("bugeval.judge.Anthropic", return_value=mock_client):
        count = judge_normalized_results(tmp_path, cases_dir, dry_run=False)

    assert count == 1


class TestJudgeViaCli:
    def test_cli_judge_calls_subprocess(self, tmp_path: Path) -> None:
        """judges=['claude-cli-opus'] calls run_claude_cli with max_turns=1."""
        from unittest.mock import patch

        from bugeval.agent_models import AgentResult
        from tests.conftest import make_case

        case = make_case()
        result = _make_result()

        valid_stdout = '{"score": 2, "reasoning": "found it", "comment_judgments": []}'
        mock_agent_result = AgentResult(stdout=valid_stdout, model="claude-opus-4-6")

        with patch("bugeval.judge.run_claude_cli", return_value=mock_agent_result) as mock_cli:
            score = judge_case(case, result, system_prompt="judge this", judges=["claude-cli-opus"])

        mock_cli.assert_called_once()
        call_kwargs = mock_cli.call_args
        assert call_kwargs.kwargs.get("max_turns") == 1 or call_kwargs.args[2] == 1
        assert score.score == 2

    def test_cli_judge_dry_run_skips_subprocess(self, tmp_path: Path) -> None:
        """dry_run=True + judges=['claude-cli-opus'] → no subprocess call, returns score 0."""
        from unittest.mock import patch

        from tests.conftest import make_case

        case = make_case()
        result = _make_result()

        with patch("bugeval.judge.run_claude_cli") as mock_cli:
            score = judge_case(
                case, result, system_prompt="p", dry_run=True, judges=["claude-cli-opus"]
            )

        mock_cli.assert_not_called()
        assert score.score == 0

    def test_cli_judge_parse_failure_returns_zero(self, tmp_path: Path) -> None:
        """run_claude_cli returns non-JSON stdout → score defaults to 0."""
        from unittest.mock import patch

        from bugeval.agent_models import AgentResult
        from tests.conftest import make_case

        case = make_case()
        result = _make_result()

        bad_result = AgentResult(stdout="I cannot determine the score.", model="claude-opus-4-6")

        with patch("bugeval.judge.run_claude_cli", return_value=bad_result):
            score = judge_case(case, result, system_prompt="judge this", judges=["claude-cli-opus"])

        assert score.score == 0
        assert "failed to parse" in score.reasoning


def test_judge_normalized_results_parallel(tmp_path: Path) -> None:
    """max_concurrent > 1 scores all results and returns the correct count."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()

    _write_case_yaml(cases_dir, "case-001")
    _write_case_yaml(cases_dir, "case-002")
    _write_normalized_yaml(tmp_path, "case-001", "greptile")
    _write_normalized_yaml(tmp_path, "case-002", "greptile")

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(2)

    with patch("bugeval.judge.Anthropic", return_value=mock_client):
        count = judge_normalized_results(tmp_path, cases_dir, dry_run=False, max_concurrent=2)

    assert count == 2
    assert len(list((tmp_path / "scores").glob("*.yaml"))) == 2


def test_judge_help_shows_max_concurrent(tmp_path: Path) -> None:
    """judge --help shows --max-concurrent option."""
    from click.testing import CliRunner

    from bugeval.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["judge", "--help"])
    assert result.exit_code == 0
    assert "--max-concurrent" in result.output


def test_judge_case_retries_on_rate_limit() -> None:
    """judge_case retries when the API raises RateLimitError, then succeeds."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    mock_client = MagicMock()
    rate_limit_error = anthropic.RateLimitError(
        message="rate limited",
        response=MagicMock(status_code=429, headers={}),
        body={},
    )
    mock_client.messages.create.side_effect = [
        rate_limit_error,
        _make_mock_response(2),
        _make_mock_response(2),
        _make_mock_response(2),
    ]

    with patch("bugeval.judge.Anthropic", return_value=mock_client):
        with patch("bugeval.agent_api_runner.time.sleep"):
            score = judge_case(case, result, system_prompt="judge this")

    assert score.score == 2
    assert mock_client.messages.create.call_count == 4  # 1 failure + 3 successes


# ---------------------------------------------------------------------------
# Cross-provider judging (Gemini / OpenAI)
# ---------------------------------------------------------------------------


def test_judge_case_gemini_model_uses_google_sdk() -> None:
    """A gemini-* model in the vote list should call the Google SDK, not Anthropic."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    judge_json = json.dumps(
        {
            "score": 2,
            "reasoning": "found it",
            "comment_judgments": [],
        }
    )

    with (
        patch("bugeval.judge.default_judging") as mock_judging,
        patch("bugeval.judge._call_google_judge", return_value=judge_json) as mock_g,
        patch("bugeval.judge.Anthropic") as mock_anthropic,
    ):
        mock_judging.return_value = MagicMock(models=["gemini-2.5-flash"], model="")
        score = judge_case(case, result, system_prompt="judge this")

    mock_g.assert_called_once()
    mock_anthropic.assert_not_called()
    assert score.score == 2


def test_judge_case_openai_model_uses_openai_sdk() -> None:
    """A gpt-*/o4-* model in the vote list should call the OpenAI SDK."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    judge_json = json.dumps(
        {
            "score": 3,
            "reasoning": "good fix",
            "comment_judgments": [],
        }
    )

    with (
        patch("bugeval.judge.default_judging") as mock_judging,
        patch("bugeval.judge._call_openai_judge", return_value=judge_json) as mock_o,
        patch("bugeval.judge.Anthropic") as mock_anthropic,
    ):
        mock_judging.return_value = MagicMock(models=["gpt-5.4-mini"], model="")
        score = judge_case(case, result, system_prompt="judge this")

    mock_o.assert_called_once()
    mock_anthropic.assert_not_called()
    assert score.score == 3


def test_build_judge_prompt_includes_diff() -> None:
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()
    prompt = _build_judge_prompt(case, result, diff_content="--- a/f.rs\n+++ b/f.rs\n")
    assert "--- a/f.rs" in prompt
    assert "### Diff" in prompt


def test_build_judge_prompt_truncates_long_diff() -> None:
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()
    long_diff = "x" * 6000
    prompt = _build_judge_prompt(case, result, diff_content=long_diff)
    assert "(truncated)" in prompt
    assert len(prompt) < len(long_diff) + 2000  # prompt overhead


def test_build_judge_prompt_no_diff_section_when_empty() -> None:
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()
    prompt = _build_judge_prompt(case, result, diff_content="")
    assert "### Diff" not in prompt


def test_build_judge_prompt_omits_case_id() -> None:
    """case.id reveals the repo name — judge prompt must not include it."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()
    prompt = _build_judge_prompt(case, result)
    # The prompt should not contain the case ID value
    assert case.id not in prompt
    # Guard against any case-id-like patterns leaking
    assert "case-001" not in prompt


def test_extract_judge_json_with_multiple_json_objects() -> None:
    """Greedy regex must not merge two separate JSON objects."""
    text = (
        'The tool found {"file": "foo.rs"} in the code.\n'
        '{"score": 2, "reasoning": "Found it", "comment_judgments": []}'
    )
    result = _extract_judge_json(text)
    assert result is not None
    assert result["score"] == 2


def test_judge_case_mixed_ensemble() -> None:
    """An ensemble with mixed providers dispatches each vote to the right SDK."""
    from tests.conftest import make_case

    case = make_case()
    result = _make_result()

    judge_json = json.dumps(
        {
            "score": 2,
            "reasoning": "ok",
            "comment_judgments": [],
        }
    )

    with (
        patch("bugeval.judge.default_judging") as mock_judging,
        patch("bugeval.judge._call_google_judge", return_value=judge_json) as mock_g,
        patch("bugeval.judge._call_openai_judge", return_value=judge_json) as mock_o,
        patch("bugeval.judge._call_anthropic_judge", return_value=judge_json) as mock_a,
    ):
        mock_judging.return_value = MagicMock(
            models=["claude-sonnet-4-6", "gemini-2.5-flash", "gpt-5.4-mini"],
            model="",
        )
        score = judge_case(case, result, system_prompt="judge this")

    mock_a.assert_called_once()
    mock_g.assert_called_once()
    mock_o.assert_called_once()
    assert score.score == 2


# ---------------------------------------------------------------------------
# resolve_judge_runner
# ---------------------------------------------------------------------------


class TestResolveJudgeRunner:
    def test_claude_cli_runner(self) -> None:
        from bugeval.judge import resolve_judge_runner

        kind, model = resolve_judge_runner("claude-cli-sonnet")
        assert kind == "claude-cli"
        assert model == "claude-sonnet-4-6"

    def test_gemini_cli_runner(self) -> None:
        from bugeval.judge import resolve_judge_runner

        kind, model = resolve_judge_runner("gemini-cli-flash")
        assert kind == "gemini-cli"
        assert model == "gemini-2.5-flash"

    def test_codex_cli_runner(self) -> None:
        from bugeval.judge import resolve_judge_runner

        kind, model = resolve_judge_runner("codex-cli-mini")
        assert kind == "codex-cli"
        assert model == "gpt-5.4-mini"

    def test_bare_anthropic_model(self) -> None:
        from bugeval.judge import resolve_judge_runner

        kind, model = resolve_judge_runner("claude-sonnet-4-6")
        assert kind == "api"
        assert model == "claude-sonnet-4-6"

    def test_bare_gemini_model(self) -> None:
        from bugeval.judge import resolve_judge_runner

        kind, model = resolve_judge_runner("gemini-2.5-flash")
        assert kind == "api"
        assert model == "gemini-2.5-flash"

    def test_bare_openai_model(self) -> None:
        from bugeval.judge import resolve_judge_runner

        kind, model = resolve_judge_runner("o4-mini")
        assert kind == "api"
        assert model == "o4-mini"


# ---------------------------------------------------------------------------
# --judges flag on judge_case
# ---------------------------------------------------------------------------


class TestJudgesParam:
    def test_judges_overrides_config_models(self) -> None:
        """When judges= is passed, it overrides config judging.models."""
        from tests.conftest import make_case

        case = make_case()
        result = _make_result()

        judge_json = json.dumps({"score": 2, "reasoning": "ok", "comment_judgments": []})

        with patch("bugeval.judge._call_anthropic_judge", return_value=judge_json) as mock_a:
            score = judge_case(
                case,
                result,
                system_prompt="judge",
                judges=["claude-sonnet-4-6"],
            )

        assert mock_a.call_count == 1
        assert score.score == 2

    def test_judges_cli_runner_dispatches_to_run_claude_cli(self) -> None:
        """judges=['claude-cli-sonnet'] should call run_claude_cli."""
        from bugeval.agent_models import AgentResult
        from tests.conftest import make_case

        case = make_case()
        result = _make_result()

        valid_stdout = json.dumps({"score": 2, "reasoning": "found it", "comment_judgments": []})
        mock_agent_result = AgentResult(stdout=valid_stdout, model="claude-sonnet-4-6")

        with patch("bugeval.judge.run_claude_cli", return_value=mock_agent_result) as mock_cli:
            score = judge_case(
                case,
                result,
                system_prompt="judge",
                judges=["claude-cli-sonnet"],
            )

        mock_cli.assert_called_once()
        assert score.score == 2

    def test_judges_gemini_cli_dispatches_to_run_gemini_cli(self) -> None:
        """judges=['gemini-cli-flash'] should call run_gemini_cli."""
        from bugeval.agent_models import AgentResult
        from tests.conftest import make_case

        case = make_case()
        result = _make_result()

        valid_stdout = json.dumps({"score": 3, "reasoning": "great", "comment_judgments": []})
        mock_agent_result = AgentResult(response_text=valid_stdout, model="gemini-2.5-flash")

        with patch("bugeval.judge.run_gemini_cli", return_value=mock_agent_result) as mock_cli:
            score = judge_case(
                case,
                result,
                system_prompt="judge",
                judges=["gemini-cli-flash"],
            )

        mock_cli.assert_called_once()
        assert score.score == 3

    def test_judges_codex_cli_dispatches_to_run_codex_cli(self) -> None:
        """judges=['codex-cli-mini'] should call run_codex_cli."""
        from bugeval.agent_models import AgentResult
        from tests.conftest import make_case

        case = make_case()
        result = _make_result()

        valid_stdout = json.dumps({"score": 2, "reasoning": "ok", "comment_judgments": []})
        mock_agent_result = AgentResult(response_text=valid_stdout, model="gpt-5.4-mini")

        with patch("bugeval.judge.run_codex_cli", return_value=mock_agent_result) as mock_cli:
            score = judge_case(
                case,
                result,
                system_prompt="judge",
                judges=["codex-cli-mini"],
            )

        mock_cli.assert_called_once()
        assert score.score == 2

    def test_judges_mixed_cli_and_api(self) -> None:
        """judges can mix CLI and API runners."""
        from bugeval.agent_models import AgentResult
        from tests.conftest import make_case

        case = make_case()
        result = _make_result()

        judge_json = json.dumps({"score": 2, "reasoning": "ok", "comment_judgments": []})
        mock_agent_result = AgentResult(response_text=judge_json, model="claude-sonnet-4-6")

        with (
            patch("bugeval.judge.run_claude_cli", return_value=mock_agent_result) as mock_cli,
            patch("bugeval.judge._call_google_judge", return_value=judge_json) as mock_g,
        ):
            score = judge_case(
                case,
                result,
                system_prompt="judge",
                judges=["claude-cli-sonnet", "gemini-2.5-flash"],
            )

        mock_cli.assert_called_once()
        mock_g.assert_called_once()
        assert score.score == 2


# ---------------------------------------------------------------------------
# --judges CLI flag
# ---------------------------------------------------------------------------


class TestJudgesCLIFlag:
    def test_help_shows_judges_flag(self) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["judge", "--help"])
        assert result.exit_code == 0
        assert "--judges" in result.output

    def test_help_does_not_show_via_cli(self) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["judge", "--help"])
        assert result.exit_code == 0
        assert "--via-cli" not in result.output


# ---------------------------------------------------------------------------
# review_quality & noise stats parsing
# ---------------------------------------------------------------------------


def _make_case():  # type: ignore[no-untyped-def]
    from tests.conftest import make_case

    return make_case()


def test_default_prompt_has_severity_actionability() -> None:
    """Default prompt should include severity and actionability sections."""
    from bugeval.judge import _DEFAULT_JUDGE_PROMPT

    assert "severity" in _DEFAULT_JUDGE_PROMPT.lower()
    assert "actionability" in _DEFAULT_JUDGE_PROMPT.lower()
    assert "review_quality" not in _DEFAULT_JUDGE_PROMPT.lower()
    # Should not contain old review quality section
    assert "Review quality score" not in _DEFAULT_JUDGE_PROMPT


def test_build_judge_prompt_clean_case_no_expected_findings() -> None:
    """For clean cases (no expected findings), judge prompt should say no known bugs."""
    from tests.conftest import make_case

    case = make_case(expected_findings=[], case_type="clean")
    result = _make_result()
    prompt = _build_judge_prompt(case, result)
    assert "no known bugs" in prompt.lower()
    assert "Expected Bug" not in prompt


def test_judge_case_clean_case_score_zero() -> None:
    """For clean cases, bug detection score is always 0 regardless of judge output."""
    from tests.conftest import make_case

    case = make_case(expected_findings=[], case_type="clean")
    result = _make_result()

    # Judge might return score=2 but clean cases should force score=0
    response = json.dumps(
        {
            "score": 2,
            "reasoning": "Found something",
            "comment_judgments": [
                {
                    "id": 0,
                    "classification": "TP-novel",
                    "severity": "medium",
                    "actionability": "directional",
                    "relevance": "direct",
                },
            ],
        }
    )
    with patch("bugeval.judge._call_anthropic_judge", return_value=response):
        score = judge_case(
            case,
            result,
            system_prompt="test",
            model="claude-sonnet-4-6",
            n_votes=1,
        )
    assert score.score == 0


def test_judge_case_parses_severity_actionability() -> None:
    """judge_case should parse severity and actionability from judge response."""
    case = _make_case()
    result = _make_result()
    response = json.dumps(
        {
            "score": 2,
            "reasoning": "Good review",
            "comment_judgments": [
                {
                    "id": 0,
                    "classification": "TP-expected",
                    "severity": "high",
                    "actionability": "actionable",
                    "relevance": "direct",
                },
            ],
        }
    )
    with patch("bugeval.judge._call_anthropic_judge", return_value=response):
        score = judge_case(
            case,
            result,
            system_prompt="test",
            model="claude-sonnet-4-6",
            n_votes=1,
        )
    assert score.noise.true_positives == 1
    assert score.noise.novel_findings == 0
    assert score.noise.weighted_signal == pytest.approx(3.0)  # high(3) * actionable(1.0)
    assert score.noise.actionability_rate == pytest.approx(1.0)
    assert score.comment_judgments[0].severity == "high"
    assert score.comment_judgments[0].actionability == "actionable"


def test_judge_case_parses_tp_novel() -> None:
    """judge_case should parse TP-novel classification correctly."""
    case = _make_case()
    result = _make_result()
    response = json.dumps(
        {
            "score": 0,
            "reasoning": "Missed main bug but found real secondary issue",
            "comment_judgments": [
                {
                    "id": 0,
                    "classification": "TP-novel",
                    "severity": "medium",
                    "actionability": "directional",
                    "relevance": "direct",
                },
            ],
        }
    )
    with patch("bugeval.judge._call_anthropic_judge", return_value=response):
        score = judge_case(
            case,
            result,
            system_prompt="test",
            model="claude-sonnet-4-6",
            n_votes=1,
        )
    assert score.score == 0
    assert score.noise.novel_findings == 1
    assert score.noise.true_positives == 0
    assert score.noise.false_positives == 0
    assert score.noise.weighted_signal == pytest.approx(1.2)  # medium(2) * directional(0.6)


def test_judge_case_noise_stats_all_classifications() -> None:
    """All four classification types should be counted correctly."""
    case = _make_case()
    # Need 4 comments for 4 classifications
    result = NormalizedResult(
        test_case_id="case-001",
        tool="greptile",
        comments=[
            Comment(body="a", file="a.rs", line=1),
            Comment(body="b", file="b.rs", line=2),
            Comment(body="c", file="c.rs", line=3),
            Comment(body="d", file="d.rs", line=4),
        ],
    )
    response = json.dumps(
        {
            "score": 2,
            "reasoning": "ok",
            "comment_judgments": [
                {
                    "id": 0,
                    "classification": "TP-expected",
                    "severity": "high",
                    "actionability": "actionable",
                    "relevance": "direct",
                },
                {
                    "id": 1,
                    "classification": "TP-novel",
                    "severity": "medium",
                    "actionability": "directional",
                    "relevance": "direct",
                },
                {"id": 2, "classification": "FP", "relevance": "unrelated"},
                {"id": 3, "classification": "low-value", "relevance": "unrelated"},
            ],
        }
    )
    with patch("bugeval.judge._call_anthropic_judge", return_value=response):
        score = judge_case(
            case,
            result,
            system_prompt="test",
            model="claude-sonnet-4-6",
            n_votes=1,
        )
    assert score.noise.total_comments == 4
    assert score.noise.true_positives == 1
    assert score.noise.novel_findings == 1
    assert score.noise.false_positives == 1
    assert score.noise.low_value == 1
    assert score.noise.snr == pytest.approx(0.5)  # (1+1)/4
    # weighted: high(3)*actionable(1.0) + medium(2)*directional(0.6) = 4.2
    assert score.noise.weighted_signal == pytest.approx(4.2)
    assert score.noise.actionability_rate == pytest.approx(0.5)  # 1 actionable / 2 TPs
