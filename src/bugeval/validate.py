"""Cross-model validation of ground truth."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bugeval.io import load_cases, load_checkpoint, save_case, save_checkpoint
from bugeval.models import TestCase, Validation

log = logging.getLogger(__name__)

_checkpoint_lock = threading.Lock()

VALID_VERDICTS = {"confirmed", "disputed", "ambiguous"}


def build_validation_prompt(case: TestCase, diff: str) -> str:
    """Build a prompt asking whether a diff introduces the described issue."""
    buggy_lines_text = ""
    if case.truth and case.truth.buggy_lines:
        lines = []
        for bl in case.truth.buggy_lines:
            lines.append(f"  - {bl.file}:{bl.line}  {bl.content}")
        buggy_lines_text = "\n".join(lines)

    return f"""\
You are a code review validation assistant. Your task is to determine whether
the following diff introduces the described bug.

## Bug Description
{case.bug_description}

## Buggy Lines
{buggy_lines_text}

## Introducing Diff
```
{diff}
```

## Instructions
Analyze the diff and determine whether it introduces the bug described above.
Respond with a JSON object (no other text):
{{"verdict": "confirmed"|"disputed"|"ambiguous", "reasoning": "..."}}

- "confirmed" — the diff clearly introduces the described bug
- "disputed" — the diff does NOT introduce the described bug
- "ambiguous" — cannot determine from the available information
"""


def call_llm(prompt: str, model: str = "", backend: str = "sdk") -> str:
    """Delegate to the unified LLM layer, return text."""
    from bugeval.llm import call_llm as _call_llm

    result = _call_llm(prompt, model, backend=backend)
    if result.error:
        log.warning("LLM call failed: %s", result.error)
        return json.dumps({"verdict": "ambiguous", "reasoning": result.error})
    return result.text


def parse_verdict(response: str) -> str:
    """Parse a JSON response to extract the verdict string."""
    text = response.strip()
    # Strip code fences if present
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return "ambiguous"
    verdict = data.get("verdict", "")
    if verdict not in VALID_VERDICTS:
        return "ambiguous"
    return verdict


def _get_introducing_diff(case: TestCase, repo_dir: Path) -> str:
    if not case.truth or not case.truth.introducing_commit:
        return ""
    commit = case.truth.introducing_commit
    try:
        result = subprocess.run(
            ["git", "show", "--format=", "--patch", commit],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        log.warning("Failed to get diff for %s commit %s", case.id, commit)
        return ""


def validate_case(
    case: TestCase,
    diff: str,
    models: list[str],
    backend: str = "sdk",
) -> Validation:
    """Run validation with specified models and return a Validation object."""
    prompt = build_validation_prompt(case, diff)
    claude_verdict = ""
    gemini_verdict = ""
    openai_verdict = ""

    for model_name in models:
        if model_name == "claude":
            raw = call_llm(prompt, backend=backend)
            claude_verdict = parse_verdict(raw)
        elif model_name == "gemini":
            raw = call_llm(prompt, backend="gemini")
            gemini_verdict = parse_verdict(raw)
        elif model_name == "openai":
            raw = call_llm(prompt, backend="openai")
            openai_verdict = parse_verdict(raw)
        else:
            log.warning("Unknown model: %s", model_name)

    # Compute agreement: all present verdicts must match
    verdicts = [v for v in [claude_verdict, gemini_verdict, openai_verdict] if v]
    if len(verdicts) <= 1:
        agreement = True  # single model: vacuously true
    else:
        agreement = len(set(verdicts)) == 1

    return Validation(
        claude_verdict=claude_verdict,
        gemini_verdict=gemini_verdict,
        openai_verdict=openai_verdict,
        agreement=agreement,
        test_validated=agreement and all(v == "confirmed" for v in verdicts),
    )


def validate_cases(
    cases_dir: Path,
    repo_dir: Path,
    models: list[str],
    concurrency: int,
    dry_run: bool,
    backend: str = "sdk",
) -> None:
    """Orchestrate validation across all cases with checkpoint support."""
    cases = load_cases(cases_dir)
    checkpoint_path = cases_dir / ".validate_checkpoint.json"
    done = load_checkpoint(checkpoint_path)

    # Filter to cases needing validation
    to_validate: list[TestCase] = []
    for case in cases:
        if case.id in done:
            log.info("Skipping %s (checkpoint)", case.id)
            continue
        if case.validation and case.validation.test_validated:
            log.info("Skipping %s (already validated)", case.id)
            continue
        if case.truth is None:
            log.info("Skipping %s (no ground truth)", case.id)
            continue
        to_validate.append(case)

    if not to_validate:
        log.info("No cases to validate")
        return

    if dry_run:
        for case in to_validate:
            log.info("[dry-run] Would validate %s", case.id)
        return

    def _process(case: TestCase) -> str:
        diff = _get_introducing_diff(case, repo_dir)
        if not diff:
            log.warning("No diff for %s, skipping", case.id)
            return case.id
        validation = validate_case(case, diff, models, backend=backend)
        case.validation = validation
        if validation.test_validated and case.status in (
            "draft",
            "ground-truth",
            "curated",
        ):
            case.status = "validated"
        # Find the original file path and save back
        for p in sorted(cases_dir.rglob("*.yaml")):
            if p.stem == case.id or p.name == f"{case.id}.yaml":
                save_case(case, p)
                break
        return case.id

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_process, c): c for c in to_validate}
        for future in as_completed(futures):
            case_id = future.result()
            with _checkpoint_lock:
                done.add(case_id)
                save_checkpoint(done, checkpoint_path)
            log.info("Validated %s", case_id)
