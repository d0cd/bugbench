"""Thin runner script executed INSIDE a Docker container.

Reads JSON config from stdin, runs the Agent SDK, writes JSON result to stdout.
This gives us: SDK turn-by-turn transcripts + Docker sandboxed Bash access.

If the agent exhausts its turns without producing JSON findings, a synthesis
prompt is sent to force output (continuation logic).

Usage (from host):
    docker run --rm -i -v src:/app/src -v workspace:/work ... \
        python3 /app/src/bugeval/_docker_runner.py < config.json
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

_SYNTHESIS_PROMPT = (
    "STOP exploring. You have used all your exploration turns.\n\n"
    "Based on everything you have seen so far — the diff, the code you read, "
    "the callers you checked — output your findings NOW.\n\n"
    "Report as a JSON array. Each finding:\n"
    '[{"file": "path", "line": N, "description": "...", "suggested_fix": "..."}]\n\n'
    "If you found no issues, return: []\n\n"
    "Do NOT make any more tool calls. Just output the JSON array immediately."
)


def _has_json_findings(text: str) -> bool:
    """Check if text contains a non-empty JSON array of findings."""
    stripped = text.strip()
    if "[" not in stripped or "]" not in stripped:
        return False
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            # Must be a non-empty list with at least one dict-like finding
            return isinstance(parsed, list) and len(parsed) > 0
        except json.JSONDecodeError:
            return False
    return False


def _capture_message(
    messages: list[dict[str, Any]],
    message: object,
    assistant_cls: type,
) -> None:
    """Extract content blocks from an AssistantMessage into messages list."""
    if isinstance(message, assistant_cls):
        entry: dict[str, Any] = {"role": "assistant", "content": []}
        for block in message.content:
            if hasattr(block, "text"):
                entry["content"].append(
                    {"type": "text", "text": block.text},
                )
            elif hasattr(block, "name"):
                entry["content"].append(
                    {
                        "type": "tool_use",
                        "name": block.name,
                        "input": getattr(block, "input", {}),
                    },
                )
        messages.append(entry)


async def run(config: dict[str, Any]) -> dict[str, Any]:
    """Run SDK query inside Docker and return structured result."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
    )

    # Support single prompt or multi-phase prompts list
    prompts: list[str] = config.get("prompts", [])
    if not prompts:
        prompts = [config["prompt"]]
    model = config.get("model", "claude-sonnet-4-6")
    max_turns = config.get("max_turns", 30)
    timeout = config.get("timeout", 600)
    cwd = config.get("cwd", "/work")
    allowed_tools = config.get(
        "allowed_tools",
        [
            "Read",
            "Glob",
            "Grep",
            "Bash",
            "WebSearch",
        ],
    )
    disallowed_tools = config.get(
        "disallowed_tools",
        [
            "Edit",
            "Write",
            "NotebookEdit",
        ],
    )

    options = ClaudeAgentOptions(
        model=model,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        cwd=cwd,
        max_turns=max_turns,
        permission_mode="acceptEdits",
    )

    result_text = ""
    total_cost = 0.0
    session_id = ""
    messages: list[dict[str, Any]] = []
    continued = False
    start = time.monotonic()

    # Reserve 90s for synthesis — primary query gets the rest
    synthesis_budget = 90
    primary_deadline = timeout - synthesis_budget

    phase_results: list[str] = []

    async with ClaudeSDKClient(options=options) as client:
        # Run each prompt sequentially in the same session
        for i, prompt in enumerate(prompts):
            if time.monotonic() - start > primary_deadline:
                break
            await client.query(prompt)
            async for message in client.receive_response():
                if time.monotonic() - start > primary_deadline:
                    break
                _capture_message(messages, message, AssistantMessage)
                if isinstance(message, ResultMessage):
                    result_text = message.result or ""
                    total_cost = message.total_cost_usd or 0.0
                    session_id = message.session_id or ""
            phase_results.append(result_text)

        # Continuation: if no JSON findings produced, ALWAYS force synthesis
        # Even if over budget — this is a final cleanup step, not exploration
        if not _has_json_findings(result_text):
            continued = True
            synthesis_deadline = time.monotonic() + 120  # 2 min max
            await client.query(_SYNTHESIS_PROMPT)
            async for message in client.receive_response():
                if time.monotonic() > synthesis_deadline:
                    break
                _capture_message(messages, message, AssistantMessage)
                if isinstance(message, ResultMessage):
                    result_text = message.result or ""
                    total_cost = message.total_cost_usd or 0.0
                    session_id = message.session_id or ""

    elapsed = time.monotonic() - start
    return {
        "result_text": result_text,
        "cost_usd": total_cost,
        "session_id": session_id,
        "messages": messages,
        "elapsed_seconds": round(elapsed, 2),
        "num_turns": len(messages),
        "continued": continued,
        "phase_results": phase_results,
    }


def main() -> None:
    config = json.loads(sys.stdin.read())
    try:
        result = asyncio.run(run(config))
        json.dump(result, sys.stdout, default=str)
    except Exception as exc:
        json.dump(
            {
                "error": str(exc),
                "result_text": "",
                "cost_usd": 0.0,
                "messages": [],
                "elapsed_seconds": 0,
                "num_turns": 0,
            },
            sys.stdout,
        )


if __name__ == "__main__":
    main()
