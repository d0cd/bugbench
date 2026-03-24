"""Anthropic Claude API agent evaluation runner."""

from __future__ import annotations

import logging
import shutil
import time
import time as _time
from pathlib import Path
from typing import Any

import anthropic
from anthropic import RateLimitError

from bugeval.agent_runner import (
    ANTHROPIC_WEB_SEARCH_TOOL,
    API_TIMEOUT_SECONDS,
    COST_CEILING_USD,
    MAX_TOKENS,
    MODEL,
    _calc_cost,
    _execute_tool,
    _get_file_tools_for_context,
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


def run_anthropic_api(
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
    """Call Anthropic API with multi-turn tool use and collect findings.

    Uses Anthropic's server-side web_search_20250305 tool for web search
    (executed by Anthropic, not locally) plus local file tools for repo access.
    """
    system = build_system_prompt(context_level)
    sanitized = sanitize_diff(diff)
    # File tools (read_file, list_directory, search_text) + Anthropic server web search
    file_tools = _get_file_tools_for_context(context_level)
    tools: list[dict[str, Any]] = list(file_tools) + [ANTHROPIC_WEB_SEARCH_TOOL]

    # Materialize workspace files for the agent to read
    effective_repo, _temp_dirs = prepare_workspace(
        case,
        diff,
        repo_dir,
        context_level,
    )

    # diff-only API runners have no file tools, so inline the diff
    inline = context_level == "diff-only"
    user_msg = build_user_prompt(
        case,
        sanitized,
        context_level,
        inline_diff=inline,
    )

    client = anthropic.Anthropic(timeout=API_TIMEOUT_SECONDS)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_msg}]
    total_cost = 0.0
    start = time.monotonic()

    try:
        for _turn in range(max_turns):
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                return _make_tool_result(
                    case=case,
                    tool="agent",
                    context_level=context_level,
                    start=start,
                    messages=messages,
                    error="Agent timeout exceeded",
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )
            if total_cost > COST_CEILING_USD:
                return _make_tool_result(
                    case=case,
                    tool="agent",
                    context_level=context_level,
                    start=start,
                    messages=messages,
                    error=f"Cost ceiling exceeded: ${total_cost:.2f} > ${COST_CEILING_USD}",
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )
            kwargs: dict[str, Any] = {
                "model": model or MODEL,
                "max_tokens": MAX_TOKENS,
                "system": system,
                "messages": messages,
            }
            if thinking_budget > 0:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget,
                }
                kwargs["max_tokens"] = max(MAX_TOKENS, thinking_budget + 4096)
            if tools:
                kwargs["tools"] = tools
            for _attempt in range(3):
                try:
                    response = client.messages.create(**kwargs)  # type: ignore[arg-type]
                    break
                except RateLimitError:
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
                    tool="agent",
                    context_level=context_level,
                    start=start,
                    messages=messages,
                    error="Rate limited after 3 retries",
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )
            total_cost += _calc_cost(response.usage)

            # Check if model wants to use tools
            if response.stop_reason == "tool_use":
                # Append assistant message
                messages.append({"role": "assistant", "content": response.content})
                # Execute each tool call
                tool_results: list[dict[str, Any]] = []
                for block in response.content:
                    block_type = getattr(block, "type", None)
                    if block_type == "thinking":
                        # Thinking blocks are kept in transcript only
                        continue
                    if block_type == "tool_use":
                        if effective_repo is None:
                            result_text = "Error: no repo available"
                        else:
                            result_text = _execute_tool(
                                block.name,  # type: ignore[union-attr]
                                block.input,  # type: ignore[union-attr]
                                effective_repo,
                            )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,  # type: ignore[union-attr]
                                "content": result_text,
                            }
                        )
                messages.append({"role": "user", "content": tool_results})
            else:
                # Final text response — append to transcript
                messages.append({"role": "assistant", "content": response.content})
                final_text = ""
                for block in response.content:
                    block_type = getattr(block, "type", None)
                    if block_type == "thinking":
                        # Thinking blocks are kept in transcript only
                        continue
                    if block_type == "text":
                        final_text += block.text  # type: ignore[union-attr]
                comments = parse_agent_findings(final_text)
                return _make_tool_result(
                    case=case,
                    tool="agent",
                    context_level=context_level,
                    start=start,
                    messages=messages,
                    comments=comments,
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )
        # Exhausted turns
        return _make_tool_result(
            case=case,
            tool="agent",
            context_level=context_level,
            start=start,
            messages=messages,
            error=f"Exhausted {max_turns} turns without final response",
            cost_usd=total_cost,
            transcript_dir=transcript_dir,
        )
    except Exception as exc:
        return _make_tool_result(
            case=case,
            tool="agent",
            context_level=context_level,
            start=start,
            messages=messages,
            error=str(exc),
            cost_usd=total_cost,
            transcript_dir=transcript_dir,
        )
    finally:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)
