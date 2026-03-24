"""Scoring: mechanical catch rate + LLM quality judge."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

import click

from bugeval.io import (
    load_cases,
    load_checkpoint,
    load_result,
    save_checkpoint,
    save_score,
)
from bugeval.models import BuggyLine, GroundTruth, TestCase
from bugeval.result_models import ToolResult
from bugeval.score_models import CaseScore, CommentScore, CommentVerdict

log = logging.getLogger(__name__)

_MAX_PRECISE_BUGGY_LINES = 50


def _get_precise_buggy_lines(truth: GroundTruth) -> list[BuggyLine]:
    """Return non-test buggy lines only if the case has precise ground truth."""
    non_test = [bl for bl in truth.buggy_lines if not bl.is_test_expectation]
    if len(non_test) > _MAX_PRECISE_BUGGY_LINES:
        return []  # Too diffuse for mechanical matching
    return non_test


_GENERIC_BODIES = {
    "lgtm",
    "looks good",
    "nit",
    "nice",
    "ok",
    "+1",
    "approved",
    "ship it",
}


def mechanical_catch(
    result: ToolResult,
    truth: GroundTruth | None,
    tolerance: int = 10,
) -> tuple[bool, int | None]:
    """Check if any comment references a buggy line within tolerance."""
    if truth is None or not truth.buggy_lines:
        return False, None

    precise = _get_precise_buggy_lines(truth)
    if not precise:
        return False, None  # Diffuse: defer to LLM judge

    best_dist: int | None = None
    for comment in result.comments:
        if comment.line == 0:
            continue
        for bl in precise:
            if _files_match(comment.file, bl.file):
                dist = abs(comment.line - bl.line)
                if dist <= tolerance:
                    if best_dist is None or dist < best_dist:
                        best_dist = dist

    if best_dist is not None:
        return True, best_dist
    return False, None


def classify_comments(
    result: ToolResult,
    truth: GroundTruth | None,
    tolerance: int = 10,
) -> list[CommentScore]:
    """Classify each comment as TP, FP, or low-value."""
    scores: list[CommentScore] = []
    for i, comment in enumerate(result.comments):
        if comment.line == 0:
            scores.append(
                CommentScore(
                    comment_index=i,
                    verdict=CommentVerdict.low_value,
                )
            )
            continue

        body_stripped = comment.body.strip().lower()
        if len(comment.body.strip()) < 20 or body_stripped in _GENERIC_BODIES:
            scores.append(
                CommentScore(
                    comment_index=i,
                    verdict=CommentVerdict.low_value,
                )
            )
            continue

        if truth is None or not truth.buggy_lines:
            scores.append(
                CommentScore(
                    comment_index=i,
                    verdict=CommentVerdict.fp,
                )
            )
            continue

        precise = _get_precise_buggy_lines(truth)
        matched = False
        for bl_idx, bl in enumerate(truth.buggy_lines):
            if bl not in precise:
                continue
            if _files_match(comment.file, bl.file):
                dist = abs(comment.line - bl.line)
                if dist <= tolerance:
                    scores.append(
                        CommentScore(
                            comment_index=i,
                            verdict=CommentVerdict.tp,
                            matched_buggy_line_idx=bl_idx,
                        )
                    )
                    matched = True
                    break

        if not matched:
            scores.append(
                CommentScore(
                    comment_index=i,
                    verdict=CommentVerdict.fp,
                )
            )

    return scores


def build_judge_prompt(
    case: TestCase,
    result: ToolResult,
    diff: str = "",
) -> str:
    """Build prompt for LLM quality judge."""
    comments_text = ""
    for i, c in enumerate(result.comments):
        comments_text += f"\n### Comment {i}\nFile: {c.file}, Line: {c.line}\nBody: {c.body}\n"
        if c.suggested_fix:
            comments_text += f"Suggested fix: {c.suggested_fix}\n"

    buggy_lines_text = ""
    if case.truth and case.truth.buggy_lines:
        for bl in case.truth.buggy_lines:
            buggy_lines_text += f"  - {bl.file}:{bl.line} {bl.content}\n"

    diff_section = ""
    if diff:
        diff_section = f"\n## Diff Under Review\n```diff\n{diff}\n```\n"

    return f"""\
