"""Curation pass: auto-detect and exclude bad test cases."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import click

from bugeval.io import load_cases, save_case
from bugeval.models import TestCase

log = logging.getLogger(__name__)

# Reasons for automatic exclusion
REASON_NO_BUGGY_LINES = "no-buggy-lines"
REASON_TOO_MANY_BUGGY_LINES = "too-many-buggy-lines"
REASON_FEATURE_NOT_FIX = "feature-not-fix"
REASON_WRONG_DESCRIPTION = "wrong-bug-description"
REASON_DUPLICATE_INTRODUCING = "duplicate-introducing-pr"
REASON_MERGED_SIBLING = "merged-sibling"
REASON_CI_FIX = "ci-release-fix"
REASON_DOC_FIX = "doc-fix"
REASON_DEPENDENCY_BUMP = "dependency-bump"
REASON_ALL_TEST_EXPECTATION = "all-test-expectation"
REASON_SELF_REFERENTIAL = "self-referential"
REASON_CORRUPTED = "corrupted-data"
REASON_ALL_NON_CODE = "all-non-code-lines"
REASON_CLIPPY_LINT = "clippy-lint-fix"
REASON_TYPO_ONLY = "typo-only"
REASON_RELEASE_VERSION = "release-version-bump"
REASON_PERF_OPTIMIZATION = "perf-optimization"
REASON_DEPRECATION = "deprecation-removal"
REASON_LLM_NOT_A_BUG = "llm-not-a-bug"
REASON_NOT_VALIDATED = "not-validated"

MAX_BUGGY_LINES = 50
FEATURE_KEYWORDS = ["[Feature]", "[Feat]", "feat:", "feat("]

_CI_SIGNALS = (
    " ci",
    "ci ",
    "ci.",
    "release script",
    "release ci",
    "musl",
    "circle ci",
    "circleci",
    "github actions",
)
_DOC_SIGNALS = (
    "[docs]",
    "[doc]",
    "doc fix",
    "fix doc",
    "broken link",
    "readme",
    "fix some doc",
    "fix and improve some doc",
    "help message",
    "in documentation",
)

_LINT_SIGNALS = re.compile(
    r"\b(clippy|lint(ing)?|rustfmt|format(ting)?)\b",
    re.IGNORECASE,
)
_TYPO_SIGNALS = re.compile(
    r"\b(typo[s]?|spelling|redundant\s+words?)\b",
    re.IGNORECASE,
)
_RELEASE_SIGNALS = re.compile(
    r"(\[release\]|patch\s+release|version\s+bump|v\d+\.\d+\.\d+\s+patch)",
    re.IGNORECASE,
)
_PERF_SIGNALS = re.compile(
    r"\b(perf[:|]|extra\s+allocat\w+|optimiz\w+|speed\s+up)\b",
    re.IGNORECASE,
)
_DEPRECATION_SIGNALS = re.compile(
    r"\b(deprecat\w+\s+warning|remove\s+deprecat\w+)\b",
    re.IGNORECASE,
)


def auto_curate_case(
    case: TestCase,
    *,
    require_validation: bool = False,
) -> str | None:
    """Return an exclusion reason if the case should be excluded, else None."""
    truth = case.truth
    fix_title = case.fix_pr_title.lower()

    # Dependency bumps (dependabot, renovate, version bumps)
    if fix_title.startswith("bump ") or fix_title.startswith("chore(deps"):
        return REASON_DEPENDENCY_BUMP

    # CI/release fixes — not reviewable code bugs
    if any(sig in fix_title for sig in _CI_SIGNALS):
        # Only if there are no source-code buggy lines
        if truth is None or not truth.buggy_lines:
            return REASON_CI_FIX
        source_lines = [bl for bl in truth.buggy_lines if not bl.is_test_expectation]
        if not source_lines:
            return REASON_CI_FIX

    # Doc-only fixes
    if any(sig in fix_title for sig in _DOC_SIGNALS):
        return REASON_DOC_FIX

    # Clippy / lint / formatting fixes
    if _LINT_SIGNALS.search(fix_title):
        return REASON_CLIPPY_LINT

    # Typo-only fixes
    if _TYPO_SIGNALS.search(fix_title):
        return REASON_TYPO_ONLY

    # Release / version bump
    if fix_title.startswith("[release]") or _RELEASE_SIGNALS.search(fix_title):
        return REASON_RELEASE_VERSION

    # Performance optimizations (no linked issue = likely not a bug)
    if _PERF_SIGNALS.search(fix_title) and not case.linked_issues:
        return REASON_PERF_OPTIMIZATION

    # Deprecation warning removal
    if _DEPRECATION_SIGNALS.search(fix_title):
        return REASON_DEPRECATION

    # Fix PR is actually a feature
    if any(kw.lower() in fix_title for kw in FEATURE_KEYWORDS):
        if "fix" not in fix_title:
            return REASON_FEATURE_NOT_FIX

    # No buggy lines — can't be mechanically scored
    if truth is not None and not truth.buggy_lines:
        return REASON_NO_BUGGY_LINES

    # All buggy lines are test expectations (no source-code ground truth)
    if truth is not None and truth.buggy_lines:
        if all(bl.is_test_expectation for bl in truth.buggy_lines):
            return REASON_ALL_TEST_EXPECTATION

    # All buggy lines are non-code (comments, imports, config, blanks, attributes)
    # Only exclude when there are 3+ non-test lines — fewer suggests weak ground truth
    if truth is not None and truth.buggy_lines:
        from bugeval.ground_truth import classify_line_content

        non_test = [bl for bl in truth.buggy_lines if not bl.is_test_expectation]
        code_lines = [
            bl for bl in non_test if (bl.line_type or classify_line_content(bl.content)) == "code"
        ]
        if not code_lines and len(non_test) >= 3:
            return REASON_ALL_NON_CODE

    # Too many buggy lines — likely a refactor (warn, don't exclude)
    # These are kept for LLM judge evaluation but flagged in analysis

    # Self-referential: introducing PR == fix PR
    if case.introducing_pr_number and case.fix_pr_number:
        if case.introducing_pr_number == case.fix_pr_number:
            return REASON_SELF_REFERENTIAL

    # Corrupted introducing commit (multi-line string with embedded diff)
    if truth and truth.introducing_commit:
        ic = truth.introducing_commit
        if len(ic) > 45 or "\n" in ic:
            return REASON_CORRUPTED

    # Validation enforcement
    if require_validation:
        if case.validation is None or not case.validation.test_validated:
            return REASON_NOT_VALIDATED

    return None


_CLASSIFY_PROMPT = """\
You are a dataset quality reviewer for a code review evaluation benchmark.
Your job is to determine whether a GitHub PR actually fixes a **correctness bug**
(wrong behavior, crash, incorrect output) or is something else.

