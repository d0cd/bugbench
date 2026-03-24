"""Claude Agent SDK evaluation runner."""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from bugeval.agent_runner import (
    _SYNTHESIS_PROMPT,
    MODEL,
    _has_json_findings,
    build_system_prompt,
    build_user_prompt,
    parse_agent_findings,
    prepare_workspace,
    sanitize_diff,
)
from bugeval.models import TestCase
from bugeval.result_models import ToolResult

log = logging.getLogger(__name__)


async def _run_agent_sdk_async(
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    timeout: int = 300,
    transcript_dir: Path | None = None,
    model: str = "",
    max_turns: int = 30,
) -> ToolResult:
    """Run Claude Code via Agent SDK with automatic continuation.

    Uses ClaudeSDKClient for sequential queries. If the agent exhausts its
    turns without producing JSON findings, a synthesis prompt is sent to
    force output (same session, full context preserved).
    """
    try:
        from claude_agent_sdk import (  # type: ignore[import-untyped]
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            CLIConnectionError,
            CLINotFoundError,
            ResultMessage,
        )
        from claude_agent_sdk.types import (  # type: ignore[import-untyped]
            TextBlock as _SdkTextBlock,
        )
        from claude_agent_sdk.types import (  # type: ignore[import-untyped,import-not-found]
            ThinkingBlock as _SdkThinkingBlock,
        )
        from claude_agent_sdk.types import (  # type: ignore[import-untyped,import-not-found]
            ToolUseBlock as _SdkToolUseBlock,
        )
    except ImportError:
        return ToolResult(
            case_id=case.id,
            tool="agent-sdk",
            context_level=context_level,
            error="claude-agent-sdk not installed. Run: uv add claude-agent-sdk",
        )

    def _capture(
        messages: list[dict[str, Any]],
        message: object,
    ) -> None:
        if isinstance(message, AssistantMessage):
            msg_entry: dict[str, Any] = {"role": "assistant", "content": []}
            for block in getattr(message, "content", []):
                if isinstance(block, _SdkTextBlock):
                    msg_entry["content"].append({"type": "text", "text": block.text})
                elif isinstance(block, _SdkThinkingBlock):
                    msg_entry["content"].append({"type": "thinking", "thinking": block.thinking})
                elif isinstance(block, _SdkToolUseBlock):
                    msg_entry["content"].append(
                        {
                            "type": "tool_use",
                            "name": block.name,
                            "input": block.input,
                        }
                    )
            messages.append(msg_entry)

    t_phases: dict[str, float] = {}
    _t0 = time.monotonic()

    system_prompt = build_system_prompt(context_level)
    sanitized = sanitize_diff(diff)

    # Materialize workspace files — SDK agent reads from cwd
    _t_ws = time.monotonic()
    effective_repo, _temp_dirs = prepare_workspace(
        case,
        diff,
        repo_dir,
        context_level,
    )
    t_phases["materialize_seconds"] = round(time.monotonic() - _t_ws, 2)

    # SDK agent reads files from cwd, no inline diff needed
    user_prompt = build_user_prompt(
        case,
        sanitized,
        context_level,
        inline_diff=False,
    )

    allowed_tools: list[str] = ["Read", "Glob", "Grep", "WebSearch"]
    disallowed = ["Edit", "Write", "Bash", "NotebookEdit"]

    effective_model = model or MODEL
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=effective_model,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed,
        cwd=str(effective_repo) if effective_repo else None,
        max_turns=max_turns,
        permission_mode="acceptEdits",
        env={"CLAUDECODE": ""},
    )

    t_phases["setup_seconds"] = round(time.monotonic() - _t0, 2)
    start = time.monotonic()
    total_cost = 0.0
    session_id = ""
    result_text = ""
    continued = False
    timed_out = False
    transcript_messages: list[dict[str, Any]] = []

    # Reserve 90s for synthesis — primary query gets the rest
    synthesis_budget = 90
    primary_deadline = timeout - synthesis_budget

    try:
        async with ClaudeSDKClient(options=options) as client:
            # Primary query
            await client.query(user_prompt)
            async for message in client.receive_response():
                if time.monotonic() - start > primary_deadline:
                    break
                _capture(transcript_messages, message)
                if isinstance(message, ResultMessage):
                    result_text = message.result or ""
                    total_cost = message.total_cost_usd or 0.0
                    session_id = message.session_id or ""

            # Continuation: if no JSON findings, force synthesis
            # Fires on turn exhaustion AND soft timeout (primary_deadline hit)
            elapsed_so_far = time.monotonic() - start
            if not _has_json_findings(result_text) and elapsed_so_far < timeout:
                continued = True
                log.info(
                    "Case %s: no JSON findings after %d messages, sending synthesis prompt",
                    case.id,
                    len(transcript_messages),
                )
                await client.query(_SYNTHESIS_PROMPT)
                async for message in client.receive_response():
                    if time.monotonic() - start > timeout:
                        timed_out = True
                        break
                    _capture(transcript_messages, message)
                    if isinstance(message, ResultMessage):
                        result_text = message.result or ""
                        total_cost = message.total_cost_usd or 0.0
                        session_id = message.session_id or ""

    except CLINotFoundError as exc:
        return ToolResult(
            case_id=case.id,
            tool="agent-sdk",
            context_level=context_level,
            time_seconds=round(time.monotonic() - start, 2),
            error=f"claude CLI not found: {exc}",
        )
    except CLIConnectionError as exc:
        return ToolResult(
            case_id=case.id,
            tool="agent-sdk",
            context_level=context_level,
            time_seconds=round(time.monotonic() - start, 2),
            error=f"CLI connection error: {exc}",
        )
    except Exception as exc:
        return ToolResult(
            case_id=case.id,
            tool="agent-sdk",
            context_level=context_level,
            time_seconds=round(time.monotonic() - start, 2),
            cost_usd=total_cost,
            error=str(exc),
        )
    finally:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)

    elapsed = time.monotonic() - start
    t_phases["agent_query_seconds"] = round(elapsed, 2)

    # Timeout if hard deadline hit or no result produced within budget
    if timed_out or (elapsed >= timeout and not _has_json_findings(result_text)):
        return ToolResult(
            case_id=case.id,
            tool="agent-sdk",
            context_level=context_level,
            time_seconds=round(elapsed, 2),
            cost_usd=total_cost,
            error=f"Agent SDK timeout after {timeout}s",
        )

    comments = parse_agent_findings(result_text)

    # Save transcript
    transcript_path = ""
    if transcript_dir:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        t_path = transcript_dir / f"{case.id}-sdk.json"
        data = {
            "session_id": session_id,
            "model": effective_model,
            "messages": transcript_messages,
            "result_text": result_text,
            "cost_usd": total_cost,
            "elapsed_seconds": round(elapsed, 2),
            "timing": t_phases,
            "continued": continued,
        }
        t_path.write_text(json.dumps(data, indent=2, default=str))
        transcript_path = str(t_path)

    return ToolResult(
        case_id=case.id,
        tool="agent-sdk",
        context_level=context_level,
        comments=comments,
        time_seconds=round(elapsed, 2),
        cost_usd=total_cost,
        transcript_path=transcript_path,
    )


def run_agent_sdk(
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    timeout: int = 300,
    transcript_dir: Path | None = None,
    model: str = "",
    max_turns: int = 30,
) -> ToolResult:
    """Run Claude Code for review via Agent SDK locally."""
    import asyncio

    return asyncio.run(
        _run_agent_sdk_async(
            case,
            diff,
            repo_dir,
            context_level,
            timeout,
            transcript_dir,
            model=model,
            max_turns=max_turns,
        )
    )
