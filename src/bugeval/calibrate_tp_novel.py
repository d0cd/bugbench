"""TP-novel calibration: measure judge accuracy on novel finding classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import click
import yaml

from bugeval.judge import judge_case, load_judge_prompt
from bugeval.judge_models import CommentClassification, JudgeScore
from bugeval.models import TestCase
from bugeval.result_models import Comment, NormalizedResult
from bugeval.run_pr_eval import load_cases


@dataclass
class CalibrationResult:
    """Aggregated calibration metrics."""

    recall_total: int = 0
    recall_correct: int = 0
    precision_total: int = 0
    precision_correct: int = 0
    scores: list[JudgeScore] = field(default_factory=list)

    @property
    def recall(self) -> float:
        """TP-novel recall: fraction of real findings correctly classified."""
        return self.recall_correct / self.recall_total if self.recall_total > 0 else 0.0

    @property
    def precision(self) -> float:
        """TP-novel precision: fraction of injected fakes correctly rejected."""
        return self.precision_correct / self.precision_total if self.precision_total > 0 else 0.0


def build_recall_case(
    case: TestCase,
    reviewer_finding: str,
    file: str = "unknown",
    line: int = 1,
) -> NormalizedResult:
    """Build a synthetic tool output that includes a known real finding."""
    return NormalizedResult(
        test_case_id=case.id,
        tool="calibration-recall",
        comments=[Comment(body=reviewer_finding, file=file, line=line)],
    )


def build_precision_case(
    case: TestCase,
    fake_finding: str,
    file: str = "unknown",
    line: int = 1,
) -> NormalizedResult:
    """Build a synthetic tool output with a plausible-but-wrong finding."""
    return NormalizedResult(
        test_case_id=case.id,
        tool="calibration-precision",
        comments=[Comment(body=fake_finding, file=file, line=line)],
    )


def run_recall_test(
    case: TestCase,
    result: NormalizedResult,
    system_prompt: str,
    diff_content: str = "",
    dry_run: bool = False,
) -> tuple[bool, JudgeScore]:
    """Run judge on a synthetic result with a known real finding.

    Returns (correct, score) where correct=True if the judge classified as TP-novel.
    """
    score = judge_case(
        case, result, system_prompt=system_prompt,
        n_votes=1, dry_run=dry_run, diff_content=diff_content,
    )
    if dry_run:
        return False, score
    correct = any(
        j.classification == CommentClassification.tp_novel
        for j in score.comment_judgments
    )
    return correct, score


def run_precision_test(
    case: TestCase,
    result: NormalizedResult,
    system_prompt: str,
    diff_content: str = "",
    dry_run: bool = False,
) -> tuple[bool, JudgeScore]:
    """Run judge on a synthetic result with a fake finding.

    Returns (correct, score) where correct=True if the judge classified as FP (not TP-novel).
    """
    score = judge_case(
        case, result, system_prompt=system_prompt,
        n_votes=1, dry_run=dry_run, diff_content=diff_content,
    )
    if dry_run:
        return False, score
    correct = not any(
        j.classification == CommentClassification.tp_novel
        for j in score.comment_judgments
    )
    return correct, score


@click.command("calibrate-tp-novel")
@click.option("--cases-dir", required=True, type=click.Path(exists=True), help="Cases directory")
@click.option("--patches-dir", default="patches/", show_default=True, type=click.Path())
@click.option("--limit", default=50, show_default=True, type=int, help="Cases per test")
@click.option("--output", default=None, type=click.Path(), help="Output YAML path for results")
@click.option("--dry-run", is_flag=True, default=False, help="Skip API calls")
def calibrate_tp_novel(
    cases_dir: str,
    patches_dir: str,
    limit: int,
    output: str | None,
    dry_run: bool,
) -> None:
    """Measure judge accuracy on TP-novel classification."""
    cases = load_cases(Path(cases_dir))
    # Only use cases with reviewer_findings for recall test
    recall_cases = [c for c in cases if c.reviewer_findings][:limit]
    # Use all valid cases for precision test
    precision_cases = [c for c in cases if c.expected_findings][:limit]

    system_prompt = load_judge_prompt()
    patches = Path(patches_dir)
    result = CalibrationResult()

    click.echo(f"Recall test: {len(recall_cases)} cases with reviewer findings")
    for case in recall_cases:
        if not case.reviewer_findings:
            continue
        rf = case.reviewer_findings[0]
        synth = build_recall_case(case, rf.summary, rf.file, rf.line)
        diff = ""
        pf = patches / f"{case.id}.patch"
        if pf.exists():
            diff = pf.read_text()
        correct, score = run_recall_test(
            case, synth, system_prompt, diff_content=diff, dry_run=dry_run,
        )
        result.recall_total += 1
        if correct:
            result.recall_correct += 1
        result.scores.append(score)
        click.echo(f"  [{'ok' if correct else 'miss'}] {case.id}")

    click.echo(f"\nPrecision test: {len(precision_cases)} cases with fake findings")
    for case in precision_cases:
        # Inject a plausible but wrong finding
        ef = case.expected_findings[0]
        fake = f"Potential null check missing on line {ef.line + 50} in {ef.file}"
        synth = build_precision_case(case, fake, ef.file, ef.line + 50)
        diff = ""
        pf = patches / f"{case.id}.patch"
        if pf.exists():
            diff = pf.read_text()
        correct, score = run_precision_test(
            case, synth, system_prompt, diff_content=diff, dry_run=dry_run,
        )
        result.precision_total += 1
        if correct:
            result.precision_correct += 1
        result.scores.append(score)
        click.echo(f"  [{'ok' if correct else 'fp'}] {case.id}")

    click.echo(
        f"\nTP-novel recall:    {result.recall:.1%}"
        f" ({result.recall_correct}/{result.recall_total})"
    )
    click.echo(
        f"TP-novel precision: {result.precision:.1%}"
        f" ({result.precision_correct}/{result.precision_total})"
    )

    if output:
        out_data = {
            "recall": result.recall,
            "recall_correct": result.recall_correct,
            "recall_total": result.recall_total,
            "precision": result.precision,
            "precision_correct": result.precision_correct,
            "precision_total": result.precision_total,
        }
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(yaml.safe_dump(out_data, sort_keys=False))
        click.echo(f"Results written to {output}")
