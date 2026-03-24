"""CLI-based agent evaluation runners (Claude, Gemini, Codex)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bugeval.agent_runner import (
    build_system_prompt,
    build_user_prompt,
    parse_agent_findings,
    prepare_workspace,
    sanitize_diff,
)
from bugeval.models import TestCase
from bugeval.result_models import ToolResult


def _estimate_claude_cli_cost(cost_info: dict[str, Any]) -> float:
    """Estimate cost from Claude CLI JSON output."""
    inp = cost_info.get("input_tokens", 0) or 0
    out = cost_info.get("output_tokens", 0) or 0
    cache_read = cost_info.get("cache_read_input_tokens", 0) or 0
    cache_create = cost_info.get("cache_creation_input_tokens", 0) or 0
    # Sonnet pricing: $3 input, $15 output, $0.30 cache read, $3.75 cache write per MTok
    return round(
        inp * 3.0 / 1e6 + out * 15.0 / 1e6 + cache_read * 0.30 / 1e6 + cache_create * 3.75 / 1e6,
        6,
    )


def _save_cli_transcript(
    transcript_dir: Path,
    case_id: str,
    cli_tool: str,
    prompt: str,
    output: Any,
) -> str:
    """Save CLI interaction as transcript JSON."""
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"{case_id}-{cli_tool}.json"
    data = {
        "tool": cli_tool,
        "prompt": prompt[:5000],  # Truncate for sanity
        "output": output if isinstance(output, dict) else str(output)[:10000],
    }
    path.write_text(json.dumps(data, indent=2, default=str))
    return str(path)


@dataclass(frozen=True)
class _CliConfig:
    """Configuration for a CLI-based agent tool."""

    binary: str
    tool_label: str
    prepend_system: bool
    build_cmd: Callable[[str, str, str], list[str]]
    parse_output: Callable[[str], tuple[str, float]]


def _run_cli_tool(
    config: _CliConfig,
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    timeout: int,
    system_prompt: str,
    transcript_dir: Path | None = None,
    model: str = "",
) -> ToolResult:
    """Generic CLI runner shared by all CLI-based agent tools."""
    sanitized = sanitize_diff(diff)

    # Materialize workspace files -- CLI tools read from cwd
    effective_repo, _temp_dirs = prepare_workspace(
        case,
        diff,
        repo_dir,
        context_level,
    )

    # CLI runners can read files from cwd, so no inline diff needed
    user_prompt = build_user_prompt(
        case,
        sanitized,
        context_level,
        inline_diff=False,
    )

    cmd = config.build_cmd(system_prompt, context_level, model)
    full_prompt = f"{system_prompt}\n\n{user_prompt}" if config.prepend_system else user_prompt

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(effective_repo) if effective_repo else None,
        )
        elapsed = time.monotonic() - start

        response_text, cost_usd = config.parse_output(result.stdout)

        transcript_path = ""
        if transcript_dir:
            transcript_path = _save_cli_transcript(
                transcript_dir,
                case.id,
                config.binary,
                full_prompt,
                {"stdout": result.stdout[:5000], "stderr": result.stderr[:2000]}
                if config.binary != "claude"
                else _try_parse_json_or_raw(result.stdout),
            )

        comments = parse_agent_findings(response_text)
        return ToolResult(
            case_id=case.id,
            tool=config.tool_label,
            context_level=context_level,
            comments=comments,
            time_seconds=round(elapsed, 2),
            cost_usd=cost_usd,
            transcript_path=transcript_path,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return ToolResult(
            case_id=case.id,
            tool=config.tool_label,
            context_level=context_level,
            time_seconds=round(elapsed, 2),
            error=f"CLI timed out after {timeout}s",
        )
    except FileNotFoundError:
        elapsed = time.monotonic() - start
        return ToolResult(
            case_id=case.id,
            tool=config.tool_label,
            context_level=context_level,
            time_seconds=round(elapsed, 2),
            error=f"{config.binary} CLI not found on PATH",
        )
    finally:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)


def _try_parse_json_or_raw(stdout: str) -> Any:
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return stdout


# ---------------------------------------------------------------------------
# Per-tool command builders and output parsers
# ---------------------------------------------------------------------------


def _claude_build_cmd(
    system_prompt: str,
    context_level: str,
    model: str = "",
) -> list[str]:
    cmd = ["claude", "-p", "--output-format", "json"]
    cmd.extend(["--system-prompt", system_prompt])
    if model:
        cmd.extend(["--model", model])
    if context_level == "diff-only":
        cmd.extend(["--disallowedTools", "Read,Edit,Bash,Glob,Grep,Write"])
    else:
        cmd.extend(["--allowedTools", "Read,Glob,Grep,WebSearch"])
    cmd.extend(["--max-turns", "30"])
    return cmd


def _claude_parse_output(stdout: str) -> tuple[str, float]:
    try:
        parsed = json.loads(stdout)
        if not isinstance(parsed, dict):
            return stdout, 0.0
        response_text = parsed.get("result", stdout)
        # Try total_cost_usd first (new format), fall back to cost dict
        cost_usd = parsed.get("total_cost_usd", 0.0)
        if not cost_usd:
            cost_usd = _estimate_claude_cli_cost(parsed.get("cost", {}))
        return response_text, float(cost_usd)
    except (json.JSONDecodeError, ValueError):
        return stdout, 0.0


def _gemini_build_cmd(
    _system_prompt: str,
    context_level: str,
    model: str = "",
) -> list[str]:
    cmd = ["gemini", "-p", "--output-format", "json"]
    if model:
        cmd.extend(["-m", model])
    if context_level != "diff-only":
        cmd.extend(["--yolo"])
    return cmd


def _plain_parse_output(stdout: str) -> tuple[str, float]:
    return stdout, 0.0


def _codex_build_cmd(
    _system_prompt: str,
    context_level: str,
    model: str = "",
) -> list[str]:
    sandbox = "read-only" if context_level == "diff-only" else "workspace-write"
    cmd = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        sandbox,
        "--ask-for-approval",
        "never",
    ]
    if model:
        cmd.extend(["-m", model])
    return cmd


_CLAUDE_CLI = _CliConfig(
    binary="claude",
    tool_label="agent-cli-claude",
    prepend_system=False,
    build_cmd=_claude_build_cmd,
    parse_output=_claude_parse_output,
)
_GEMINI_CLI = _CliConfig(
    binary="gemini",
    tool_label="agent-cli-gemini",
    prepend_system=True,
    build_cmd=_gemini_build_cmd,
    parse_output=_plain_parse_output,
)
_CODEX_CLI = _CliConfig(
    binary="codex",
    tool_label="agent-cli-codex",
    prepend_system=True,
    build_cmd=_codex_build_cmd,
    parse_output=_plain_parse_output,
)

_CLI_CONFIGS: dict[str, _CliConfig] = {
    "claude": _CLAUDE_CLI,
    "gemini": _GEMINI_CLI,
    "codex": _CODEX_CLI,
}


# ---------------------------------------------------------------------------
# Thin wrappers (preserve existing call signatures for backward compat)
# ---------------------------------------------------------------------------


def _run_claude_cli(
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    timeout: int,
    system_prompt: str,
    transcript_dir: Path | None = None,
    model: str = "",
) -> ToolResult:
    """Run Claude Code CLI with full flag support."""
    return _run_cli_tool(
        _CLAUDE_CLI,
        case,
        diff,
        repo_dir,
        context_level,
        timeout,
        system_prompt,
        transcript_dir,
        model=model,
    )


def _run_gemini_cli(
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    timeout: int,
    system_prompt: str,
    transcript_dir: Path | None = None,
    model: str = "",
) -> ToolResult:
    """Run Gemini CLI."""
    return _run_cli_tool(
        _GEMINI_CLI,
        case,
        diff,
        repo_dir,
        context_level,
        timeout,
        system_prompt,
        transcript_dir,
        model=model,
    )


def _run_codex_cli(
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    timeout: int,
    system_prompt: str,
    transcript_dir: Path | None = None,
    model: str = "",
) -> ToolResult:
    """Run OpenAI Codex CLI."""
    return _run_cli_tool(
        _CODEX_CLI,
        case,
        diff,
        repo_dir,
        context_level,
        timeout,
        system_prompt,
        transcript_dir,
        model=model,
    )


def run_agent_cli(
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    cli_tool: str = "claude",
    timeout: int = 300,
    transcript_dir: Path | None = None,
    model: str = "",
) -> ToolResult:
    """Dispatch to the appropriate CLI runner."""
    system_prompt = build_system_prompt(context_level)
    if cli_tool == "claude":
        return _run_claude_cli(
            case,
            diff,
            repo_dir,
            context_level,
            timeout,
            system_prompt,
            transcript_dir,
            model=model,
        )
    elif cli_tool == "gemini":
        return _run_gemini_cli(
            case,
            diff,
            repo_dir,
            context_level,
            timeout,
            system_prompt,
            transcript_dir,
            model=model,
        )
    elif cli_tool == "codex":
        return _run_codex_cli(
            case,
            diff,
            repo_dir,
            context_level,
            timeout,
            system_prompt,
            transcript_dir,
            model=model,
        )
    return ToolResult(
        case_id=case.id,
        tool=f"agent-cli-{cli_tool}",
        context_level=context_level,
        error=f"Unknown CLI tool: {cli_tool}",
    )
