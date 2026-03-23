"""Custom Claude agent evaluation runner."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

import anthropic

from bugeval.models import TestCase
from bugeval.result_models import Comment, ToolResult

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
COST_CEILING_USD = 2.0
API_TIMEOUT_SECONDS = 120.0

# File-system tools available to all API runners (Anthropic, Gemini, OpenAI).
# Web search is handled per-provider via native server tools (not in this list).
FILE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path",
                    "default": ".",
                },
            },
        },
    },
    {
        "name": "search_text",
        "description": "Search for a regex pattern across files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {
                    "type": "string",
                    "description": "Directory to search in",
                    "default": ".",
                },
            },
            "required": ["pattern"],
        },
    },
]

# Anthropic server-side web search tool (executed by Anthropic, not by us).
ANTHROPIC_WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

# Backward compat alias
TOOL_DEFS = FILE_TOOLS


_SYSTEM_BASE = """\
You are an expert code reviewer performing a thorough review of a pull request.

Your workspace contains:
- `diff.patch` — the unified diff of the changes under review
- `.pr/description.md` — the PR title and description
- `.pr/commits.txt` — commit messages

Review the changes for bugs, security vulnerabilities, correctness issues, \
logic errors, and edge cases. Focus on the CHANGED code in the diff — look \
at both what was added and what was removed.

"""

_SYSTEM_REPO_V2 = """\
You have access to the full repository. Follow this review methodology:

## Review Steps (follow in order)

1. **Read the diff first.** Understand every hunk. Note which functions, \
structs, and types are modified.

2. **For each modified function:** Read the FULL function (not just the diff \
hunk) to understand pre/post conditions, error handling, and return types.

3. **Check callers.** Grep for call sites of modified functions. Will callers \
break with the new signature, return type, or behavior?

4. **Check type consistency.** If a type, struct field, or enum variant \
changed, grep for all usages. Are all sites updated?

5. **Check error paths.** Does the new code handle errors (Result/Option) \
consistently with surrounding code? Does it introduce panics where errors \
were previously recoverable?

6. **Check edge cases.** Empty inputs, zero values, integer overflow, \
off-by-one in loops, null/None fields.

Start with the diff, then use these steps to guide your exploration. \
Prioritize checking callers and type consistency — these are the most \
common sources of bugs that the diff alone won't reveal.

"""

_SYSTEM_DOMAIN = """\
Domain context is available in `.pr/domain.md`. This is a zero-knowledge \
cryptography / blockchain project.

"""

_SYSTEM_SEARCH = """\
You have access to web search for looking up documentation, API references, \
known CVEs, and language/library semantics. Use it when you need to verify \
behavior or check for known issues.

IMPORTANT: Do NOT search for the specific repository, PR, commit, or issue \
being reviewed. Do NOT visit github.com URLs related to this project. Web \
search is for reference material only.

"""

_SYSTEM_OUTPUT = """\
After your review, report your findings as a JSON array. Each finding:
- "file": the file path
- "line": the line number where the issue is
- "description": what the problem is and why it matters
- "suggested_fix": how to fix it (if you know)

If you find no issues, return an empty array: []
Output the JSON array as your final message."""

_SYNTHESIS_PROMPT = """\
STOP exploring. You have used all your exploration turns.

Based on everything you have seen so far — the diff, the code you read, \
the callers you checked — output your findings NOW.

Report as a JSON array. Each finding:
[{"file": "path", "line": N, "description": "...", "suggested_fix": "..."}]

If you found no issues, return: []

Do NOT make any more tool calls. Just output the JSON array immediately."""


def _has_json_findings(text: str) -> bool:
    """Check if text contains a JSON array (findings output)."""
    stripped = text.strip()
    # Quick check: must contain [ and ]
    if "[" not in stripped or "]" not in stripped:
        return False
    # Try to find a valid JSON array
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            return isinstance(parsed, list)
        except json.JSONDecodeError:
            return False
    return False


_SYSTEM_DOCKER_V3 = """\
You are an expert code reviewer with a full Rust development environment.

