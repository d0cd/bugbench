"""Prompt building for in-house agent evaluation."""

from __future__ import annotations

from pathlib import Path

from bugeval.models import TestCase

_DEFAULT_SYSTEM_PROMPT = """\
You are an expert code reviewer. You will review a pull request.

The workspace contains:
- `.pr/description.md` — PR title, description, and metadata
- `.pr/commits.txt` — commit messages (one per line)
- `diff.patch` — the unified diff to review
- `.pr/domain.md` — domain context hints (when available)

If you have file access, read these files directly. Otherwise, they are included inline below.

Do NOT use web search to look up the specific commit, PR, issue, or repository being reviewed.
Your review must be based solely on the PR description, the patch, the repository contents
(if available), and your own expertise.

## Key Principle: Review Both Old and New Code

Analyze BOTH sides of the diff:
- **Removed lines** (`-`): What was the old code doing? What bugs or issues existed?
- **Added lines** (`+`): Is the new code correct? Does it introduce new issues?

Many PRs are bug fixes. When reviewing a bug-fix PR, identify the bug that motivated
the fix (in the removed lines) AND verify the fix is correct (in the added lines).
Report findings from either side of the diff.

## What to Look For

- **Correctness**: Logic errors, off-by-one, wrong conditions, incorrect arithmetic,
  inverted predicates, ignored return values, missing error checks
- **Security**: Injection, unsafe operations, missing auth checks, cryptographic misuse
- **Concurrency**: Data races, deadlocks, incorrect synchronization, TOCTOU
- **Completeness**: Missing error handling, unhandled edge cases, partial migrations,
  incomplete API changes
- **Performance**: Unnecessary allocations, algorithmic inefficiency, missing indexes
- **Style & Clarity**: Confusing naming, dead code, misleading comments, typos in
  string literals or identifiers

## What NOT to Flag

- Test-only changes (added/updated tests, unless the test has a bug)
- Pure refactors with no behavior change
- Low-confidence suspicions below 0.5

## Output Schema

Return your findings as a JSON array:
```json
[
  {
    "file": "path/to/file.rs",
    "line": 42,
    "summary": "Brief description of the issue",
    "confidence": 0.9,
    "severity": "high",
    "category": "logic",
    "suggested_fix": "Change X to Y",
    "reasoning": "Why this is an issue and what impact it has."
  }
]
```

Severity values: "critical" | "high" | "medium" | "low"
Category values: "logic" | "memory" | "concurrency" | "api-misuse" | "type"
  | "cryptographic" | "constraint" | "security" | "performance" | "style"
  | "incomplete" | "code-smell"
Confidence: 0.0-1.0; omit findings below 0.5.

If the PR is a bug fix, report the original bug as a finding and note the fix is correct.
If the PR introduces no issues and fixes no bugs, return: []

End your response with:
1. The JSON array of findings (in a ```json code block)
2. Your review verdict: approve (no blocking issues) or request changes\
"""


def load_agent_prompt(
    path: Path | None = None,
    language: str | None = None,
    config_dir: Path | None = None,
    context_level: str | None = None,
    case_type: str | None = None,
) -> str:
    """Load system prompt, with optional context-level, case-type, and language overrides.

    Resolution order:
    1. Explicit path= (if provided and exists)
    2. config_dir/agent_prompt_{case_type}.md (if case_type provided and file exists)
    3. config_dir/agent_prompt_{context_level}.md (if context_level provided and file exists)
    4. config_dir/agent_prompt_{language}.md (if language provided and file exists)
    5. config_dir/agent_prompt.md
    6. Built-in _DEFAULT_SYSTEM_PROMPT
    """
    if path is not None:
        if path.exists():
            return path.read_text()
        return _DEFAULT_SYSTEM_PROMPT

    base = config_dir if config_dir is not None else Path("config")

    if case_type and case_type != "fix":
        ct_file = base / f"agent_prompt_{case_type}.md"
        if ct_file.exists():
            return ct_file.read_text()

    if context_level:
        ctx_file = base / f"agent_prompt_{context_level}.md"
        if ctx_file.exists():
            return ctx_file.read_text()

    if language:
        lang_file = base / f"agent_prompt_{language}.md"
        if lang_file.exists():
            return lang_file.read_text()

    generic = base / "agent_prompt.md"
    if generic.exists():
        return generic.read_text()

    return _DEFAULT_SYSTEM_PROMPT


