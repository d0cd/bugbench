"""Claude Agent SDK runner for agent evaluation."""

from __future__ import annotations

import time
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    CLIConnectionError,
    CLINotFoundError,
    ResultMessage,
    query,
)

from bugeval.agent_cli_runner import _parse_cli_findings
from bugeval.agent_models import AgentResult


async def run_agent_sdk(
    repo_dir: Path,
    system_prompt: str,
    user_prompt: str,
    max_turns: int = 10,
    model: str = "claude-sonnet-4-6",
    max_budget_usd: float = 1.0,
    context_level: str = "diff+repo",
) -> AgentResult:
    """Run the Claude Agent SDK against repo_dir with the given prompt.

    Returns AgentResult. On error, sets AgentResult.error (never raises).
    """
    allowed = ["Read", "Glob", "Grep"] if context_level != "diff-only" else []
    options = ClaudeAgentOptions(
        cwd=str(repo_dir),
        allowed_tools=allowed,
        system_prompt=system_prompt,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        permission_mode="acceptEdits",
        model=model,
    )

    start = time.monotonic()
    result_text = ""

    try:
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, ResultMessage):
                result_text = message.result or ""
    except CLINotFoundError as exc:
        return AgentResult(
            wall_time_seconds=time.monotonic() - start,
            model=model,
            error=f"claude CLI not found: {exc}",
        )
    except CLIConnectionError as exc:
        return AgentResult(
            wall_time_seconds=time.monotonic() - start,
            model=model,
            error=f"connection error: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return AgentResult(
            wall_time_seconds=time.monotonic() - start,
            model=model,
            error=str(exc),
        )

    wall_time = time.monotonic() - start
    findings = _parse_cli_findings(result_text)

    return AgentResult(
        findings=findings,
        stdout=result_text,
        wall_time_seconds=wall_time,
        model=model,
    )