You are reviewing a pull request that has ALREADY been merged. The diff shows \
changes that may have introduced bugs. Your job is to find those bugs.

Your workspace contains:
- `diff.patch` — the unified diff of the changes under review
- `.pr/description.md` — the PR title and description
- `.pr/commits.txt` — commit messages
- A full Rust repository with the PR changes already applied

## Your Tools (use the right tool for the job)

**Bash** (most powerful — start here):
- `cat diff.patch` — read the full diff
- `rg "function_name" -l` — find files containing a symbol (100x faster than Grep)
- `rg "function_name" -C 5` — show context around matches
- `rg "fn modified_fn" --type rust -A 20` — read full function signature + body
- `wc -l diff.patch` — check diff size before reading
- `head -200 diff.patch` — read first part of large diffs

Note: `cargo check` and `cargo clippy` are available but take several minutes \
on this project. Only use them if you have specific questions the compiler can \
answer (e.g., "does this type implement Trait?"). Prefer `rg` for exploration.

**Read** — read file contents (use when you know the exact file and line range)
**Grep** — search file contents (for simple patterns; `rg` via Bash is faster)
**Glob** — find files by name pattern
**WebSearch** — look up Rust stdlib docs, crate APIs, known CVEs

## Review Workflow

1. **Read the diff** — `cat diff.patch` or Read `diff.patch`. Note which \
   functions, types, and modules are modified.
2. **Identify modified symbols** — Note which functions, types, structs \
   changed. Use `rg "fn function_name" --type rust -A 20` to read their \
   full signatures and bodies.
3. **Check callers** — `rg "function_name" -l --type rust` then read call \
   sites. Will callers break with the new behavior/signature?
4. **Check type consistency** — If types changed, `rg "TypeName" -l` to \
   find all usages. Are all sites updated?
5. **Review each diff hunk** — Logic errors, edge cases, missing error \
   handling, off-by-one, null/None, integer overflow.
6. **Report findings** — Output a JSON array with file, line, description.

## Key Principles
- **`rg` is your best tool.** Fast symbol search replaces slow file browsing.
- **Be targeted.** Check callers of modified functions, not callers of callers.
- **Output findings even if uncertain.** A possible bug is better than silence.
- **Don't re-read files.** Read once, take notes, move on.
- **ALWAYS output findings.** Even partial analysis is valuable. Never end \
  with an empty array unless you genuinely found zero issues.

