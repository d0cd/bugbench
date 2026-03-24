"""Google Gemini API agent evaluation runner."""

from __future__ import annotations

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


def run_google_api(
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
    """Call Google Gemini API with multi-turn tool use and collect findings."""
    try:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types as genai_types  # type: ignore[import-untyped]
    except ImportError:
        return ToolResult(
            case_id=case.id,
            tool="agent-gemini",
            context_level=context_level,
            error="google-genai not installed. Run: pip install google-genai",
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

    # Convert TOOL_DEFS to Google FunctionDeclaration format
    google_tools: list[Any] = []
    if tools_for_ctx:
        func_decls: list[Any] = []
        for td in tools_for_ctx:
            schema = td["input_schema"].copy()
            # Google expects "properties" at top level; remove JSON Schema extras
            schema.pop("additionalProperties", None)
            func_decls.append(
                genai_types.FunctionDeclaration(
                    name=td["name"],
                    description=td["description"],
                    parameters=schema,
                )
            )
        google_tools.append(genai_types.Tool(function_declarations=func_decls))

    # Add Google Search grounding (native server tool — Gemini executes searches).
    # Older SDK versions may not expose GoogleSearch; fall back gracefully.
    try:
        google_search_tool = genai_types.Tool(
            google_search=genai_types.GoogleSearch(),
        )
        google_tools.append(google_search_tool)
    except (AttributeError, TypeError):
        pass  # SDK too old for google_search grounding — skip

    client = genai.Client()
    contents: list[Any] = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=user_msg)],
        )
    ]
    # For transcript saving, keep a parallel list of dicts
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_msg}]

    # Gemini 2.5 Flash pricing: $0.15/$0.60 per MTok
    GOOGLE_INPUT_RATE = 0.15 / 1_000_000
    GOOGLE_OUTPUT_RATE = 0.60 / 1_000_000
    total_cost = 0.0
    start = time.monotonic()
    effective_model = model or "gemini-2.5-flash"

    try:
        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            tools=google_tools or None,
        )
        for _turn in range(max_turns):
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                return _make_tool_result(
                    case=case,
                    tool="agent-gemini",
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
                    tool="agent-gemini",
                    context_level=context_level,
                    start=start,
                    messages=messages,
                    error=f"Cost ceiling exceeded: ${total_cost:.2f}",
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )

            for _attempt in range(3):
                try:
                    response = client.models.generate_content(
                        model=effective_model,
                        contents=contents,
                        config=config,
                    )
                    break
                except Exception as _rate_exc:
                    _msg = str(_rate_exc).lower()
                    if "429" in _msg or "rate" in _msg or "resource exhausted" in _msg:
                        wait = 2**_attempt * 5  # 5s, 10s, 20s
                        log.warning(
                            "Rate limited, retrying in %ds (attempt %d/3)",
                            wait,
                            _attempt + 1,
                        )
                        _time.sleep(wait)
                    else:
                        raise
            else:
                return _make_tool_result(
                    case=case,
                    tool="agent-gemini",
                    context_level=context_level,
                    start=start,
                    messages=messages,
                    error="Rate limited after 3 retries",
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )

            # Estimate cost from usage metadata
            usage = getattr(response, "usage_metadata", None)
            if usage:
                inp = getattr(usage, "prompt_token_count", 0) or 0
                out = getattr(usage, "candidates_token_count", 0) or 0
                total_cost += round(inp * GOOGLE_INPUT_RATE + out * GOOGLE_OUTPUT_RATE, 6)

            # Check for function calls in response
            candidate = response.candidates[0]  # type: ignore[index]
            content = candidate.content  # type: ignore[union-attr]
            parts: list[Any] = content.parts or []  # type: ignore[union-attr]
            func_calls = [p for p in parts if getattr(p, "function_call", None)]

            if func_calls:
                # Record assistant message in transcript
                msg_entry: dict[str, Any] = {"role": "assistant", "content": []}
                for p in parts:
                    fc = getattr(p, "function_call", None)
                    if fc is not None:
                        msg_entry["content"].append(
                            {
                                "type": "tool_use",
                                "name": fc.name,  # type: ignore[union-attr]
                                "input": dict(fc.args) if fc.args else {},  # type: ignore[union-attr]
                            }
                        )
                    elif getattr(p, "text", None):
                        msg_entry["content"].append({"type": "text", "text": p.text})
                messages.append(msg_entry)

                # Add assistant turn to contents
                contents.append(content)

                # Execute tools and build function responses
                func_response_parts: list[Any] = []
                for p in func_calls:
                    fc = p.function_call  # type: ignore[union-attr]
                    fc_name: str = fc.name  # type: ignore[union-attr]
                    fc_args: dict[str, Any] = dict(fc.args) if fc.args else {}  # type: ignore[union-attr]
                    if effective_repo is None:
                        result_text = "Error: no repo available"
                    else:
                        result_text = _execute_tool(
                            fc_name,
                            fc_args,
                            effective_repo,
                        )
                    func_response_parts.append(
                        genai_types.Part.from_function_response(
                            name=fc_name,
                            response={"result": result_text},
                        )
                    )
                contents.append(
                    genai_types.Content(
                        role="user",
                        parts=func_response_parts,
                    )
                )
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "name": getattr(p.function_call, "name", "")}
                            for p in func_calls
                        ],
                    }
                )
            else:
                # Final text response
                final_text = ""
                msg_entry_final: dict[str, Any] = {
                    "role": "assistant",
                    "content": [],
                }
                for p in parts:
                    text_val = getattr(p, "text", None)
                    if text_val:
                        final_text += str(text_val)
                        msg_entry_final["content"].append({"type": "text", "text": str(text_val)})
                messages.append(msg_entry_final)
                comments = parse_agent_findings(final_text)
                return _make_tool_result(
                    case=case,
                    tool="agent-gemini",
                    context_level=context_level,
                    start=start,
                    messages=messages,
                    comments=comments,
                    cost_usd=total_cost,
                    transcript_dir=transcript_dir,
                )

        return _make_tool_result(
            case=case,
            tool="agent-gemini",
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
            tool="agent-gemini",
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
