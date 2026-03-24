"""Two-pass and three-phase agent evaluation runners."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from bugeval.agent_runner import (
    MODEL,
    _scrub_fix_references,
    annotate_diff,
    materialize_workspace,
    parse_agent_findings,
    sanitize_diff,
)
from bugeval.models import TestCase
from bugeval.result_models import ToolResult

log = logging.getLogger(__name__)

_V3_SYSTEM = """\
You are an expert Rust code reviewer specializing in compiler internals, \
CLI tools, and language tooling. You are reviewing a pull request for the \
Leo compiler (a language for zero-knowledge proofs on Aleo).

Your workspace contains:
- `diff.patch` — the unified diff (may have formatting annotations)
- `.pr/description.md` — PR title and description
- `.pr/commits.txt` — commit messages
- `.pr/domain.md` — Leo language rules and common bug patterns

You have access to the full repository via Read, Glob, and Grep tools.

IMPORTANT: Do NOT search for this repository on the web. Do NOT visit \
github.com URLs. Web search is only for language/API reference docs.
"""

_V3_PHASE1_SURVEY = """\
## Phase 1: Survey

Read `diff.patch` and `.pr/domain.md` first.

Then for EVERY function, struct, enum, or type modified in the diff:
1. Read the FULL function body (not just the diff hunk)
2. Use the Grep tool to find callers (search for the function name in .rs files)
3. Note the error handling pattern (Result/Option/panic)

Output a SURVEY TABLE with one row per modified symbol:

| Symbol | File:Line | What Changed | Callers | Risk |
|--------|-----------|--------------|---------|------|

Mark Risk as HIGH (signature/error/scope changed), MEDIUM (logic changed), \
or LOW (comment/formatting only).

Include ALL modified symbols. Do not skip any.
After the table, list any [SCOPE CHANGE] annotations from the diff.
"""

_V3_PHASE2_INVESTIGATE = """\
## Phase 2: Investigate

For each HIGH and MEDIUM risk symbol in your survey, answer ALL of these:

1. **Caller impact**: Read each caller. Will they break?
2. **Missing error paths**: What inputs cause a panic? Any unwrap() that \
could fail? Missing match arms?
3. **Spec compliance**: Check `.pr/domain.md` — does this violate any \
Leo language rules?
4. **Scope/nesting**: Did any statement change its enclosing scope?

For each issue found, note the EXACT file:line and explain why it's wrong.
"""

_V3_PHASE3_REPORT = """\
## Phase 3: Report

Output findings as a JSON array. Each finding:
{"file": "path", "line": N, "description": "what and why", \
"suggested_fix": "how to fix"}

Include confirmed bugs, suspicious patterns, and missing error handling.
Do NOT include style nits or formatting preferences.
If no issues: return []
Output the JSON array as your final message.
"""

_EXPLORER_PROMPT = """\
You are a code exploration assistant. A reviewer will use your notes to find bugs.

The workspace has a Rust repo with a PR already applied. Your job:
1. Read `diff.patch` — list every modified function, type, and module
2. For each modified public function, use `rg` to find ALL callers
3. Read the full body of each modified function (not just the diff hunk)
4. Note any type changes, error handling changes, or behavioral changes
5. Check if callers handle new return types / error cases correctly

IMPORTANT: You have limited turns. Output your context document AS SOON AS \
you have enough information. Do NOT keep exploring until you run out of turns. \
After 15 turns of exploration, STOP and output what you have.

Output a structured CONTEXT DOCUMENT:

## Modified Symbols
- fn_name (file:line) — what changed and why it might be wrong

## Callers Found
- fn_name called by: [files:lines with brief context]