"""


def build_system_prompt(
    context_level: str,
    bash_enabled: bool = False,
) -> str:
    """Build system prompt based on context level and available tools.

    When bash_enabled is True (e.g. running inside Docker with full toolchain),
    uses the enhanced prompt with Bash/rg/cargo guidance.
    """
    if bash_enabled:
        # Full toolchain prompt: Bash, rg, cargo available
        parts = [_SYSTEM_DOCKER_V3, _SYSTEM_SEARCH, _SYSTEM_OUTPUT]
        return "".join(parts)

    parts = [_SYSTEM_BASE]
    if context_level in ("diff+repo", "diff+repo+domain"):
        parts.append(_SYSTEM_REPO_V2)
    if context_level == "diff+repo+domain":
        parts.append(_SYSTEM_DOMAIN)
    parts.append(_SYSTEM_SEARCH)
    parts.append(_SYSTEM_OUTPUT)
    return "".join(parts)


def _scrub_fix_references(text: str) -> str:
    """Remove lines that leak fix/bug context from PR body."""
    fix_pattern = re.compile(
        r"(^.*\b(fix(es|ed|ing)?|bug|patch|hotfix|resolv(es|ed|ing)?)\b.*$)"
        r"|(^.*#\d+.*$)",
        re.IGNORECASE | re.MULTILINE,
    )
    return fix_pattern.sub("", text).strip()


def build_user_prompt(
    case: TestCase,
    diff: str,
    context_level: str,
    *,
    inline_diff: bool = False,
) -> str:
    """Build user message directing the agent to review workspace files.

    When inline_diff is True (diff-only API runners that lack file tools),
    the diff content is appended directly so the model can still see it.
    Otherwise the agent is expected to read diff.patch from the workspace.
    """
    parts: list[str] = [
        "Please review the pull request in your workspace.",
        "Start by reading `diff.patch` and `.pr/description.md`.",
    ]
    if context_level in ("diff+repo", "diff+repo+domain"):
        parts.append("Use the repository tools to explore surrounding code for context.")
    if context_level == "diff+repo+domain":
        parts.append("Check `.pr/domain.md` for domain-specific guidance.")
    parts.append("Report all bugs, security issues, and correctness problems you find.")
    if inline_diff:
        parts.append(f"\n```diff\n{diff}\n```")
    return "\n".join(parts)


def sanitize_diff(diff: str) -> str:
    """Strip identifying metadata from diff for anti-contamination."""
    sha_pattern = re.compile(r"\b[0-9a-f]{7,40}\b")
    lines = diff.splitlines()
    cleaned: list[str] = []
    for line in lines:
        # Strip index lines (contain blob SHAs)
        if line.startswith("index "):
            continue
        # Strip author/date from git log-style headers
        if line.startswith("Author:") or line.startswith("Date:"):
            continue
        # Strip From: headers (patch email format)
        if line.startswith("From:"):
            continue
        # Strip lines that are purely commit SHAs (e.g. "From <sha>")
        if line.startswith("From ") and sha_pattern.search(line):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def annotate_diff(diff: str) -> str:
    """Strip formatting-only hunks and annotate scope changes."""
    if not diff.strip():
        return ""

    result_lines: list[str] = []
    current_hunk_lines: list[str] = []
    file_header: list[str] = []
    file_header_written = False
    stripped_count = 0
    total_hunks = 0

    def _is_ws_only(removed: list[str], added: list[str]) -> bool:
        """Check if changes are whitespace-only (same tokens)."""
        if len(removed) != len(added):
            return False
        for r, a in zip(removed, added):
            if r.split() != a.split():
                return False
        return True

    def _flush_hunk() -> None:
        nonlocal stripped_count, total_hunks, file_header_written
        if not current_hunk_lines:
            return
        total_hunks += 1

        removed = [ln[1:] for ln in current_hunk_lines if ln.startswith("-")]
        added = [ln[1:] for ln in current_hunk_lines if ln.startswith("+")]

        if removed and added and _is_ws_only(removed, added):
            stripped_count += 1
            # Check for scope changes (indent level shift >= 4 spaces)
            for r, a in zip(removed, added):
                r_indent = len(r) - len(r.lstrip())
                a_indent = len(a) - len(a.lstrip())
                if abs(r_indent - a_indent) >= 4 and r.strip():
                    result_lines.append(
                        f"# [SCOPE CHANGE] indent {r_indent}->{a_indent}: {r.strip()}\n"
                    )
            return

        # Real change — write file header if not yet written
        if not file_header_written and file_header:
            result_lines.extend(file_header)
            file_header_written = True
        result_lines.extend(current_hunk_lines)

    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git"):
            _flush_hunk()
            current_hunk_lines = []
            file_header = [line]
            file_header_written = False
        elif line.startswith("---") or line.startswith("+++"):
            file_header.append(line)
        elif line.startswith("@@"):
            _flush_hunk()
            current_hunk_lines = []
            current_hunk_lines.append(line)
        elif current_hunk_lines or line.startswith(("+", "-", " ")):
            current_hunk_lines.append(line)

    _flush_hunk()

    result = "".join(result_lines)

    # Add summary annotation at the top
    if stripped_count > 0:
        if stripped_count == total_hunks:
            header = (
                "# [WARNING: ALL hunks appear formatting-only. "
                "Formatting tools can accidentally change logic "
                "by moving statements between scope levels. "
                "Check SCOPE CHANGE annotations below.]\n\n"
            )
        else:
            header = (
                f"# [FORMATTING: {stripped_count}/{total_hunks} "
                f"hunks were whitespace-only and stripped. "
                f"Check for scope changes in stripped "
                f"sections.]\n\n"
            )
        result = header + result

    return result


_DOMAIN_HINTS = (
    "This is a zero-knowledge cryptography / blockchain project written "
    "primarily in Rust. Pay special attention to:\n"
    "- Cryptographic correctness (field arithmetic, curve ops)\n"
    "- Consensus safety (state transitions, finality)\n"
    "- Serialization round-trip fidelity\n"
    "- Resource exhaustion / DoS vectors\n"
    "- Unsafe blocks and FFI boundaries\n"
)


def materialize_workspace(
    case: TestCase,
    diff: str,
    workspace: Path,
    context_level: str,
) -> Path:
    """Write PR context and diff as files in the workspace.

    Creates:
      workspace/.pr/description.md  -- scrubbed PR title + body
      workspace/.pr/commits.txt     -- scrubbed commit messages (one per line)
      workspace/diff.patch          -- sanitized unified diff
      workspace/.pr/domain.md       -- domain hints (diff+repo+domain only)

    For diff-only: creates a temp directory with just these files.
    For diff+repo / diff+repo+domain: writes into the existing repo clone.
    """
    if context_level == "diff-only":
        workspace = Path(
            tempfile.mkdtemp(
                prefix="bugeval-ws-",
                dir=workspace.parent,
            )
        )

    pr_dir = workspace / ".pr"
    pr_dir.mkdir(parents=True, exist_ok=True)

    # description.md
    desc_parts: list[str] = []
    if case.introducing_pr_title:
        scrubbed_title = _scrub_fix_references(case.introducing_pr_title)
        if scrubbed_title:
            desc_parts.append(f"# {scrubbed_title}")
    if case.introducing_pr_body:
        scrubbed_body = _scrub_fix_references(case.introducing_pr_body)
        if scrubbed_body:
            desc_parts.append(scrubbed_body)
    (pr_dir / "description.md").write_text(
        "\n\n".join(desc_parts) if desc_parts else "(no description)",
    )

    # commits.txt
    commit_lines: list[str] = []
    if case.introducing_pr_commit_messages:
        for msg in case.introducing_pr_commit_messages:
            scrubbed = _scrub_fix_references(msg)
            if scrubbed.strip():
                commit_lines.append(scrubbed.strip())
    (pr_dir / "commits.txt").write_text(
        "\n".join(commit_lines) if commit_lines else "(no commits)",
    )

    # diff.patch
    (workspace / "diff.patch").write_text(diff)

    # domain.md (only for diff+repo+domain)
    if context_level == "diff+repo+domain":
        (pr_dir / "domain.md").write_text(_DOMAIN_HINTS)

    return workspace


def prepare_workspace(
    case: TestCase,
    diff: str,
    repo_dir: Path | None,
    context_level: str,
    work_dir: Path | None = None,
) -> tuple[Path | None, list[Path]]:
    """Prepare workspace for a runner. Returns (workspace_path, temp_dirs).

    Caller must clean up temp_dirs after the runner completes.
    """
    sanitized = sanitize_diff(diff)
    temp_dirs: list[Path] = []

    if repo_dir is not None:
        workspace = materialize_workspace(
            case,
            sanitized,
            repo_dir,
            context_level,
        )
        return workspace, temp_dirs

    if context_level == "diff-only":
        tmp_ws = Path(tempfile.mkdtemp(prefix="bugeval-ws-"))
        temp_dirs.append(tmp_ws)
        workspace = materialize_workspace(
            case,
            sanitized,
            tmp_ws,
            context_level,
        )
        if workspace != tmp_ws:
            temp_dirs.append(workspace)
        return workspace, temp_dirs

    return None, temp_dirs


def parse_agent_findings(response: str) -> list[Comment]:
    """Parse agent's response to extract findings as Comments."""
    # Try to extract JSON array from the response
    text = response.strip()
    # Find the best JSON array match — try longest first, then shorter on failure
    raw = None
    arr_start = text.find("[")
    if arr_start >= 0:
        pos = len(text)
        while pos > arr_start:
            pos = text.rfind("]", arr_start, pos)
            if pos < 0:
                break
            candidate = text[arr_start : pos + 1]
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    raw = parsed
                    break
            except json.JSONDecodeError:
                pass  # try shorter match
    if raw is None:
        return []
    findings = raw
    comments: list[Comment] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        comments.append(
            Comment(
                file=str(f.get("file", "")),
                line=int(f.get("line", 0)),
                body=str(f.get("description", "")),
                suggested_fix=str(f.get("suggested_fix", "")),
            )
        )
    return comments


