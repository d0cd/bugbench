"""Tests for agent_cli_runner."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from bugeval.agent_cli_runner import (
    _parse_cli_findings,
    _parse_cli_token_count,
    _parse_codex_json_output,
    _parse_gemini_stream_json,
    _parse_stream_json_output,
    run_claude_cli,
    run_claude_cli_docker,
    run_codex_cli,
    run_gemini_cli,
)


def _make_stream_jsonl(
    result_text: str,
    turns: int = 1,
    cost: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> str:
    """Build a minimal valid stream-json JSONL string for use in tests."""
    lines = [
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": result_text}]}}
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "num_turns": turns,
                "result": result_text,
                "total_cost_usd": cost,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                },
            }
        ),
    ]
    return "\n".join(lines)


def test_run_claude_cli_success(tmp_path: Path) -> None:
    findings_json = '[{"file": "src/main.rs", "line": 10, "summary": "bug"}]'
    result_text = f"Some output\n```json\n{findings_json}\n```\n"
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_stream_jsonl(result_text)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(tmp_path, "review this patch")

    assert result.error is None
    assert len(result.findings) == 1
    assert result.findings[0]["file"] == "src/main.rs"
    assert result.model == "claude-sonnet-4-6"
    assert result.wall_time_seconds >= 0


def test_run_claude_cli_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5)):
        result = run_claude_cli(tmp_path, "prompt", timeout_seconds=5)

    assert result.error == "timeout"
    assert result.findings == []


def test_run_claude_cli_nonzero_exit(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "claude: command not found"

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(tmp_path, "prompt")

    assert result.error is not None
    assert "code 1" in result.error
    assert result.findings == []


def test_parse_cli_findings_with_json_array() -> None:
    stdout = 'Here are the findings:\n```json\n[{"file": "a.rs", "line": 5, "summary": "x"}]\n```'
    findings = _parse_cli_findings(stdout)
    assert len(findings) == 1
    assert findings[0]["file"] == "a.rs"


def test_parse_cli_findings_empty_output() -> None:
    findings = _parse_cli_findings("")
    assert findings == []


def test_run_claude_cli_passes_max_turns(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_claude_cli(tmp_path, "prompt", max_turns=5)

    call_args = mock_run.call_args[0][0]
    assert "--max-turns" in call_args
    assert "5" in call_args


def test_run_claude_cli_docker_calls_docker(tmp_path: Path) -> None:
    """Verify docker run command is constructed correctly."""
    findings_json = '[{"file": "a.rs", "line": 1, "summary": "bug"}]'
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_stream_jsonl(f"```json\n{findings_json}\n```")
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = run_claude_cli_docker(
            repo_dir=tmp_path,
            prompt="Review this code.",
            max_turns=5,
            image="bugeval-agent",
        )

    args = mock_run.call_args[0][0]
    assert args[0] == "docker"
    assert "run" in args
    assert "--rm" in args
    assert "-e" in args
    assert "ANTHROPIC_API_KEY" in args
    assert f"{tmp_path.resolve()}:/work" in args
    assert "bugeval-agent" in args
    assert "--max-turns" in args
    assert "5" in args
    assert result.findings == [{"file": "a.rs", "line": 1, "summary": "bug"}]


def test_run_claude_cli_docker_timeout(tmp_path: Path) -> None:
    """Timeout returns AgentResult with error='timeout'."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=5)):
        result = run_claude_cli_docker(tmp_path, "prompt", image="bugeval-agent")
    assert result.error == "timeout"