You are an expert code review judge. Evaluate the quality of a tool\'s \
bug-finding review.

## Known Bug
Description: {case.bug_description}
Fix summary: {case.truth.fix_summary if case.truth else "N/A"}
Buggy lines:
{buggy_lines_text or "  (none)"}

## Review Comments
{comments_text or "(no comments)"}
{diff_section}
## Scoring Rubric

IMPORTANT: Score detection_score and review_quality INDEPENDENTLY.
A review can have high quality (found other real issues, good explanations)
even if it missed the specific known bug (detection_score=0).

**Detection Score (0-3):**
- 0 = missed \u2014 tool did not identify the bug at all
- 1 = wrong-area \u2014 tool flagged something in the right file but wrong area
- 2 = correct-id \u2014 tool correctly identified the bug location
- 3 = correct-id-and-fix \u2014 tool identified the bug AND suggested a correct fix

**Review Quality (0-4):**
- 0 = useless \u2014 no actionable feedback
- 1 = shallow \u2014 vague or generic feedback
- 2 = adequate \u2014 identifies the issue with some detail
- 3 = strong \u2014 clear identification with good explanation
- 4 = exceptional \u2014 precise identification, root cause, and correct fix

**Comment Verdicts:** For each comment, assign one of:
- "TP" \u2014 true positive, correctly identifies the known bug
- "TP-novel" \u2014 true positive, identifies a real bug NOT in the known ground truth
- "FP" \u2014 false positive, incorrect or irrelevant
- "low-value" \u2014 generic, vague, or too short to be useful