_BLOCKED_DIRS = {".git", ".hg", ".svn"}


def _check_path_traversal(target: Path, resolved_repo: Path) -> str | None:
    """Return error string if target escapes repo or accesses .git/, else None."""
    try:
        rel = target.relative_to(resolved_repo)
    except ValueError:
        return "Error: path outside workspace"
    # Block access to VCS internals (prevents reading git history/logs)
    if any(part in _BLOCKED_DIRS for part in rel.parts):
        return "Error: access to version control directories is not allowed"
    return None


def _execute_tool(name: str, tool_input: dict[str, Any], repo_dir: Path) -> str:
    resolved_repo = repo_dir.resolve()
    if name == "read_file":
        target = (repo_dir / tool_input["path"]).resolve()
        if err := _check_path_traversal(target, resolved_repo):
            return err
        if not target.is_file():
            return f"Error: file not found: {tool_input['path']}"
        try:
            return target.read_text(errors="replace")[:50_000]
        except OSError as e:
            return f"Error reading file: {e}"
    elif name == "list_directory":
        path_str = tool_input.get("path", ".")
        target = (repo_dir / path_str).resolve()
        if err := _check_path_traversal(target, resolved_repo):
            return err
        if not target.is_dir():
            return f"Error: directory not found: {path_str}"
        try:
            entries = sorted(p.name for p in target.iterdir())
            return "\n".join(entries[:200])
        except OSError as e:
            return f"Error listing directory: {e}"
    elif name == "search_text":
        pattern = tool_input["pattern"]
        path_str = tool_input.get("path", ".")
        target = (repo_dir / path_str).resolve()
        if err := _check_path_traversal(target, resolved_repo):
            return err
        try:
            result = subprocess.run(
                [
                    "grep",
                    "-rn",
                    "--include=*.rs",
                    "--include=*.toml",
                    "--include=*.py",
                    "--include=*.ts",
                    "--include=*.go",
                    "--include=*.java",
                    "-m",
                    "50",
                    pattern,
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout[:20_000] or "No matches found."
        except (subprocess.TimeoutExpired, OSError):
            return "Error: search timed out or failed"
    return f"Error: unknown tool {name}"


def _get_file_tools_for_context(context_level: str) -> list[dict[str, Any]]:
    """Return file-system tools for the given context level."""
    if context_level in ("diff+repo", "diff+repo+domain"):
        return FILE_TOOLS
    return []


# Backward compat alias used by tests
_get_tools_for_context = _get_file_tools_for_context


def _calc_cost(usage: Any) -> float:
    # Claude Sonnet 4.6 pricing: $3/$15 per MTok (input/output)
    # Thinking tokens are billed as output tokens at the same rate.
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    # cache_creation_input_tokens and cache_read_input_tokens are ignored for now
    return round(inp * 3.0 / 1_000_000 + out * 15.0 / 1_000_000, 6)


def setup_workspace(
    case: TestCase,
    repo_source: str | Path,
    context_level: str,
    work_dir: Path,
) -> Path | None:
    """Clone repo at base_commit if context requires it, else return None.

    *repo_source* can be a local path (fast, uses git clone --local) or
    a URL (slow network clone). Prefer passing repo_dir for speed.
    """
    if context_level == "diff-only":
        return None
    from bugeval.git_utils import clone_at_sha

    dest = work_dir / case.id
    source = str(repo_source)
    return clone_at_sha(source, dest, case.base_commit)


def _save_transcript(messages: list[dict[str, Any]], transcript_dir: Path, case_id: str) -> str:
    """Serialize messages to JSON and return the file path."""
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"{case_id}.json"
    # Convert non-serializable content blocks to dicts
    serializable: list[dict[str, Any]] = []
    for msg in messages:
        entry: dict[str, Any] = {"role": msg.get("role", "")}
        content = msg.get("content")
        if isinstance(content, str):
            entry["content"] = content
        elif isinstance(content, list):
            entry_content: list[Any] = []
            for item in content:
                if isinstance(item, dict):
                    entry_content.append(item)
                elif hasattr(item, "type") and item.type == "thinking":
                    entry_content.append({"type": "thinking", "thinking": item.thinking})
                elif hasattr(item, "type") and item.type == "text":
                    entry_content.append({"type": "text", "text": item.text})
                elif hasattr(item, "type") and item.type == "tool_use":
                    entry_content.append(
                        {
                            "type": "tool_use",
                            "name": item.name,
                            "input": item.input,
                            "id": item.id,
                        }
                    )
                else:
                    entry_content.append(str(item))
            entry["content"] = entry_content
        else:
            entry["content"] = str(content)
        serializable.append(entry)
    path.write_text(json.dumps(serializable, indent=2, default=str))
    return str(path)


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

    def _make_result(
        comments: list[Comment] | None = None,
        error: str = "",
    ) -> ToolResult:
        elapsed = time.monotonic() - start
        transcript_path = ""
        if transcript_dir is not None:
            transcript_path = _save_transcript(messages, transcript_dir, case.id)
        return ToolResult(
            case_id=case.id,
            tool="agent",
            context_level=context_level,
            comments=comments or [],
            time_seconds=round(elapsed, 2),
            cost_usd=total_cost,
            error=error,
            transcript_path=transcript_path,
        )

    try:
        for _turn in range(max_turns):
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                return _make_result(error="Agent timeout exceeded")
            if total_cost > COST_CEILING_USD:
                return _make_result(
                    error=f"Cost ceiling exceeded: ${total_cost:.2f} > ${COST_CEILING_USD}"
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
            response = client.messages.create(**kwargs)  # type: ignore[arg-type]
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
                return _make_result(comments=comments)
        # Exhausted turns
        return _make_result(error=f"Exhausted {max_turns} turns without final response")
    except Exception as exc:
        elapsed = time.monotonic() - start
        transcript_path = ""
        if transcript_dir is not None:
            transcript_path = _save_transcript(messages, transcript_dir, case.id)
        return ToolResult(
            case_id=case.id,
            tool="agent",
            context_level=context_level,
            time_seconds=round(elapsed, 2),
            cost_usd=total_cost,
            error=str(exc),
            transcript_path=transcript_path,
        )
    finally:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)


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

    def _make_result(
        comments: list[Comment] | None = None,
        error: str = "",
    ) -> ToolResult:
        elapsed = time.monotonic() - start
        transcript_path = ""
        if transcript_dir is not None:
            transcript_path = _save_transcript(messages, transcript_dir, case.id)
        return ToolResult(
            case_id=case.id,
            tool="agent-gemini",
            context_level=context_level,
            comments=comments or [],
            time_seconds=round(elapsed, 2),
            cost_usd=total_cost,
            error=error,
            transcript_path=transcript_path,
        )

    try:
        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            tools=google_tools or None,
        )
        for _turn in range(max_turns):
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                return _make_result(error="Agent timeout exceeded")
            if total_cost > COST_CEILING_USD:
                return _make_result(error=f"Cost ceiling exceeded: ${total_cost:.2f}")

            response = client.models.generate_content(
                model=effective_model,
                contents=contents,
                config=config,
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
                return _make_result(comments=comments)

        return _make_result(error=f"Exhausted {max_turns} turns without final response")
    except Exception as exc:
        return ToolResult(
            case_id=case.id,
            tool="agent-gemini",
            context_level=context_level,
            time_seconds=round(time.monotonic() - start, 2),
            cost_usd=total_cost,
            error=str(exc),
            transcript_path=(
                _save_transcript(messages, transcript_dir, case.id) if transcript_dir else ""
            ),
        )
    finally:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)


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

    def _make_result(
        comments: list[Comment] | None = None,
        error: str = "",
    ) -> ToolResult:
        elapsed = time.monotonic() - start
        transcript_path = ""
        if transcript_dir is not None:
            transcript_path = _save_transcript(transcript, transcript_dir, case.id)
        return ToolResult(
            case_id=case.id,
            tool="agent-openai",
            context_level=context_level,
            comments=comments or [],
            time_seconds=round(elapsed, 2),
            cost_usd=total_cost,
            error=error,
            transcript_path=transcript_path,
        )

    try:
        for _turn in range(max_turns):
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                return _make_result(error="Agent timeout exceeded")
            if total_cost > COST_CEILING_USD:
                return _make_result(error=f"Cost ceiling exceeded: ${total_cost:.2f}")

            kwargs: dict[str, Any] = {
                "model": effective_model,
                "messages": messages,
                "tools": openai_tools,
            }

            response = client.chat.completions.create(**kwargs)

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
                return _make_result(comments=comments)

        return _make_result(error=f"Exhausted {max_turns} turns without final response")
    except Exception as exc:
        return ToolResult(
            case_id=case.id,
            tool="agent-openai",
            context_level=context_level,
            time_seconds=round(time.monotonic() - start, 2),
            cost_usd=total_cost,
            error=str(exc),
            transcript_path=(
                _save_transcript(transcript, transcript_dir, case.id) if transcript_dir else ""
            ),
        )
    finally:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)


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
        from claude_agent_sdk.types import (
            ThinkingBlock as _SdkThinkingBlock,
        )
        from claude_agent_sdk.types import (
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
            for block in message.content:
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


# ---------------------------------------------------------------------------
# Two-pass runner: explorer + reviewer
# ---------------------------------------------------------------------------


@dataclass
class _PassResult:
    """Result from a single pass (explorer or reviewer)."""

    text: str
    cost: float
    messages: list[dict[str, Any]] = dataclass_field(default_factory=list)


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

    env = dict(subprocess.os.environ)
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
                    if hasattr(block, "text"):
                        entry["content"].append(
                            {"type": "text", "text": block.text},
                        )
                    elif hasattr(block, "name"):
                        entry["content"].append(
                            {
                                "type": "tool_use",
                                "name": block.name,
                                "input": getattr(
                                    block,
                                    "input",
                                    {},
                                ),
                            },
                        )
                messages.append(entry)

        async with ClaudeSDKClient(options=options) as client:
            # Pass 1: Explorer
            explorer_msgs: list[dict[str, Any]] = []
            explorer_text = ""
            explorer_cost = 0.0
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
            reviewer_msgs: list[dict[str, Any]] = []
            reviewer_text = ""
            reviewer_cost = 0.0
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
                    if hasattr(block, "text"):
                        entry["content"].append(
                            {
                                "type": "text",
                                "text": block.text,
                            },
                        )
                    elif hasattr(block, "name"):
                        entry["content"].append(
                            {
                                "type": "tool_use",
                                "name": block.name,
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
