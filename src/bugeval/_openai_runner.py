"""OpenAI API agent evaluation runner."""

from __future__ import annotations

import json
import logging
import shutil
import time
import time as _time
from pathlib import Path
from typing import Any

from bugeval.agent_runner import (
    COST_CEILING_USD,
    _execute_tool,
    _get_tools_for_context,
    _make_tool_result,
    build_system_prompt,
    build_user_prompt,
    parse_agent_findings,
    prepare_workspace,
    sanitize_diff,
)
from bugeval.models import TestCase
from bugeval.result_models import ToolResult

log = logging.getLogger(__name__)


def run_openai_api(
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    max_turns: int = 30,
    timeout: int = 300,
    transcript_dir: Path | None = None,
    thinking_budget: int = 0,
    model: str = "",
) -> ToolResult:
    """Call OpenAI API with multi-turn tool use and collect findings."""
    try:
        import openai  # type: ignore[import-untyped]
    except ImportError:
        return ToolResult(
            case_id=case.id,
            tool="agent-openai",
            context_level=context_level,
            error="openai not installed. Run: pip install openai",
        )

    system = build_system_prompt(context_level)
    sanitized = sanitize_diff(diff)
    tools_for_ctx = _get_tools_for_context(context_level)

    # Materialize workspace files for the agent to read
    effective_repo, _temp_dirs = prepare_workspace(
        case,
        diff,
        repo_dir,
        context_level,
    )

    inline = context_level == "diff-only"
    user_msg = build_user_prompt(
        case,
        sanitized,
        context_level,
        inline_diff=inline,
    )

    # Convert TOOL_DEFS to OpenAI function tool format.
    # Always include web_search_preview (native server tool — OpenAI executes
    # searches and returns results as regular assistant content).
    openai_tools: list[dict[str, Any]] = [{"type": "web_search_preview"}]
    if tools_for_ctx:
        for td in tools_for_ctx:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": td["name"],
                        "description": td["description"],
                        "parameters": td["input_schema"],
                    },
                }
            )

    client = openai.OpenAI()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    # Separate transcript list (includes system for completeness)
    transcript: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]

    # o4-mini pricing: $1.10/$4.40 per MTok
    OPENAI_INPUT_RATE = 1.10 / 1_000_000
    OPENAI_OUTPUT_RATE = 4.40 / 1_000_000
    total_cost = 0.0
    start = time.monotonic()
    effective_model = model or "o4-mini"

    try:
        for _turn in range(max_turns):
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                return _make_tool_result(
                    case=case,
                    tool="agent-openai",
                    context_level=context_level,
                    start=start,
                    messages=transcript,
                    error="Agent timeout exceeded",
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )
            if total_cost > COST_CEILING_USD:
                return _make_tool_result(
                    case=case,
                    tool="agent-openai",
                    context_level=context_level,
                    start=start,
                    messages=transcript,
                    error=f"Cost ceiling exceeded: ${total_cost:.2f}",
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )

            kwargs: dict[str, Any] = {
                "model": effective_model,
                "messages": messages,
                "tools": openai_tools,
            }

            for _attempt in range(3):
                try:
                    response = client.chat.completions.create(**kwargs)
                    break
                except openai.RateLimitError:
                    wait = 2**_attempt * 5  # 5s, 10s, 20s
                    log.warning(
                        "Rate limited, retrying in %ds (attempt %d/3)",
                        wait,
                        _attempt + 1,
                    )
                    _time.sleep(wait)
            else:
                return _make_tool_result(
                    case=case,
                    tool="agent-openai",
                    context_level=context_level,
                    start=start,
                    messages=transcript,
                    error="Rate limited after 3 retries",
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )

            # Estimate cost from usage
            usage = getattr(response, "usage", None)
            if usage:
                inp = getattr(usage, "prompt_tokens", 0) or 0
                out = getattr(usage, "completion_tokens", 0) or 0
                total_cost += round(inp * OPENAI_INPUT_RATE + out * OPENAI_OUTPUT_RATE, 6)

            choice = response.choices[0]  # type: ignore[index]
            message = choice.message
            finish_reason = choice.finish_reason

            if finish_reason == "tool_calls" and message.tool_calls:
                # Append assistant message with tool calls
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }
                messages.append(assistant_msg)
                transcript.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": tc.function.name,
                                "input": tc.function.arguments,
                                "id": tc.id,
                            }
                            for tc in message.tool_calls
                        ],
                    }
                )

                # Execute each tool call and feed results back
                for tc in message.tool_calls:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}
                    if effective_repo is None:
                        result_text = "Error: no repo available"
                    else:
                        result_text = _execute_tool(
                            fn_name,
                            fn_args,
                            effective_repo,
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_text,
                        }
                    )
                    transcript.append(
                        {
                            "role": "user",
                            "content": [{"type": "tool_result", "tool_call_id": tc.id}],
                        }
                    )
            else:
                # Final text response
                final_text = message.content or ""
                transcript.append(
                    {
                        "role": "assistant",
                        "content": final_text,
                    }
                )
                comments = parse_agent_findings(final_text)
                return _make_tool_result(
                    case=case,
                    tool="agent-openai",
                    context_level=context_level,
                    start=start,
                    messages=transcript,
                    comments=comments,
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )

        return _make_tool_result(
            case=case,
            tool="agent-openai",
            context_level=context_level,
            start=start,
            messages=transcript,
            error=f"Exhausted {max_turns} turns without final response",
            cost_usd=total_cost,
            transcript_dir=transcript_dir,
        )
    except Exception as exc:
        return _make_tool_result(
            case=case,
            tool="agent-openai",
            context_level=context_level,
            start=start,
            messages=transcript,
            error=str(exc),
            cost_usd=total_cost,
            transcript_dir=transcript_dir,
        )
    finally:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)