def test_run_claude_cli_docker_nonzero_exit(tmp_path: Path) -> None:
    """Non-zero exit code returns AgentResult with error set."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "container error"

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli_docker(tmp_path, "prompt", image="bugeval-agent")
    assert result.error is not None
    assert "code 1" in result.error


def test_run_gemini_cli_success(tmp_path: Path) -> None:
    findings_json = '[{"file": "src/main.rs", "line": 10, "summary": "bug"}]'
    response = f"```json\n{findings_json}\n```"
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_gemini_stream_jsonl(response=response)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_gemini_cli(tmp_path, "review this patch")

    assert result.error is None
    assert len(result.findings) == 1
    assert result.findings[0]["file"] == "src/main.rs"
    assert result.model == "gemini-2.5-flash"
    assert result.wall_time_seconds >= 0


def test_run_gemini_cli_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gemini", timeout=5)):
        result = run_gemini_cli(tmp_path, "prompt", timeout_seconds=5)

    assert result.error == "timeout"
    assert result.findings == []


def test_run_gemini_cli_nonzero_exit(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "gemini: command not found"

    with patch("subprocess.run", return_value=mock_result):
        result = run_gemini_cli(tmp_path, "prompt")

    assert result.error is not None
    assert "code 1" in result.error
    assert result.findings == []


def test_run_codex_cli_success(tmp_path: Path) -> None:
    findings_json = '[{"file": "src/lib.rs", "line": 5, "summary": "issue"}]'
    response = f"```json\n{findings_json}\n```"
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_codex_jsonl(response=response)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_codex_cli(tmp_path, "review this patch")

    assert result.error is None
    assert len(result.findings) == 1
    assert result.findings[0]["file"] == "src/lib.rs"
    assert result.model == "gpt-5.4-mini"
    assert result.wall_time_seconds >= 0


def test_run_codex_cli_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=5)):
        result = run_codex_cli(tmp_path, "prompt", timeout_seconds=5)

    assert result.error == "timeout"
    assert result.findings == []


def test_run_codex_cli_nonzero_exit(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "codex: command not found"

    with patch("subprocess.run", return_value=mock_result):
        result = run_codex_cli(tmp_path, "prompt")

    assert result.error is not None
    assert "code 1" in result.error
    assert result.findings == []


def test_run_gemini_cli_passes_model(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_gemini_cli(tmp_path, "prompt", model="gemini-2.5-flash-lite")

    call_args = mock_run.call_args[0][0]
    assert "-m" in call_args
    assert "gemini-2.5-flash-lite" in call_args


# ---------------------------------------------------------------------------
# Token count parsing
# ---------------------------------------------------------------------------


def test_parse_cli_token_count_total_tokens_pattern() -> None:
    """Parses 'Total tokens: N' pattern."""
    assert _parse_cli_token_count("Total tokens: 1234") == 1234


def test_parse_cli_token_count_input_output_pattern() -> None:
    """Sums input + output tokens when both are present."""
    output = "Input tokens: 100\nOutput tokens: 50"
    assert _parse_cli_token_count(output) == 150


def test_parse_cli_token_count_case_insensitive() -> None:
    """Parsing is case-insensitive."""
    assert _parse_cli_token_count("TOTAL TOKENS: 999") == 999


def test_parse_cli_token_count_returns_zero_when_absent() -> None:
    """Returns 0 when no token info is found."""
    assert _parse_cli_token_count("Here is the review. No bugs found.") == 0


def test_parse_cli_token_count_returns_zero_on_empty() -> None:
    """Returns 0 for empty string."""
    assert _parse_cli_token_count("") == 0


def test_run_claude_cli_includes_token_count_from_output() -> None:
    """run_claude_cli populates token_count from the stream-json result event."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_stream_jsonl(
        "Some reasoning text", turns=3, cost=0.05, input_tokens=300, output_tokens=200
    )
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(Path("/tmp"), "review")

    assert result.token_count == 500
    assert result.turns == 3
    assert result.cost_usd == 0.05
    assert result.response_text == "Some reasoning text"


def test_run_claude_cli_extracts_envelope_metadata(tmp_path: Path) -> None:
    """run_claude_cli extracts turns, cost, tokens, response_text from stream-json result event."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_stream_jsonl(
        "Here is my analysis of the patch.",
        turns=2,
        cost=0.012,
        input_tokens=800,
        output_tokens=150,
    )
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(tmp_path, "prompt")

    assert result.turns == 2
    assert result.cost_usd == 0.012
    assert result.token_count == 950
    assert result.response_text == "Here is my analysis of the patch."


def test_run_claude_cli_missing_result_event_defaults_to_zero(tmp_path: Path) -> None:
    """When stdout has no type=result event, cost/turns/tokens all default to 0."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}
    )
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(tmp_path, "prompt")

    assert result.turns == 0
    assert result.cost_usd == 0.0
    assert result.token_count == 0
    assert result.response_text == ""