## Suspicious Patterns
- [anything that looks wrong: type mismatches, missing error handling, \
  callers that don't handle new behavior]

## Key Code Snippets
- [paste the EXACT lines of code that look suspicious, with file:line]

Be specific. Include file paths and line numbers. The reviewer cannot see \
the repo — they only see your notes and the diff.
Do NOT output bug findings. Only output context and suspicious patterns.
"""

_REVIEWER_PROMPT = """\
You are an expert code reviewer. An exploration assistant has gathered context \
from a Rust repository. Use their notes AND the diff to find bugs.

## Diff
```diff
{diff}
```

## PR Description
{description}

## Explorer's Context Notes
{context}

## Your Task
Review the diff for bugs, security issues, and correctness problems. \
The explorer's notes show you callers, types, and surrounding code — \
use this to find bugs the diff alone wouldn't reveal.

Report findings as a JSON array:
[{{"file": "path", "line": N, "description": "...", "suggested_fix": "..."}}]
If no issues found, return [].
"""


@dataclass
class _PassResult:
    """Result from a single pass (explorer or reviewer)."""

    text: str
    cost: float
    messages: list[dict[str, Any]] = dataclass_field(default_factory=list)


def _claude_parse_output(stdout: str) -> tuple[str, float]:
    from bugeval._cli_runners import _claude_parse_output as _parse

    return _parse(stdout)


def _run_single_pass_cli(
    workspace: Path | None,
    prompt: str,
    max_turns: int,
    model: str,
    timeout: int,
) -> _PassResult:
    """Run one pass via claude CLI subprocess on the host."""
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--dangerously-skip-permissions",
        "--allowedTools",
        "Read,Glob,Grep,WebSearch",
    ]
    if model:
        cmd.extend(["--model", model])

    env = dict(os.environ)
    env["CLAUDECODE"] = ""  # Allow nested session

    cwd = str(workspace.resolve()) if workspace else None

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return _PassResult(text="", cost=0.0)
    except FileNotFoundError:
        return _PassResult(text="(claude CLI not found)", cost=0.0)

    if not result.stdout.strip():
        log.warning("CLI pass empty stdout: %s", result.stderr[:200])
        return _PassResult(text="", cost=0.0)

    text, cost = _claude_parse_output(result.stdout)
    return _PassResult(text=text, cost=cost)


async def _run_single_pass_sdk(
    workspace: Path | None,
    prompt: str,
    max_turns: int,
    model: str,
    timeout: int,
) -> _PassResult:
    """Run one pass via SDK (async — must be called from asyncio context)."""
    try:
        from claude_agent_sdk import (  # type: ignore[import-untyped]
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            query,
        )
    except ImportError:
        return _PassResult(text="(SDK not installed)", cost=0.0)

    effective_model = model or MODEL
    allowed = ["Read", "Glob", "Grep", "WebSearch"]
    disallowed = ["Edit", "Write", "Bash", "NotebookEdit"]

    options = ClaudeAgentOptions(
        model=effective_model,
        allowed_tools=allowed,
        disallowed_tools=disallowed,
        cwd=str(workspace.resolve()) if workspace else None,
        max_turns=max_turns,
        permission_mode="acceptEdits",
        env={"CLAUDECODE": ""},  # Allow nested session
    )

    result_text = ""
    total_cost = 0.0
    transcript: list[dict[str, Any]] = []
    start = time.monotonic()

    async for message in query(prompt=prompt, options=options):
        if time.monotonic() - start > timeout:
            break
        if isinstance(message, AssistantMessage):
            entry: dict[str, Any] = {"role": "assistant", "content": []}
            for block in message.content:
                text_val = getattr(block, "text", None)
                name_val = getattr(block, "name", None)
                if text_val is not None:
                    entry["content"].append(
                        {"type": "text", "text": text_val},
                    )
                elif name_val is not None:
                    entry["content"].append(
                        {
                            "type": "tool_use",
                            "name": name_val,
                            "input": getattr(block, "input", {}),
                        },
                    )
            transcript.append(entry)
        elif isinstance(message, ResultMessage):
            result_text = message.result or ""
            total_cost = message.total_cost_usd or 0.0

    return _PassResult(
        text=result_text,
        cost=total_cost,
        messages=transcript,
    )


def run_agent_sdk_2pass(
    case: TestCase,
    diff: str,
    workspace: Path | None,
    context_level: str,
    timeout: int = 600,
    transcript_dir: Path | None = None,
    model: str = "",
    max_turns: int = 30,
) -> ToolResult:
    """Two-pass review: explorer gathers context, reviewer finds bugs."""
    start = time.monotonic()
    explorer_prompt = _EXPLORER_PROMPT + "\nRead diff.patch and .pr/description.md."

    # Read PR description from workspace (needed for reviewer prompt)
    description = ""
    if workspace:
        desc_path = workspace / ".pr" / "description.md"
        if desc_path.exists():
            description = desc_path.read_text()[:2000]
    sanitized = sanitize_diff(diff)

    # SDK: use ClaudeSDKClient for sequential queries in same session.
    # The reviewer gets the explorer's full conversation history.
    import asyncio

    async def _sdk_two_pass() -> tuple[_PassResult, _PassResult]:
        try:
            from claude_agent_sdk import (  # type: ignore[import-untyped]
                AssistantMessage,
                ClaudeAgentOptions,
                ClaudeSDKClient,
                ResultMessage,
            )
        except ImportError:
            empty = _PassResult(text="(SDK not installed)", cost=0.0)
            return empty, empty

        effective_model = model or MODEL
        allowed = ["Read", "Glob", "Grep", "WebSearch"]
        disallowed = ["Edit", "Write", "Bash", "NotebookEdit"]

        options = ClaudeAgentOptions(
            model=effective_model,
            allowed_tools=allowed,
            disallowed_tools=disallowed,
            cwd=str(workspace.resolve()) if workspace else None,
            max_turns=max_turns,
            permission_mode="acceptEdits",
            env={"CLAUDECODE": ""},
        )

        def _capture_messages(
            messages: list[dict[str, Any]],
            message: object,
        ) -> None:
            if isinstance(message, AssistantMessage):
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": [],
                }
                for block in message.content:
                    text_val = getattr(block, "text", None)
                    name_val = getattr(block, "name", None)
                    if text_val is not None:
                        entry["content"].append(
                            {"type": "text", "text": text_val},
                        )
                    elif name_val is not None:
                        entry["content"].append(
                            {
                                "type": "tool_use",
                                "name": name_val,
                                "input": getattr(
                                    block,
                                    "input",
                                    {},
                                ),
                            },
                        )
                messages.append(entry)

        explorer_msgs: list[dict[str, Any]] = []
        explorer_text = ""
        explorer_cost = 0.0
        reviewer_msgs: list[dict[str, Any]] = []
        reviewer_text = ""
        reviewer_cost = 0.0

        async with ClaudeSDKClient(options=options) as client:
            # Pass 1: Explorer
            await client.query(explorer_prompt)
            async for msg in client.receive_response():
                _capture_messages(explorer_msgs, msg)
                if isinstance(msg, ResultMessage):
                    explorer_text = msg.result or ""
                    explorer_cost = msg.total_cost_usd or 0.0

            ctx = explorer_text or "(Explorer produced no output)"

            # Build reviewer prompt with explorer context
            rev_prompt = _REVIEWER_PROMPT.format(
                diff=sanitized[:15000],
                description=_scrub_fix_references(description),
                context=ctx[:10000],
            )

            # Pass 2: Reviewer (same session, has full context)
            await client.query(rev_prompt)
            async for msg in client.receive_response():
                _capture_messages(reviewer_msgs, msg)
                if isinstance(msg, ResultMessage):
                    reviewer_text = msg.result or ""
                    reviewer_cost = msg.total_cost_usd or 0.0

        return (
            _PassResult(
                text=explorer_text,
                cost=explorer_cost,
                messages=explorer_msgs,
            ),
            _PassResult(
                text=reviewer_text,
                cost=reviewer_cost,
                messages=reviewer_msgs,
            ),
        )

    explorer_result, reviewer_result = asyncio.run(_sdk_two_pass())
    context_text = explorer_result.text or "(Explorer produced no output)"

    elapsed = time.monotonic() - start
    total_cost = explorer_result.cost + reviewer_result.cost
    comments = parse_agent_findings(reviewer_result.text)

    # Save transcript
    tp = ""
    if transcript_dir:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        t_path = transcript_dir / f"{case.id}-2pass.json"
        data = {
            "tool": "agent-sdk-2pass",
            "model": model or MODEL,
            "explorer_prompt": explorer_prompt[:1000],
            "explorer_output": context_text,
            "explorer_messages": explorer_result.messages,
            "reviewer_prompt": _REVIEWER_PROMPT[:500] + "...(truncated)",
            "reviewer_output": reviewer_result.text,
            "reviewer_messages": reviewer_result.messages,
            "cost_explorer": explorer_result.cost,
            "cost_reviewer": reviewer_result.cost,
            "time_total": round(elapsed, 2),
        }
        t_path.write_text(
            json.dumps(data, indent=2, default=str),
        )
        tp = str(t_path)

    return ToolResult(
        case_id=case.id,
        tool="agent-sdk-2pass",
        context_level=context_level,
        comments=comments,
        time_seconds=round(elapsed, 2),
        cost_usd=total_cost,
        transcript_path=tp,
    )


def run_agent_sdk_v3(
    case: TestCase,
    diff: str,
    workspace: Path | None,
    context_level: str,
    timeout: int = 900,
    transcript_dir: Path | None = None,
    model: str = "",
    max_turns: int = 40,
) -> ToolResult:
    """Three-phase review: survey -> investigate -> report."""
    import asyncio

    start = time.monotonic()
    sanitized = sanitize_diff(diff)
    annotated = annotate_diff(sanitized)

    # Workspace setup: evaluate.py already called setup_workspace +
    # materialize_workspace for diff+repo. We just need to:
    # 1. Overwrite diff.patch with the annotated version
    # 2. Add domain.md
    # For diff-only (no workspace from evaluate.py), we create one.
    effective_repo = workspace
    _temp_dirs: list[Path] = []
    if workspace is not None:
        # Workspace already materialized — overwrite diff with annotated version
        diff_path = workspace / "diff.patch"
        if diff_path.exists():
            diff_path.write_text(annotated)
        else:
            # Workspace exists but no diff.patch — full materialization needed
            effective_repo = materialize_workspace(
                case,
                annotated,
                workspace,
                context_level,
            )
    elif context_level == "diff-only":
        tmp_ws = Path(tempfile.mkdtemp(prefix="bugeval-ws-"))
        _temp_dirs.append(tmp_ws)
        effective_repo = materialize_workspace(
            case,
            annotated,
            tmp_ws,
            context_level,
        )
        if effective_repo != tmp_ws:
            _temp_dirs.append(effective_repo)

    # Write domain rules file (Leo compiler-specific; extend for other repos)
    domain_src = Path(__file__).resolve().parent.parent.parent / "config" / "domain" / "compiler.md"
    if effective_repo and domain_src.exists():
        pr_dir = effective_repo / ".pr"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "domain.md").write_text(domain_src.read_text())

    async def _v3_run() -> ToolResult:
        try:
            from claude_agent_sdk import (  # type: ignore[import-untyped]
                AssistantMessage,
                ClaudeAgentOptions,
                ClaudeSDKClient,
                ResultMessage,
            )
        except ImportError:
            return ToolResult(
                case_id=case.id,
                tool="agent-sdk-v3",
                context_level=context_level,
                error="claude-agent-sdk not installed",
            )

        effective_model = model or "claude-opus-4-6"
        allowed = [
            "Read",
            "Glob",
            "Grep",
            "Bash",
            "WebSearch",
        ]
        disallowed = [
            "Edit",
            "Write",
            "NotebookEdit",
        ]

        options = ClaudeAgentOptions(
            system_prompt=_V3_SYSTEM,
            model=effective_model,
            allowed_tools=allowed,
            disallowed_tools=disallowed,
            cwd=(str(effective_repo.resolve()) if effective_repo else None),
            max_turns=max_turns,
            permission_mode="acceptEdits",
            env={"CLAUDECODE": ""},
        )

        all_messages: list[dict[str, Any]] = []
        total_cost = 0.0
        session_id = ""
        phase_texts: dict[str, str] = {}

        def _capture(
            messages: list[dict[str, Any]],
            msg: object,
        ) -> None:
            if isinstance(msg, AssistantMessage):
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": [],
                }
                for block in msg.content:
                    text_val = getattr(block, "text", None)
                    name_val = getattr(block, "name", None)
                    if text_val is not None:
                        entry["content"].append(
                            {
                                "type": "text",
                                "text": text_val,
                            },
                        )
                    elif name_val is not None:
                        entry["content"].append(
                            {
                                "type": "tool_use",
                                "name": name_val,
                                "input": getattr(
                                    block,
                                    "input",
                                    {},
                                ),
                            },
                        )
                messages.append(entry)

        # Reserve 30% of budget for the report phase
        report_deadline = start + timeout * 0.7

        try:
            async with ClaudeSDKClient(
                options=options,
            ) as client:
                # Phase 1: Survey (explore the diff + callers)
                phase1_msgs: list[dict[str, Any]] = []
                await client.query(
                    _V3_PHASE1_SURVEY + "\nStart by reading " + "diff.patch and .pr/domain.md."
                )
                async for msg in client.receive_response():
                    _capture(phase1_msgs, msg)
                    if isinstance(msg, ResultMessage):
                        phase_texts["survey"] = msg.result or ""
                        total_cost = msg.total_cost_usd or 0.0
                        session_id = msg.session_id or ""
                all_messages.extend(phase1_msgs)

                # Phase 2: Investigate (if budget remains)
                if time.monotonic() < report_deadline:
                    phase2_msgs: list[dict[str, Any]] = []
                    await client.query(
                        _V3_PHASE2_INVESTIGATE,
                    )
                    async for msg in client.receive_response():
                        _capture(phase2_msgs, msg)
                        if isinstance(msg, ResultMessage):
                            phase_texts["investigate"] = msg.result or ""
                            total_cost = msg.total_cost_usd or 0.0
                    all_messages.extend(phase2_msgs)
                else:
                    phase_texts["investigate"] = "(skipped: budget exhausted)"

                # Phase 3: Report (always runs)
                phase3_msgs: list[dict[str, Any]] = []
                await client.query(_V3_PHASE3_REPORT)
                async for msg in client.receive_response():
                    _capture(phase3_msgs, msg)
                    if isinstance(msg, ResultMessage):
                        phase_texts["report"] = msg.result or ""
                        total_cost = msg.total_cost_usd or 0.0
                all_messages.extend(phase3_msgs)

        except Exception as exc:
            elapsed = time.monotonic() - start
            return ToolResult(
                case_id=case.id,
                tool="agent-sdk-v3",
                context_level=context_level,
                time_seconds=round(elapsed, 2),
                cost_usd=total_cost,
                error=str(exc),
            )

        elapsed = time.monotonic() - start
        report_text = phase_texts.get("report", "")
        comments = parse_agent_findings(report_text)

        # Save transcript
        transcript_path = ""
        if transcript_dir:
            transcript_dir.mkdir(parents=True, exist_ok=True)
            t_path = transcript_dir / f"{case.id}-v3.json"
            data = {
                "session_id": session_id,
                "model": effective_model,
                "phases": phase_texts,
                "messages": all_messages,
                "cost_usd": total_cost,
                "elapsed_seconds": round(elapsed, 2),
            }
            t_path.write_text(
                json.dumps(data, indent=2, default=str),
            )
            transcript_path = str(t_path)

        return ToolResult(
            case_id=case.id,
            tool="agent-sdk-v3",
            context_level=context_level,
            comments=comments,
            time_seconds=round(elapsed, 2),
            cost_usd=total_cost,
            transcript_path=transcript_path,
        )

    try:
        result = asyncio.run(_v3_run())
    finally:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)

    return result
