"""Custom Claude agent evaluation runner."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from bugeval.models import TestCase
from bugeval.result_models import Comment, ToolResult

log = logging.getLogger(__name__)

MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
COST_CEILING_USD = float(os.getenv("COST_CEILING_USD", "2.0"))
API_TIMEOUT_SECONDS = float(os.getenv("API_TIMEOUT_SECONDS", "120.0"))

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


def _make_tool_result(
    *,
    case: TestCase,
    tool: str,
    context_level: str,
    start: float,
    messages: list[dict[str, Any]],
    comments: list[Comment] | None = None,
    error: str = "",
    cost_usd: float = 0.0,
    transcript_dir: Path | None = None,
) -> ToolResult:
    """Build a ToolResult with elapsed time and optional transcript."""
    elapsed = time.monotonic() - start
    transcript_path = ""
    if transcript_dir and messages:
        transcript_path = _save_transcript(messages, transcript_dir, case.id)
    return ToolResult(
        case_id=case.id,
        tool=tool,
        context_level=context_level,
        comments=comments or [],
        time_seconds=round(elapsed, 2),
        cost_usd=cost_usd,
        error=error,
        transcript_path=transcript_path,
    )


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


# ---------------------------------------------------------------------------
# Re-exports for backward compatibility
# ---------------------------------------------------------------------------
from bugeval._anthropic_runner import run_anthropic_api as run_anthropic_api  # noqa: F401,E402
from bugeval._cli_runners import _claude_build_cmd as _claude_build_cmd  # noqa: F401,E402
from bugeval._cli_runners import _claude_parse_output as _claude_parse_output  # noqa: F401,E402
from bugeval._cli_runners import _CliConfig as _CliConfig  # noqa: F401,E402
from bugeval._cli_runners import _codex_build_cmd as _codex_build_cmd  # noqa: F401,E402
from bugeval._cli_runners import (  # noqa: F401,E402
    _estimate_claude_cli_cost as _estimate_claude_cli_cost,
)
from bugeval._cli_runners import _gemini_build_cmd as _gemini_build_cmd  # noqa: F401,E402
from bugeval._cli_runners import _plain_parse_output as _plain_parse_output  # noqa: F401,E402
from bugeval._cli_runners import _run_claude_cli as _run_claude_cli  # noqa: F401,E402
from bugeval._cli_runners import _run_cli_tool as _run_cli_tool  # noqa: F401,E402
from bugeval._cli_runners import _run_codex_cli as _run_codex_cli  # noqa: F401,E402
from bugeval._cli_runners import _run_gemini_cli as _run_gemini_cli  # noqa: F401,E402
from bugeval._cli_runners import _save_cli_transcript as _save_cli_transcript  # noqa: F401,E402
from bugeval._cli_runners import (  # noqa: F401,E402
    _try_parse_json_or_raw as _try_parse_json_or_raw,
)
from bugeval._cli_runners import run_agent_cli as run_agent_cli  # noqa: F401,E402
from bugeval._gemini_runner import run_google_api as run_google_api  # noqa: F401,E402
from bugeval._openai_runner import run_openai_api as run_openai_api  # noqa: F401,E402
from bugeval._sdk_runner import (  # noqa: F401,E402
    _run_agent_sdk_async as _run_agent_sdk_async,
)
from bugeval._sdk_runner import run_agent_sdk as run_agent_sdk  # noqa: F401,E402
from bugeval._two_pass import _EXPLORER_PROMPT as _EXPLORER_PROMPT  # noqa: F401,E402
from bugeval._two_pass import _REVIEWER_PROMPT as _REVIEWER_PROMPT  # noqa: F401,E402
from bugeval._two_pass import _V3_PHASE1_SURVEY as _V3_PHASE1_SURVEY  # noqa: F401,E402
from bugeval._two_pass import (  # noqa: F401,E402
    _V3_PHASE2_INVESTIGATE as _V3_PHASE2_INVESTIGATE,
)
from bugeval._two_pass import _V3_PHASE3_REPORT as _V3_PHASE3_REPORT  # noqa: F401,E402
from bugeval._two_pass import _V3_SYSTEM as _V3_SYSTEM  # noqa: F401,E402
from bugeval._two_pass import _PassResult as _PassResult  # noqa: F401,E402
from bugeval._two_pass import (  # noqa: F401,E402
    _run_single_pass_cli as _run_single_pass_cli,
)
from bugeval._two_pass import (  # noqa: F401,E402
    _run_single_pass_sdk as _run_single_pass_sdk,
)
from bugeval._two_pass import run_agent_sdk_2pass as run_agent_sdk_2pass  # noqa: F401,E402
from bugeval._two_pass import run_agent_sdk_v3 as run_agent_sdk_v3  # noqa: F401,E402