def test_run_claude_cli_malformed_stdout_gives_empty_findings(tmp_path: Path) -> None:
    """When stdout is not valid stream-json, no findings or metadata are extracted."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not json at all"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(tmp_path, "prompt")

    assert result.error is None
    assert result.findings == []
    assert result.response_text == ""
    assert result.token_count == 0


# ---------------------------------------------------------------------------
# _parse_stream_json_output
# ---------------------------------------------------------------------------


def test_parse_stream_json_output_extracts_result() -> None:
    """Parses result text, turns, cost, and tokens from type=result event."""
    stdout = _make_stream_jsonl(
        "hello world", turns=2, cost=0.05, input_tokens=100, output_tokens=50
    )
    conv, result_text, tokens, cost, turns = _parse_stream_json_output(stdout)
    assert result_text == "hello world"
    assert turns == 2
    assert cost == 0.05
    assert tokens == 150


def test_parse_stream_json_output_includes_cache_tokens() -> None:
    """Token count includes cache_creation + cache_read tokens."""
    stdout = _make_stream_jsonl(
        "x", input_tokens=10, output_tokens=5, cache_creation=200, cache_read=50
    )
    _, _, tokens, _, _ = _parse_stream_json_output(stdout)
    assert tokens == 265


def test_parse_stream_json_output_builds_conversation() -> None:
    """type=assistant events are included in conversation."""
    stdout = _make_stream_jsonl("my analysis")
    conv, _, _, _, _ = _parse_stream_json_output(stdout)
    assert len(conv) == 1
    assert conv[0]["role"] == "assistant"
    assert conv[0]["content"][0]["text"] == "my analysis"


def test_parse_stream_json_output_includes_tool_use_in_conversation() -> None:
    """tool_use content blocks in assistant messages appear in conversation."""
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Read",
                            "input": {"file_path": "src/main.rs"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "fn main() {}"}
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "```json\n[]\n```"}]},
            }
        ),
        json.dumps(
            {
                "type": "result",
                "num_turns": 2,
                "result": "```json\n[]\n```",
                "total_cost_usd": 0.01,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
        ),
    ]
    stdout = "\n".join(lines)
    conv, _, _, _, turns = _parse_stream_json_output(stdout)
    assert turns == 2
    assert len(conv) == 3
    assert conv[0]["role"] == "assistant"
    assert conv[0]["content"][0]["type"] == "tool_use"
    assert conv[1]["role"] == "user"
    assert conv[1]["content"][0]["type"] == "tool_result"


def test_parse_stream_json_output_ignores_noise_events() -> None:
    """system and stream_event types are ignored."""
    lines = [
        json.dumps({"type": "system", "subtype": "init", "data": "ignored"}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta"}}),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
        json.dumps(
            {
                "type": "result",
                "num_turns": 1,
                "result": "hi",
                "total_cost_usd": 0.0,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
        ),
    ]
    conv, result_text, _, _, _ = _parse_stream_json_output("\n".join(lines))
    assert len(conv) == 1
    assert result_text == "hi"


def test_run_claude_cli_populates_conversation(tmp_path: Path) -> None:
    """run_claude_cli populates AgentResult.conversation from stream-json assistant events."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_stream_jsonl("analysis text")
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_claude_cli(tmp_path, "prompt")

    assert len(result.conversation) == 1
    assert result.conversation[0]["role"] == "assistant"


def test_run_claude_cli_uses_stream_json_format(tmp_path: Path) -> None:
    """run_claude_cli passes --output-format stream-json --verbose to the subprocess."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_stream_jsonl("")
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_claude_cli(tmp_path, "prompt")

    call_args = mock_run.call_args[0][0]
    assert "stream-json" in call_args
    assert "--verbose" in call_args
    # --output-format value should be stream-json, not bare json
    fmt_idx = call_args.index("--output-format")
    assert call_args[fmt_idx + 1] == "stream-json"


def test_run_claude_cli_disables_user_settings(tmp_path: Path) -> None:
    """run_claude_cli passes --setting-sources project,local to skip user hooks."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_stream_jsonl("")
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_claude_cli(tmp_path, "prompt")

    call_args = mock_run.call_args[0][0]
    assert "--setting-sources" in call_args
    src_idx = call_args.index("--setting-sources")
    assert call_args[src_idx + 1] == "project,local"