def build_user_prompt(case: TestCase, workspace_dir: Path, context_level: str) -> str:
    """Build the user message for the agent based on context level.

    Reads PR context and diff from workspace files written by
    ``materialize_workspace()``.  Content is always inlined (API agents
    can't read files).  For ``diff+repo`` and ``diff+repo+domain`` the
    prompt also tells CLI agents where the files live on disk.
    """
    pr_dir = workspace_dir / ".pr"

    # --- read workspace files --------------------------------------------------
    desc_path = pr_dir / "description.md"
    description = desc_path.read_text() if desc_path.exists() else ""

    commits_path = pr_dir / "commits.txt"
    commits_raw = commits_path.read_text().strip() if commits_path.exists() else ""

    diff_path = workspace_dir / "diff.patch"
    diff_content = diff_path.read_text() if diff_path.exists() else ""

    domain_path = pr_dir / "domain.md"
    domain_content = domain_path.read_text().strip() if domain_path.exists() else ""

    # --- assemble prompt -------------------------------------------------------
    is_cli = True  # CLI agents can read files; API agents need inline content

    lines = ["## Pull Request Review", ""]

    # PR description — always inline (small)
    if description.strip():
        lines += [description.strip(), ""]

    # Commits — always inline (small)
    if commits_raw:
        lines += ["### Commits"]
        for msg in commits_raw.splitlines():
            if msg.strip():
                lines += [f"- {msg.strip()}"]
        lines += [""]

    # Diff — inline only for API agents; CLI agents read from workspace file
    if is_cli:
        lines += [
            "### Diff",
            "",
            "The diff is at `diff.patch` in the current working directory.",
            "Read it with the Read tool before starting your review.",
        ]
    elif diff_content.strip():
        lines += ["### Diff", "```diff", diff_content.rstrip(), "```"]

    # Workspace file pointers (all context levels for CLI)
    lines += [
        "",
        "The workspace contains `.pr/description.md`, `.pr/commits.txt`,"
        " and `diff.patch`. Read these files to understand the PR.",
    ]

    # Repo exploration instructions (context-level dependent)
    if context_level in ("diff+repo", "diff+repo+domain"):
        lines += [
            "",
            "The full repository is checked out in the current working directory.",
            "",
            "**Step 1 — Understand the change** (use Read, Grep, Glob as needed):",
            "- Read the functions that contain the changed lines (not the entire file)",
            "- Grep for callers and usages of any function the patch modifies",
            "- Briefly describe: what this patch is trying to accomplish and what"
            "  invariants or contracts the changed code is expected to maintain",
            "",
            "**Step 2 — Find violations**:",
            "- Check whether the patch correctly maintains those invariants",
            "- Look for edge cases, incorrect assumptions, or missing checks",
        ]

    # Domain hints (highest context level only)
    if context_level == "diff+repo+domain" and domain_content:
        lines += [
            "",
            "### Domain Context",
        ]
        for dl in domain_content.splitlines():
            lines += [f"- {dl}"]

    # Closing instruction
    if context_level in ("diff+repo", "diff+repo+domain"):
        lines += [
            "",
            "Walk through your reasoning, then end with the JSON array in a ```json code block.",
        ]
    else:
        lines += [
            "",
            "Review this pull request. Identify bugs, security issues, incomplete"
            " changes, performance problems, or anything a good reviewer would flag."
            " Return your findings as a JSON array.",
        ]

    return "\n".join(lines)