Respond with ONLY valid JSON (no markdown fences):
{{{{\"detection_score\": <0-3>, \"review_quality\": <0-4>, \
\"comment_verdicts\": [<verdict for each comment>], \
\"reasoning\": \"<brief explanation>\"}}}}"""


def majority_vote(
    verdicts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate multiple judge verdicts via majority voting.

    For numeric scores, the most common value wins. Ties go to the
    lower (more conservative) score. For comment verdicts, per-index
    majority vote with conservative tie-breaking.
    """
    if len(verdicts) == 1:
        result = dict(verdicts[0])
        result["judge_agreement"] = 1.0
        result.setdefault("judge_cost_usd", 0.0)
        return result

    def _majority_int(key: str) -> int:
        vals = [int(v.get(key, 0)) for v in verdicts]
        counts = Counter(vals)
        max_count = max(counts.values())
        candidates = [v for v, c in counts.items() if c == max_count]
        return min(candidates)

    det = _majority_int("detection_score")
    rq = _majority_int("review_quality")

    det_vals = [int(v.get("detection_score", 0)) for v in verdicts]
    agreement = det_vals.count(det) / len(det_vals)

    _CONSERVATIVE_ORDER: dict[str, int] = {
        "FP": 0,
        "low-value": 1,
        "TP-novel": 2,
        "TP": 3,
    }
    all_verdict_lists = [v.get("comment_verdicts", []) for v in verdicts]
    max_len = max((len(vl) for vl in all_verdict_lists), default=0)
    merged_verdicts: list[str] = []
    for idx in range(max_len):
        position_vals = [vl[idx] for vl in all_verdict_lists if idx < len(vl)]
        if not position_vals:
            merged_verdicts.append("FP")
            continue
        counts = Counter(position_vals)
        max_count = max(counts.values())
        candidates = [v for v, c in counts.items() if c == max_count]
        candidates.sort(key=lambda x: _CONSERVATIVE_ORDER.get(x, 0))
        merged_verdicts.append(candidates[0])

    reasoning = verdicts[0].get("reasoning", "")
    total_judge_cost = sum(v.get("judge_cost_usd", 0.0) for v in verdicts)

    return {
        "detection_score": det,
        "review_quality": rq,
        "comment_verdicts": merged_verdicts,
        "reasoning": reasoning,
        "judge_agreement": round(agreement, 4),
        "judge_cost_usd": round(total_judge_cost, 6),
    }


def _call_single_judge(
    prompt: str,
    model: str,
    case_id: str,
) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text  # type: ignore[union-attr]
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    result: dict[str, Any] = json.loads(text)
    usage = response.usage
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    result["judge_cost_usd"] = round(inp * 0.80 / 1e6 + out * 4.00 / 1e6, 6)
    return result


def _call_judge_sdk(
    prompt: str,
    model: str,
    case_id: str,
) -> dict[str, Any]:
    """Call LLM judge via the SDK backend (no API key needed)."""
    from bugeval.llm import call_llm

    result = call_llm(prompt, model=model, backend="sdk", max_tokens=2048)
    if result.error:
        raise RuntimeError(f"SDK judge failed: {result.error}")
    text = result.text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed: dict[str, Any] = json.loads(text)
    parsed["judge_cost_usd"] = round(result.cost_usd, 6)
    return parsed


def call_judge(
    prompt: str,
    model: str = "claude-haiku-4-5",
    case_id: str = "",
    judge_models: list[str] | None = None,
    backend: str = "api",
) -> dict[str, Any]:
    """Call LLM for judging and parse JSON response.

    backend="api" uses anthropic.Anthropic() (needs ANTHROPIC_API_KEY).
    backend="sdk" uses claude-agent-sdk (no API key needed).
    """
    judge_fn = _call_judge_sdk if backend == "sdk" else _call_single_judge

    if judge_models:
        collected: list[dict[str, Any]] = []
        for m in judge_models:
            try:
                v = judge_fn(prompt, m, case_id)
                collected.append(v)
            except Exception as exc:
                log.warning(
                    "Judge %s failed for %s: %s",
                    m,
                    case_id,
                    exc,
                )
        if not collected:
            return {
                "detection_score": 0,
                "review_quality": 0,
                "comment_verdicts": [],
                "reasoning": "All ensemble judges failed",
                "judge_failed": True,
                "judge_cost_usd": 0.0,
            }
        return majority_vote(collected)

    # Single-model path
    try:
        return judge_fn(prompt, model, case_id)
    except Exception as exc:
        log.warning("Judge call failed for %s: %s", case_id, exc)
        return {
            "detection_score": 0,
            "review_quality": 0,
            "comment_verdicts": [],
            "reasoning": "LLM judge failed",
            "judge_failed": True,
            "judge_cost_usd": 0.0,
        }


def score_case(
    case: TestCase,
    result: ToolResult,
    use_llm: bool = True,
    judge_model: str = "claude-haiku-4-5",
    judge_models: list[str] | None = None,
    backend: str = "api",
) -> CaseScore:
    """Score a single case result (mechanical + optional LLM)."""
    if case.kind == "clean":
        has_bug_comments = len(result.comments) > 0
        comment_scores = classify_comments(result, None)
        fp = sum(1 for cs in comment_scores if cs.verdict == CommentVerdict.fp)
        return CaseScore(
            case_id=case.id,
            tool=result.tool,
            caught=False,
            false_alarm=has_bug_comments,
            comment_scores=comment_scores,
            tp_count=0,
            fp_count=fp,
            potentially_contaminated=result.potentially_contaminated,
            context_level=result.context_level,
        )

    caught, dist = mechanical_catch(result, case.truth)
    comment_scores = classify_comments(result, case.truth)

    tp_count = sum(1 for s in comment_scores if s.verdict == CommentVerdict.tp)
    fp_count = sum(1 for s in comment_scores if s.verdict == CommentVerdict.fp)
    novel_count = sum(1 for s in comment_scores if s.verdict == CommentVerdict.tp_novel)

    detection_score = 0
    review_quality = 0
    reasoning = ""
    judge_failed = False
    judge_cost_usd = 0.0
    judge_result: dict[str, Any] = {}

    if use_llm and case.truth:
        prompt = build_judge_prompt(case, result)
        judge_result = call_judge(
            prompt,
            model=judge_model,
            case_id=case.id,
            judge_models=judge_models,
            backend=backend,
        )
        detection_score = max(0, min(3, int(judge_result.get("detection_score", 0))))
        review_quality = max(0, min(4, int(judge_result.get("review_quality", 0))))
        reasoning = str(judge_result.get("reasoning", ""))
        judge_failed = bool(judge_result.get("judge_failed", False))
        judge_cost_usd = float(judge_result.get("judge_cost_usd", 0.0))

        verdicts = judge_result.get("comment_verdicts", [])
        _VERDICT_MAP = {
            "TP": CommentVerdict.tp,
            "TP-novel": CommentVerdict.tp_novel,
            "FP": CommentVerdict.fp,
            "low-value": CommentVerdict.low_value,
        }
        for j, v in enumerate(verdicts):
            if j < len(comment_scores) and v in _VERDICT_MAP:
                comment_scores[j] = CommentScore(
                    comment_index=j,
                    verdict=_VERDICT_MAP[v],
                    matched_buggy_line_idx=comment_scores[j].matched_buggy_line_idx,
                )

        tp_count = sum(1 for s in comment_scores if s.verdict == CommentVerdict.tp)
        fp_count = sum(1 for s in comment_scores if s.verdict == CommentVerdict.fp)
        novel_count = sum(1 for s in comment_scores if s.verdict == CommentVerdict.tp_novel)
    else:
        if caught and any(c.suggested_fix for c in result.comments):
            detection_score = 3
        elif caught:
            detection_score = 2

    judge_agreement = (
        judge_result.get("judge_agreement") if (use_llm and case.truth and judge_models) else None
    )

    # Per-finding-group catch rate
    findings_total = 0
    findings_caught_count = 0
    diffuse = False

    if case.truth and case.truth.buggy_lines:
        precise = _get_precise_buggy_lines(case.truth)
        diffuse = len(precise) == 0

        # Count distinct finding groups (by fix_pr_number)
        fix_prs: set[int] = set()
        for bl in case.truth.buggy_lines:
            if not bl.is_test_expectation:
                fix_prs.add(bl.fix_pr_number or 0)
        # If all buggy lines have fix_pr_number=0, it's 1 finding group
        findings_total = max(len(fix_prs), 1) if fix_prs else 0

        if not diffuse:
            # Check which finding groups have at least one TP match
            caught_groups: set[int] = set()
            for cs in comment_scores:
                if cs.verdict == CommentVerdict.tp and cs.matched_buggy_line_idx is not None:
                    bl = case.truth.buggy_lines[cs.matched_buggy_line_idx]
                    caught_groups.add(bl.fix_pr_number or 0)
            findings_caught_count = len(caught_groups)

    return CaseScore(
        case_id=case.id,
        tool=result.tool,
        caught=caught,
        localization_distance=dist,
        detection_score=detection_score,
        review_quality=review_quality,
        comment_scores=comment_scores,
        reasoning=reasoning,
        tp_count=tp_count,
        fp_count=fp_count,
        novel_count=novel_count,
        potentially_contaminated=result.potentially_contaminated,
        context_level=result.context_level,
        judge_failed=judge_failed,
        judge_models=judge_models or [],
        judge_agreement=judge_agreement,
        judge_cost_usd=judge_cost_usd,
        findings_caught=findings_caught_count,
        findings_total=findings_total,
        diffuse_ground_truth=diffuse,
    )


def detect_contamination(result: ToolResult, case: TestCase) -> bool:
    """Check if tool comments overlap suspiciously with fix PR text."""
    fix_texts = [
        case.fix_pr_title,
        case.fix_pr_body,
        *case.fix_pr_commit_messages,
        *case.fix_pr_review_comments,
        *case.fix_pr_discussion_comments,
    ]
    fix_words: set[str] = set()
    for text in fix_texts:
        fix_words.update(_tokenize(text))

    if len(fix_words) < 3:
        return False

    for comment in result.comments:
        comment_words = _tokenize(comment.body)
        if not comment_words:
            continue
        overlap = comment_words & fix_words
        if len(overlap) / len(comment_words) > 0.3:
            return True

    return False


def score_run(
    run_dir: Path,
    cases_dir: Path,
    dry_run: bool,
    judge_model: str = "claude-haiku-4-5",
    judge_models: list[str] | None = None,
    backend: str = "api",
) -> None:
    """Orchestrator: load results + cases, score each, save scores."""
    results_dir = run_dir / "results"
    scores_dir = run_dir / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_path = scores_dir / "checkpoint.json"
    done = load_checkpoint(checkpoint_path)

    cases = load_cases(cases_dir)
    case_map = {c.id: c for c in cases}

    result_files = sorted(results_dir.glob("*.yaml"))
    total = len(result_files)
    scored = 0

    for rf in result_files:
        result = load_result(rf)
        key = f"{result.case_id}__{result.tool}"

        if key in done:
            continue

        case = case_map.get(result.case_id)
        if case is None:
            click.echo(f"Warning: no case found for {result.case_id}")
            continue

        result.potentially_contaminated = detect_contamination(result, case)
        cs = score_case(
            case,
            result,
            use_llm=not dry_run,
            judge_model=judge_model,
            judge_models=judge_models,
            backend=backend,
        )
        save_score(cs, scores_dir / f"{key}.yaml")

        done.add(key)
        save_checkpoint(done, checkpoint_path)
        scored += 1

    click.echo(f"Scored {scored}/{total} results in {scores_dir}")


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _files_match(comment_file: str, truth_file: str) -> bool:
    """Match file paths, handling partial paths."""
    if not comment_file or not truth_file:
        return False
    if comment_file == truth_file:
        return True
    # Suffix match: one path ends with the other, but the shorter
    # path must contain at least one directory component (a '/').
    if "/" in truth_file and comment_file.endswith("/" + truth_file):
        return True
    if "/" in comment_file and truth_file.endswith("/" + comment_file):
        return True
    return False


def mechanical_catch_details(
    result: ToolResult,
    truth: GroundTruth | None,
    tolerance: int = 10,
) -> list[dict[str, Any]]:
    """Return per-comment diagnostics for mechanical catch matching."""
    if truth is None or not truth.buggy_lines:
        return []

    details: list[dict[str, Any]] = []
    for i, comment in enumerate(result.comments):
        entry: dict[str, Any] = {
            "comment_index": i,
            "comment_file": comment.file,
            "comment_line": comment.line,
        }
        if comment.line == 0:
            entry["skipped"] = "line=0"
            details.append(entry)
            continue

        comparisons: list[dict[str, Any]] = []
        for bl in truth.buggy_lines:
            file_ok = _files_match(comment.file, bl.file)
            dist = abs(comment.line - bl.line)
            comparisons.append(
                {
                    "buggy_file": bl.file,
                    "buggy_line": bl.line,
                    "file_matched": file_ok,
                    "distance": dist,
                    "within_tolerance": file_ok and dist <= tolerance,
                }
            )
        entry["comparisons"] = comparisons
        details.append(entry)

    return details


def _tokenize(text: str) -> set[str]:
    """Extract lowercase word tokens (3+ chars) from text."""
    return {w.lower() for w in re.findall(r"[a-zA-Z_]\w{2,}", text)}