## Fix PR Title
{title}

## Fix PR Body
{body}

## Bug Description
{description}

## Instructions
Classify this PR into exactly one category:
- "bug" — fixes a correctness defect (wrong behavior, crash, panic, incorrect \
output, type error, logic error, security vulnerability)
- "feature" — adds new functionality, support for new syntax, new API endpoints
- "performance" — optimization, allocation reduction, speed improvement
- "style" — typos, formatting, clippy/lint fixes, comment corrections
- "docs" — documentation, README, help text changes
- "refactor" — code restructuring with no behavior change
- "infra" — CI, release scripts, version bumps, dependency updates
- "not-a-bug" — anything else that is not a correctness defect

Respond with JSON only (no other text):
{{"classification": "<category>", "reasoning": "<one sentence>"}}
"""

_VALID_NON_BUG_CATEGORIES = frozenset(
    {
        "feature",
        "performance",
        "style",
        "docs",
        "refactor",
        "infra",
        "not-a-bug",
    }
)


def llm_classify_case(
    case: TestCase,
    model: str = "",
    backend: str = "sdk",
) -> str | None:
    """Call LLM to classify whether a case is a real bug."""
    from bugeval.llm import call_llm

    prompt = _CLASSIFY_PROMPT.format(
        title=case.fix_pr_title,
        body=(case.fix_pr_body or "")[:2000],
        description=(case.bug_description or "")[:1000],
    )
    result = call_llm(prompt, model=model, backend=backend)
    if result.error:
        log.warning("LLM classify failed for %s: %s", case.id, result.error)
        return None  # Fail open

    text = result.text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        log.warning("LLM classify: unparseable JSON for %s", case.id)
        return None

    classification = data.get("classification", "")
    reasoning = data.get("reasoning", "")

    if classification == "bug":
        return None
    if classification in _VALID_NON_BUG_CATEGORIES:
        log.info(
            "LLM excluded %s as %s: %s",
            case.id,
            classification,
            reasoning,
        )
        return REASON_LLM_NOT_A_BUG

    return None  # Unknown → fail open


def compute_quality_flags(case: TestCase) -> list[str]:
    """Compute non-exclusion quality warnings for a case."""
    flags: list[str] = []
    truth = case.truth

    if truth and truth.buggy_lines and len(truth.buggy_lines) > MAX_BUGGY_LINES:
        flags.append("many-buggy-lines")

    if truth and truth.blame_confidence and truth.blame_confidence in ("C", "D"):
        flags.append("low-blame-confidence")

    return flags


def find_duplicate_introducing(cases: list[TestCase]) -> set[str]:
    """Find cases that share an introducing PR (keep first, exclude rest)."""
    seen: dict[int | None, str] = {}
    duplicates: set[str] = set()
    for case in cases:
        ipn = case.introducing_pr_number
        if ipn is None:
            continue
        if ipn in seen:
            duplicates.add(case.id)
        else:
            seen[ipn] = case.id
    return duplicates


def curate_cases(
    cases_dir: Path,
    *,
    dry_run: bool = False,
    reset: bool = False,
    use_llm: bool = False,
    llm_model: str = "",
    llm_backend: str = "sdk",
    require_validation: bool = False,
) -> dict[str, list[str]]:
    """Run curation pass on all cases. Returns {reason: [case_ids]}."""

    all_cases = load_cases(cases_dir, include_excluded=True)
    results: dict[str, list[str]] = {}

    if reset:
        for case in all_cases:
            if case.excluded:
                case.excluded = False
                case.excluded_reason = ""
                if not dry_run:
                    path = _find_case_path(cases_dir, case.id)
                    if path:
                        save_case(case, path)
        return results

    # Find introducing PR duplicates
    dup_ids = find_duplicate_introducing(all_cases)

    for case in all_cases:
        if case.excluded:
            results.setdefault("already-excluded", []).append(case.id)
            continue

        reason = auto_curate_case(case, require_validation=require_validation)
        if reason is None and case.id in dup_ids:
            reason = REASON_MERGED_SIBLING
        if reason is None and use_llm:
            reason = llm_classify_case(case, model=llm_model, backend=llm_backend)

        if reason:
            results.setdefault(reason, []).append(case.id)
            if not dry_run:
                case.excluded = True
                case.excluded_reason = reason
                path = _find_case_path(cases_dir, case.id)
                if path:
                    save_case(case, path)

        if reason is None:
            status_changed = False
            if case.status in ("draft", "ground-truth"):
                if not dry_run:
                    case.status = "curated"
                    status_changed = True
            flags = compute_quality_flags(case)
            if flags:
                case.quality_flags = flags
                results.setdefault("quality-flagged", []).append(case.id)
            if not dry_run and (flags or status_changed):
                path = _find_case_path(cases_dir, case.id)
                if path:
                    save_case(case, path)

    return results


def _find_case_path(cases_dir: Path, case_id: str) -> Path | None:
    """Find the YAML file for a case ID."""
    for p in cases_dir.rglob("*.yaml"):
        if p.stem == case_id:
            return p
    return None


@click.command()
@click.option("--cases-dir", required=True, help="Directory with case YAMLs")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be excluded without modifying",
)
@click.option("--reset", is_flag=True, help="Reset all exclusions")
@click.option("--llm", is_flag=True, help="Use LLM classification gate")
@click.option("--llm-model", default="", help="Model for LLM classification")
@click.option("--llm-backend", default="sdk", help="Backend for LLM calls")
@click.option(
    "--require-validation",
    is_flag=True,
    help="Exclude cases without validation",
)
def curate(
    cases_dir: str,
    dry_run: bool,
    reset: bool,
    llm: bool,
    llm_model: str,
    llm_backend: str,
    require_validation: bool,
) -> None:
    """Auto-curate test cases: detect and exclude bad cases."""
    results = curate_cases(
        Path(cases_dir),
        dry_run=dry_run,
        reset=reset,
        use_llm=llm,
        llm_model=llm_model,
        llm_backend=llm_backend,
        require_validation=require_validation,
    )

    if reset:
        click.echo("Reset all exclusions.")
        return

    total_excluded = 0
    for reason, case_ids in sorted(results.items()):
        if reason == "already-excluded":
            click.echo(f"  Already excluded: {len(case_ids)}")
            continue
        action = "Would exclude" if dry_run else "Excluded"
        click.echo(f"  {action} ({reason}): {', '.join(case_ids)}")
        total_excluded += len(case_ids)

    click.echo(f"\n{'Would exclude' if dry_run else 'Excluded'} {total_excluded} cases.")
