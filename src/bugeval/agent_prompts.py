"""Prompt building for in-house agent evaluation."""

from __future__ import annotations

from pathlib import Path

from bugeval.models import TestCase

_DEFAULT_SYSTEM_PROMPT = """\
You are an expert code reviewer specializing in finding bugs in Rust and systems programming code.

You will be given a code patch (diff) to review. Your task is to identify bugs introduced in the
patch or pre-existing bugs that the patch reveals.

For each bug found, record:
- The file where the bug exists
- The approximate line number in the patched file
- A concise summary of what the bug is

Return your findings as a JSON array:
```json
[
  {"file": "path/to/file.rs", "line": 42, "summary": "Brief description of the bug"},
  ...
]
```

If no bugs are found, return an empty array: `[]`

Focus on:
- Logic errors (off-by-one, wrong conditions, incorrect arithmetic)
- Memory safety issues (use-after-free, buffer overflows in unsafe blocks)
- Concurrency bugs (data races, deadlocks, incorrect synchronization)
- API misuse (incorrect parameter order, wrong return value handling)
- Type errors (integer overflow, incorrect casting)

Return ONLY the JSON array of findings, no other text.\
"""


def load_agent_prompt(path: Path | None = None) -> str:
    """Load system prompt from config/agent_prompt.md. Falls back to default."""
    resolved = path or Path("config") / "agent_prompt.md"
    if resolved.exists():
        return resolved.read_text()
    return _DEFAULT_SYSTEM_PROMPT


def build_user_prompt(case: TestCase, patch_content: str, context_level: str) -> str:
    """Build the user message for the agent based on context level.

    - diff-only: just the patch
    - diff+repo: patch + instruction to explore the repo
    - diff+repo+domain: patch + repo + domain context (category, severity)
    """
    lines = [
        f"## Case: {case.id}",
        "",
        "### Patch (diff)",
        "```diff",
        patch_content,
        "```",
    ]

    if context_level in ("diff+repo", "diff+repo+domain"):
        lines += [
            "",
            "The full repository is available in the current working directory.",
            "You may explore the repo to understand surrounding context before reporting findings.",
        ]

    if context_level == "diff+repo+domain":
        lines += [
            "",
            "### Domain Context",
            f"- Category: {case.category}",
            f"- Severity: {case.severity}",
            f"- Language: {case.language}",
            f"- Description: {case.description}",
        ]

    lines += [
        "",
        "Review the patch and return a JSON array of findings.",
    ]

    return "\n".join(lines)
