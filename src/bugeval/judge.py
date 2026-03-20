# src/bugeval/judge.py
"""LLM-as-judge: 3× majority vote scoring for normalized results."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import click
import yaml
from anthropic import Anthropic

from bugeval.agent_api_runner import _ANTHROPIC_RETRYABLE, _retry_call
from bugeval.agent_cli_runner import run_claude_cli, run_codex_cli, run_gemini_cli
from bugeval.judge_models import (
    CommentClassification,
    CommentJudgment,
    JudgeScore,
    NoiseStats,
    majority_vote,
)
from bugeval.models import TestCase
from bugeval.pr_eval_models import default_judging, default_scoring
from bugeval.result_models import NormalizedResult
from bugeval.run_pr_eval import load_cases

_DEFAULT_JUDGE_PROMPT = """\
You are an impartial judge evaluating an AI code review tool's output against a \
known bug-fix PR. Tool name is intentionally omitted to prevent bias.

Per-comment classification — assign each comment one of:
- TP-expected: correctly identifies a known expected finding (matches ground truth)
- TP-novel: identifies a genuine issue NOT in ground truth (verify from the diff)
- FP: incorrect, wrong, or describes a non-issue
- low-value: generic advice, obvious, or not actionable
- uncertain: cannot confidently determine if the finding is real from the diff alone

Per-comment severity (TP-expected and TP-novel only; null for FP/low-value/uncertain):
- critical (4): data loss, security vulnerability, crash in production
- high (3): functional bug, incorrect behavior under normal use
- medium (2): edge case, incomplete handling, misleading code
- low (1): style, naming, minor code smell

Per-comment actionability (TP-expected and TP-novel only; null for FP/low-value/uncertain):
- actionable (1.0): specific fix — what to change, where, and why
- directional (0.6): identifies the problem clearly, no specific fix
- vague (0.3): points at something but unclear what to do

Per-comment relevance: "direct" | "adjacent" | "unrelated"

Bug detection score (0-3):
0 = missed (known bug not identified)
1 = wrong-area (right file, wrong issue)
2 = correct-id (correct file + approximate line, ±10 tolerance)
3 = correct-id-and-fix (correct ID + actionable fix suggestion)

Line number tolerance: accept match if file and semantic description align, even if \
line numbers differ by up to 10.

Multiple expected findings: score based on BEST match. TP-expected count should \
reflect ALL matched findings.

Use the diff to verify TP-novel claims — only classify as TP-novel if you can \
independently confirm the issue is real from the diff.