def test_run_codex_cli_passes_model(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_codex_cli(tmp_path, "prompt", model="gpt-5.4-mini")

    call_args = mock_run.call_args[0][0]
    assert "gpt-5.4-mini" in call_args


# ---------------------------------------------------------------------------
# Gemini CLI: stream-json parsing
# ---------------------------------------------------------------------------


def _make_gemini_stream_jsonl(
    response: str = "hello",
    total_tokens: int = 100,
    input_tokens: int = 80,
    output_tokens: int = 10,
    cached: int = 0,
    duration_ms: int = 1000,
    tool_calls: int = 0,
) -> str:
    """Build Gemini CLI stream-json JSONL output."""
    lines = [
        json.dumps({"type": "init", "session_id": "abc", "model": "gemini-2.5-flash"}),
        json.dumps({"type": "message", "role": "user", "content": "prompt"}),
        json.dumps({"type": "message", "role": "assistant", "content": response, "delta": True}),
        json.dumps(
            {
                "type": "result",
                "status": "success",
                "stats": {
                    "total_tokens": total_tokens,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached": cached,
                    "input": input_tokens - cached,
                    "duration_ms": duration_ms,
                    "tool_calls": tool_calls,
                },
            }
        ),
    ]
    return "\n".join(lines)


def test_parse_gemini_stream_json_extracts_stats() -> None:
    stdout = _make_gemini_stream_jsonl(
        response="bug found",
        total_tokens=9500,
        input_tokens=9000,
        output_tokens=50,
        cached=8000,
    )
    conv, result_text, token_count, in_toks, out_toks, turns = _parse_gemini_stream_json(stdout)
    assert result_text == "bug found"
    assert token_count == 9500
    assert in_toks == 9000
    assert out_toks == 50
    assert turns == 1  # one assistant message = one turn


def test_parse_gemini_stream_json_counts_tool_turns() -> None:
    """Multiple assistant messages count as multiple turns."""
    msg1 = {"type": "message", "role": "assistant", "content": "reading file", "delta": True}
    msg2 = {"type": "message", "role": "assistant", "content": "found it", "delta": True}
    lines = [
        json.dumps({"type": "init", "session_id": "abc", "model": "gemini-2.5-flash"}),
        json.dumps(msg1),
        json.dumps(msg2),
        json.dumps(
            {
                "type": "result",
                "status": "success",
                "stats": {
                    "total_tokens": 100,
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "cached": 0,
                    "input": 80,
                    "duration_ms": 500,
                    "tool_calls": 1,
                },
            }
        ),
    ]
    _, result_text, _, _, _, turns = _parse_gemini_stream_json("\n".join(lines))
    assert turns == 2
    assert result_text == "found it"  # last assistant message


def test_parse_gemini_stream_json_empty_output() -> None:
    conv, result_text, token_count, in_toks, out_toks, turns = _parse_gemini_stream_json("")
    assert result_text == ""
    assert token_count == 0
    assert turns == 0


def test_run_gemini_cli_uses_stream_json(tmp_path: Path) -> None:
    """run_gemini_cli should pass -o stream-json -y -s false flags."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_gemini_stream_jsonl()
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_gemini_cli(tmp_path, "prompt")

    call_args = mock_run.call_args[0][0]
    assert "-o" in call_args
    o_idx = call_args.index("-o")
    assert call_args[o_idx + 1] == "stream-json"
    assert "-y" in call_args
    assert "-s" in call_args
    s_idx = call_args.index("-s")
    assert call_args[s_idx + 1] == "false"


def test_run_gemini_cli_extracts_tokens_from_stream_json(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_gemini_stream_jsonl(
        response="```json\n[]\n```",
        total_tokens=9500,
        input_tokens=9000,
        output_tokens=50,
    )
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_gemini_cli(tmp_path, "prompt")

    assert result.token_count == 9500
    assert result.response_text == "```json\n[]\n```"


# ---------------------------------------------------------------------------
# Codex CLI: JSON output parsing
# ---------------------------------------------------------------------------


def _make_codex_jsonl(
    response: str = "hello",
    input_tokens: int = 7000,
    cached_input_tokens: int = 6000,
    output_tokens: int = 50,
) -> str:
    """Build Codex CLI --json JSONL output."""
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "019d-abc"}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": response,
                },
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                },
            }
        ),
    ]
    return "\n".join(lines)


def test_parse_codex_json_output_extracts_stats() -> None:
    stdout = _make_codex_jsonl(
        response="bug here",
        input_tokens=8000,
        cached_input_tokens=7000,
        output_tokens=100,
    )
    conv, result_text, token_count, in_toks, out_toks, turns = _parse_codex_json_output(stdout)
    assert result_text == "bug here"
    assert token_count == 8100  # input + output
    assert in_toks == 8000
    assert out_toks == 100
    assert turns == 1


def test_parse_codex_json_output_multi_turn() -> None:
    """Multiple turn.completed events accumulate tokens and count turns."""
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "019d-abc"}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "reading..."},
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": 50},
            }
        ),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_1", "type": "agent_message", "text": "found bug"},
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 60},
            }
        ),
    ]
    conv, result_text, token_count, in_toks, out_toks, turns = _parse_codex_json_output(
        "\n".join(lines)
    )
    assert result_text == "found bug"  # last message
    assert token_count == 2310  # (1000+50) + (1200+60)
    assert in_toks == 2200
    assert out_toks == 110
    assert turns == 2


def test_parse_codex_json_output_empty() -> None:
    conv, result_text, token_count, in_toks, out_toks, turns = _parse_codex_json_output("")
    assert result_text == ""
    assert token_count == 0
    assert turns == 0


def test_run_codex_cli_uses_exec_full_auto(tmp_path: Path) -> None:
    """run_codex_cli should use 'codex exec' with --full-auto and --json."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_codex_jsonl()
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_codex_cli(tmp_path, "prompt")

    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "codex"
    assert call_args[1] == "exec"
    assert "--full-auto" in call_args
    assert "--json" in call_args


def test_run_codex_cli_uses_cd_flag(tmp_path: Path) -> None:
    """run_codex_cli should pass -C <dir> instead of relying on cwd."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_codex_jsonl()
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        run_codex_cli(tmp_path, "prompt")

    call_args = mock_run.call_args[0][0]
    assert "-C" in call_args
    c_idx = call_args.index("-C")
    assert call_args[c_idx + 1] == str(tmp_path)


def test_run_codex_cli_extracts_tokens_from_json(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_codex_jsonl(
        response="```json\n[]\n```",
        input_tokens=8000,
        output_tokens=100,
    )
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_codex_cli(tmp_path, "prompt")

    assert result.token_count == 8100
    assert result.turns == 1
    assert result.response_text == "```json\n[]\n```"


# ---------------------------------------------------------------------------
# Gemini CLI: conversation building
# ---------------------------------------------------------------------------


def test_parse_gemini_stream_json_builds_conversation() -> None:
    """Gemini parser should build conversation from message/tool_use/tool_result events."""
    lines = [
        json.dumps({"type": "init", "session_id": "abc", "model": "gemini-2.5-flash"}),
        json.dumps({"type": "message", "role": "user", "content": "review this"}),
        json.dumps(
            {
                "type": "tool_use",
                "tool_name": "read_file",
                "tool_id": "read_1",
                "parameters": {"file_path": "src/main.rs"},
            }
        ),
        json.dumps(
            {
                "type": "tool_result",
                "tool_id": "read_1",
                "status": "success",
                "output": "fn main() {}",
            }
        ),
        json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "content": "found a bug",
                "delta": True,
            }
        ),
        json.dumps(
            {
                "type": "result",
                "status": "success",
                "stats": {
                    "total_tokens": 100,
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "cached": 0,
                    "input": 80,
                    "duration_ms": 500,
                    "tool_calls": 1,
                },
            }
        ),
    ]
    stdout = "\n".join(lines)
    conv, result_text, token_count, in_toks, out_toks, turns = _parse_gemini_stream_json(stdout)
    assert len(conv) == 4  # user msg, tool_use, tool_result, assistant msg
    assert conv[0]["role"] == "user"
    assert conv[1]["role"] == "assistant"
    assert conv[1]["tool_use"]["name"] == "read_file"
    assert conv[2]["role"] == "tool"
    assert conv[2]["tool_id"] == "read_1"
    assert conv[3]["role"] == "assistant"
    assert conv[3]["content"] == "found a bug"
    assert result_text == "found a bug"
    assert turns == 1  # only assistant messages count


def test_run_gemini_cli_populates_conversation(tmp_path: Path) -> None:
    """run_gemini_cli should populate AgentResult.conversation."""
    lines = [
        json.dumps({"type": "init", "session_id": "abc", "model": "gemini-2.5-flash"}),
        json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "content": "analysis",
                "delta": True,
            }
        ),
        json.dumps(
            {
                "type": "result",
                "status": "success",
                "stats": {
                    "total_tokens": 100,
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "cached": 0,
                    "input": 80,
                    "duration_ms": 500,
                    "tool_calls": 0,
                },
            }
        ),
    ]
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "\n".join(lines)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_gemini_cli(tmp_path, "prompt")

    assert len(result.conversation) == 1
    assert result.conversation[0]["role"] == "assistant"


def test_run_gemini_cli_estimates_cost(tmp_path: Path) -> None:
    """run_gemini_cli should compute cost_usd from token counts + pricing."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_gemini_stream_jsonl(
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_gemini_cli(tmp_path, "prompt", model="gemini-2.5-flash")

    # gemini-2.5-flash: $0.15/MTok input, $0.60/MTok output
    # cost = 1M * 0.15/1M + 100K * 0.60/1M = 0.15 + 0.06 = 0.21
    assert result.cost_usd > 0
    assert abs(result.cost_usd - 0.21) < 0.01


# ---------------------------------------------------------------------------
# Codex CLI: conversation building
# ---------------------------------------------------------------------------


def test_parse_codex_json_output_builds_conversation() -> None:
    """Codex parser should build conversation from item events."""
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "019d-abc"}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": "Let me check the code",
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "ls src/",
                    "aggregated_output": "main.rs\nlib.rs\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "agent_message",
                    "text": "Found a bug",
                },
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 0,
                    "output_tokens": 50,
                },
            }
        ),
    ]
    stdout = "\n".join(lines)
    conv, result_text, token_count, in_toks, out_toks, turns = _parse_codex_json_output(stdout)
    assert len(conv) == 3
    assert conv[0]["role"] == "assistant"
    assert conv[0]["content"] == "Let me check the code"
    assert conv[1]["role"] == "tool"
    assert conv[1]["command"] == "ls src/"
    assert conv[2]["role"] == "assistant"
    assert conv[2]["content"] == "Found a bug"
    assert result_text == "Found a bug"


def test_run_codex_cli_populates_conversation(tmp_path: Path) -> None:
    """run_codex_cli should populate AgentResult.conversation."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_codex_jsonl(response="analysis")
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_codex_cli(tmp_path, "prompt")

    assert len(result.conversation) == 1
    assert result.conversation[0]["role"] == "assistant"


def test_run_codex_cli_estimates_cost(tmp_path: Path) -> None:
    """run_codex_cli should compute cost_usd from token counts + pricing."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_codex_jsonl(
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_codex_cli(tmp_path, "prompt", model="gpt-5.4-mini")

    # gpt-5.4-mini: $0.40/MTok input, $1.60/MTok output
    # cost = 1M * 0.40/1M + 100K * 1.60/1M = 0.40 + 0.16 = 0.56
    assert result.cost_usd > 0
    assert abs(result.cost_usd - 0.56) < 0.01