Return ONLY a JSON object: {"score": N, "reasoning": "...", \
"comment_judgments": [{"id": N, "classification": "TP-expected"|"TP-novel"|"FP"\
|"low-value"|"uncertain", "severity": "critical"|"high"|"medium"|"low"|null, \
"actionability": "actionable"|"directional"|"vague"|null, \
"relevance": "direct"|"adjacent"|"unrelated"}]}
"""


def load_judge_prompt(path: Path | None = None) -> str:
    """Load system prompt from config/judge_prompt.md. Falls back to default."""
    resolved = path or Path("config") / "judge_prompt.md"
    if resolved.exists():
        return resolved.read_text()
    return _DEFAULT_JUDGE_PROMPT


def _extract_judge_json(text: str) -> dict[str, Any] | None:
    """Extract JSON object from judge response.

    Uses bracket counting to find the outermost {...} correctly,
    avoiding greedy regex issues with multiple JSON fragments.
    """
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    search_text = fence.group(1) if fence else text

    pos = 0
    while pos < len(search_text):
        start = search_text.find("{", pos)
        if start == -1:
            return None

        depth = 0
        for i in range(start, len(search_text)):
            if search_text[i] == "{":
                depth += 1
            elif search_text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = search_text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and "score" in parsed:
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    # Continue searching after this object
                    pos = i + 1
                    break
        else:
            # Unbalanced braces — no more candidates
            return None
    return None


def _build_judge_prompt(case: TestCase, result: NormalizedResult, diff_content: str = "") -> str:
    """Build the user message for the judge."""
    is_clean = not case.expected_findings and case.case_type == "clean"

    if is_clean:
        ground_truth_section = (
            "### Ground Truth\n"
            "This PR has no known bugs. Classify each tool comment as "
            "TP-novel (if a real issue confirmed from the diff), FP, "
            "uncertain, or low-value."
        )
    else:
        findings_text = "\n".join(
            f"  - file: {f.file}, line: {f.line}, summary: {f.summary}"
            for f in case.expected_findings
        )
        ground_truth_section = f"### Expected Bug\n{findings_text}"

    diff_section = ""
    if diff_content:
        _MAX_DIFF = 5000
        truncated = diff_content[:_MAX_DIFF]
        if len(diff_content) > _MAX_DIFF:
            truncated += "\n(truncated)"
        diff_section = f"### Diff\n```diff\n{truncated}\n```\n\n"

    comment_lines = []
    for i, c in enumerate(result.comments):
        line = f"  [{i}] file={c.file or '(none)'} line={c.line or '?'}: {c.body[:400]}"
        if c.suggested_fix:
            line += f"\n      Fix: {c.suggested_fix[:400]}"
        comment_lines.append(line)
    comments_text = "\n".join(comment_lines) or "  (no comments)"

    # Tool name is intentionally omitted to prevent judge bias (self-eval pitfall mitigation).
    return (
        f"## Test Case\n"
        f"{ground_truth_section}\n\n"
        f"{diff_section}"
        f"### Tool Comments ({len(result.comments)} total)\n"
        f"{comments_text}\n\n"
        f"Score this tool's output 0–3 and classify each comment."
    )


def _is_google_model(model: str) -> bool:
    return model.startswith("gemini-")


def _is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "o4-", "o3-", "o1-"))


# CLI runner name → model mapping.  Mirrors config.yaml tool definitions.
_CLI_RUNNER_MODELS: dict[str, str] = {
    "claude-cli-haiku": "claude-haiku-4-5",
    "claude-cli-sonnet": "claude-sonnet-4-6",
    "claude-cli-opus": "claude-opus-4-6",
    "gemini-cli-flash-lite": "gemini-2.5-flash-lite",
    "gemini-cli-flash": "gemini-2.5-flash",
    "gemini-cli-pro": "gemini-2.5-pro",
    "codex-cli-mini": "gpt-5.4-mini",
    "codex-cli-5.4": "gpt-5.4",
    "codex-cli-codex": "gpt-5.3-codex",
}


def resolve_judge_runner(name: str) -> tuple[str, str]:
    """Map a judge name to (runner_kind, model).

    CLI runners: "claude-cli-sonnet" → ("claude-cli", "claude-sonnet-4-6")
    API models:  "gemini-2.5-flash"  → ("api", "gemini-2.5-flash")

    # TODO: Extend this pattern to run_agent_eval.py dispatch. A unified
    # resolve_runner() could also handle multi-turn, SDK, and Docker runners,
    # replacing the if/elif chain in process_case_tool_agent().
    """
    if name in _CLI_RUNNER_MODELS:
        if name.startswith("claude-cli"):
            return "claude-cli", _CLI_RUNNER_MODELS[name]
        elif name.startswith("gemini-cli"):
            return "gemini-cli", _CLI_RUNNER_MODELS[name]
        elif name.startswith("codex-cli"):
            return "codex-cli", _CLI_RUNNER_MODELS[name]
    # Bare model name → use the appropriate provider API
    return "api", name


def _call_cli_judge(runner_kind: str, model: str, prompt: str) -> str:
    """Dispatch a single judge call to the appropriate CLI runner."""
    tmp_workspace = Path(tempfile.mkdtemp())
    try:
        if runner_kind == "claude-cli":
            agent_result = run_claude_cli(tmp_workspace, prompt, max_turns=1, model=model)
        elif runner_kind == "gemini-cli":
            agent_result = run_gemini_cli(tmp_workspace, prompt, model=model)
        elif runner_kind == "codex-cli":
            agent_result = run_codex_cli(tmp_workspace, prompt, model=model)
        else:
            raise ValueError(f"Unknown CLI runner kind: {runner_kind!r}")
        return agent_result.response_text or agent_result.stdout
    finally:
        shutil.rmtree(tmp_workspace, ignore_errors=True)


def _call_anthropic_judge(
    model: str, system_prompt: str, user_prompt: str, client: Anthropic | None = None
) -> str:
    _client = client or Anthropic()
    vm = model
    response = _retry_call(
        lambda: _client.messages.create(
            model=vm,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],  # type: ignore[arg-type]
            max_tokens=2048,
            temperature=0,
        ),
        _ANTHROPIC_RETRYABLE,
    )
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


def _call_google_judge(model: str, system_prompt: str, user_prompt: str) -> str:
    import google.genai as genai  # type: ignore[import-untyped]

    api_key = os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=api_key)
    cfg = genai.types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=2048,
        temperature=0,
    )
    response = client.models.generate_content(
        model=model,
        contents=[genai.types.Content(role="user", parts=[genai.types.Part(text=user_prompt)])],
        config=cfg,
    )
    if response.candidates:
        content = response.candidates[0].content
        parts = (content.parts if content else None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                return text
    return ""


def _call_openai_judge(model: str, system_prompt: str, user_prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=2048,
        temperature=0,
    )
    return response.choices[0].message.content or ""


def judge_case(
    case: TestCase,
    result: NormalizedResult,
    system_prompt: str,
    model: str | None = None,
    n_votes: int | None = None,
    dry_run: bool = False,
    client: Anthropic | None = None,
    judges: list[str] | None = None,
    diff_content: str = "",
) -> JudgeScore:
    """Run n_votes independent judge calls. Return majority-vote JudgeScore.

    Args:
        judges: Explicit list of judge runner names (e.g. ``["claude-cli-sonnet",
            "gemini-2.5-flash"]``).  Each entry is resolved via
            ``resolve_judge_runner()`` to determine CLI vs API dispatch.
            When provided, overrides ``config.judging.models``.
    """
    _judging = default_judging()

    # Resolve which judges to use.  ``judges`` param takes priority, then
    # config ensemble, then single-model fallback.
    if judges is not None:
        resolved = [resolve_judge_runner(j) for j in judges]
    elif _judging.models:
        resolved = [resolve_judge_runner(m) for m in _judging.models]
    else:
        fallback = model or _judging.model
        resolved = [resolve_judge_runner(fallback)] * (n_votes or _judging.llm_calls)

    if dry_run:
        return JudgeScore(
            test_case_id=case.id,
            tool=result.tool,
            score=0,
            votes=[0] * len(resolved),
            reasoning="dry-run",
        )

    user_prompt = _build_judge_prompt(case, result, diff_content=diff_content)
    votes: list[int] = []
    parse_failures = 0
    parsed_votes: list[tuple[int, str, list[CommentJudgment]]] = []

    _client = client
    for runner_kind, vote_model in resolved:
        if runner_kind != "api":
            # CLI runner dispatch
            cli_prompt = f"{system_prompt}\n\n{user_prompt}"
            text = _call_cli_judge(runner_kind, vote_model, cli_prompt)
        elif _is_google_model(vote_model):
            text = _call_google_judge(vote_model, system_prompt, user_prompt)
        elif _is_openai_model(vote_model):
            text = _call_openai_judge(vote_model, system_prompt, user_prompt)
        else:
            text = _call_anthropic_judge(vote_model, system_prompt, user_prompt, client=_client)

        data = _extract_judge_json(text)
        if data is None:
            parse_failures += 1
            votes.append(0)
            continue
        vote_score = int(data.get("score", 0))
        vote_reasoning = str(data.get("reasoning", ""))
        votes.append(vote_score)
        raw_judgments = data.get("comment_judgments", [])
        parsed: list[CommentJudgment] = []
        for j in raw_judgments:
            try:
                parsed.append(
                    CommentJudgment(
                        id=int(j["id"]),
                        classification=CommentClassification(j["classification"]),
                        severity=j.get("severity"),
                        actionability=j.get("actionability"),
                        relevance=str(j.get("relevance", "")),
                    )
                )
            except (KeyError, ValueError):
                pass
        parsed_votes.append((vote_score, vote_reasoning, parsed))

    is_clean = not case.expected_findings and case.case_type == "clean"
    score = 0 if is_clean else majority_vote(votes)
    # Use the first parsed vote that matches the majority score for reasoning and
    # comment judgments (consistent: the vote whose score determined the outcome).
    winning_vote = next((pv for pv in parsed_votes if pv[0] == score), None)
    last_judgments = winning_vote[2] if winning_vote else []

    tp_count = sum(
        1 for j in last_judgments if j.classification == CommentClassification.tp_expected
    )
    novel_count = sum(
        1 for j in last_judgments if j.classification == CommentClassification.tp_novel
    )
    fp_count = sum(1 for j in last_judgments if j.classification == CommentClassification.fp)
    lv_count = sum(1 for j in last_judgments if j.classification == CommentClassification.low_value)
    uncertain_count = sum(
        1 for j in last_judgments if j.classification == CommentClassification.uncertain
    )
    total = len(result.comments)
    snr = (tp_count + novel_count) / total if total > 0 else 0.0

    # Compute weighted_signal and actionability_rate from severity/actionability
    scoring_cfg = default_scoring()
    sev_w = scoring_cfg.severity_weights
    act_w = scoring_cfg.actionability_weights
    weighted_signal = 0.0
    actionable_count = 0
    tp_total = tp_count + novel_count
    for j in last_judgments:
        if j.classification in (
            CommentClassification.tp_expected,
            CommentClassification.tp_novel,
        ):
            sw = sev_w.get(j.severity or "", 0)
            aw = act_w.get(j.actionability or "", 0.0)
            weighted_signal += sw * aw
            if j.actionability == "actionable":
                actionable_count += 1
    actionability_rate = actionable_count / tp_total if tp_total > 0 else 0.0

    n_votes_cast = len(votes)
    vote_agreement = sum(1 for v in votes if v == score) / n_votes_cast if n_votes_cast > 0 else 0.0

    reasoning = (
        winning_vote[1]
        if winning_vote and winning_vote[1]
        else f"Votes: {votes}. Majority: {score}."
    )
    if parse_failures:
        reasoning += f" ({parse_failures}/{n_votes_cast} votes failed to parse.)"

    return JudgeScore(
        test_case_id=case.id,
        tool=result.tool,
        score=score,
        votes=votes,
        reasoning=reasoning,
        comment_judgments=last_judgments,
        noise=NoiseStats(
            total_comments=total,
            true_positives=tp_count,
            novel_findings=novel_count,
            false_positives=fp_count,
            low_value=lv_count,
            uncertain=uncertain_count,
            snr=snr,
            weighted_signal=weighted_signal,
            actionability_rate=actionability_rate,
        ),
        vote_agreement=vote_agreement,
    )


def _score_one(
    path: Path,
    result: NormalizedResult,
    cases: dict[str, TestCase],
    scores_dir: Path,
    system_prompt: str,
    api_client: Anthropic | None,
    judge_kwargs: dict[str, Any],
    patches_dir: Path | None = None,
) -> bool:
    """Score a single result. Returns True if scored, False if skipped."""
    case = cases.get(result.test_case_id)
    if case is None:
        click.echo(f"[skip] {path.name}: case '{result.test_case_id}' not found")
        return False

    diff_content = ""
    if patches_dir is not None:
        patch_file = patches_dir / f"{result.test_case_id}.patch"
        if patch_file.exists():
            diff_content = patch_file.read_text()

    click.echo(f"[judging] {path.stem}")
    score = judge_case(
        case,
        result,
        system_prompt=system_prompt,
        client=api_client,
        diff_content=diff_content,
        **judge_kwargs,
    )
    out = scores_dir / path.name
    out.write_text(yaml.safe_dump(score.model_dump(mode="json"), sort_keys=False))
    click.echo(f"[score={score.score}] {path.stem}")
    return True


def judge_normalized_results(
    run_dir: Path,
    cases_dir: Path,
    dry_run: bool = False,
    model: str | None = None,
    tools_filter: str | None = None,
    judges: list[str] | None = None,
    max_concurrent: int = 1,
    patches_dir: Path | None = None,
) -> int:
    """Judge all normalized results in run_dir. Returns count of results scored.

    Args:
        run_dir: Directory containing normalized result YAML files.
        cases_dir: Directory containing test case YAML definitions.
        dry_run: If True, skip LLM API calls and assign score 0 to every result.
            Score YAML files are still written to scores/.
        model: Override the judge model (defaults to claude-opus-4-6 inside judge_case).
        tools_filter: Comma-separated tool names to judge; all tools are judged if None.
        judges: Explicit judge runner names (e.g. ``["claude-cli-sonnet",
            "gemini-2.5-flash"]``).  Overrides config ``judging.models``.
        max_concurrent: Number of cases to judge in parallel (default: 1 = sequential).
        patches_dir: Directory containing .patch files (keyed by test_case_id).
            When set, diff content is included in the judge prompt.
    """
    cases = {c.id: c for c in load_cases(cases_dir)}
    system_prompt = load_judge_prompt()

    # Find normalized result files: *.yaml excluding checkpoint.yaml
    candidate_files = [p for p in run_dir.glob("*.yaml") if p.name != "checkpoint.yaml"]

    # Parse candidates; skip files that aren't valid NormalizedResult YAML
    parsed: list[tuple[Path, NormalizedResult]] = []
    for p in candidate_files:
        data = yaml.safe_load(p.read_text()) or {}
        try:
            parsed.append((p, NormalizedResult(**data)))
        except (yaml.YAMLError, ValueError) as e:
            click.echo(f"[skip] {p.name}: {e}", err=True)

    if not parsed:
        click.echo(f"No normalized results found in {run_dir}")
        return 0

    if tools_filter:
        names = {n.strip() for n in tools_filter.split(",")}
        parsed = [(p, r) for p, r in parsed if r.tool in names]

    scores_dir = run_dir / "scores"
    scores_dir.mkdir(exist_ok=True)

    judge_kwargs: dict[str, Any] = {"dry_run": dry_run}
    if judges is not None:
        judge_kwargs["judges"] = judges
    if model is not None:
        judge_kwargs["model"] = model

    # Defer Anthropic client creation — only needed when ensemble includes Anthropic models.
    # Creating eagerly crashes if ANTHROPIC_API_KEY is unset, even for all-Gemini ensembles.
    resolved_judges = judges if judges is not None else (default_judging().models or [])
    _needs_anthropic = not dry_run and any(
        resolve_judge_runner(j)[0] == "api" and not _is_google_model(j) and not _is_openai_model(j)
        for j in (resolved_judges or [model or default_judging().model])
    )
    api_client = Anthropic() if _needs_anthropic else None

    ordered = sorted(parsed, key=lambda x: x[0])
    if max_concurrent > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futs = [
                executor.submit(
                    _score_one,
                    p,
                    r,
                    cases,
                    scores_dir,
                    system_prompt,
                    api_client,
                    judge_kwargs,
                    patches_dir,
                )
                for p, r in ordered
            ]
            count = sum(1 for f in concurrent.futures.as_completed(futs) if f.result())
    else:
        count = 0
        for path, result in ordered:
            if _score_one(
                path,
                result,
                cases,
                scores_dir,
                system_prompt,
                api_client,
                judge_kwargs,
                patches_dir,
            ):
                count += 1

    click.echo(f"Scores written to {scores_dir}/")
    return count


@click.command("judge")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Path to run output directory (must contain normalized *.yaml files)",
)
@click.option(
    "--cases-dir",
    default="cases/",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Directory containing case YAML files",
)
@click.option("--tools", "tools_filter", default=None, help="Comma-separated tool names")
@click.option("--dry-run", is_flag=True, default=False, help="Skip API calls; score everything 0")
@click.option(
    "--judges",
    default=None,
    help=(
        "Comma-separated judge runners (e.g. 'claude-cli-sonnet,gemini-2.5-flash')."
        " CLI runners use the CLI subprocess; bare model names use the provider API."
        " Overrides config judging.models when set."
    ),
)
@click.option(
    "--max-concurrent",
    default=1,
    show_default=True,
    type=int,
    help="Number of cases to judge in parallel",
)
@click.option(
    "--patches-dir",
    default="patches/",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Directory containing .patch files for diff context",
)
def judge(
    run_dir: str,
    cases_dir: str,
    tools_filter: str | None,
    dry_run: bool,
    judges: str | None,
    max_concurrent: int,
    patches_dir: str,
) -> None:
    """Run LLM-as-judge (3× majority vote) on normalized results."""
    judges_list = [j.strip() for j in judges.split(",")] if judges else None
    patches_path = Path(patches_dir) if patches_dir else None
    count = judge_normalized_results(
        Path(run_dir),
        Path(cases_dir),
        dry_run,
        None,
        tools_filter,
        judges_list,
        max_concurrent,
        patches_path,
    )
    click.echo(f"Judged {count} result(s).")
