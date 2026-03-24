"""Integration test: evaluate (dry-run) -> score (dry-run) -> analyze."""

from __future__ import annotations

import json
from pathlib import Path

from bugeval.analyze import run_analysis
from bugeval.io import save_case, save_result
from bugeval.models import BuggyLine, GroundTruth, TestCase
from bugeval.result_models import Comment, ToolResult
from bugeval.score import score_run


def _make_bug_case(case_id: str = "test-001") -> TestCase:
    """Create a minimal bug case with ground truth."""
    return TestCase(
        id=case_id,
        repo="ProvableHQ/leo",
        base_commit="def456",
        fix_commit="abc123",
        fix_pr_number=1,
        fix_pr_title="Fix parser bug",
        kind="bug",
        status="active",
        truth=GroundTruth(
            buggy_lines=[BuggyLine(file="src/parser.rs", line=42)],
            blame_confidence="A",
        ),
    )


def _make_result(case_id: str = "test-001", tool: str = "agent") -> ToolResult:
    """Create a result with a comment near the buggy line."""
    return ToolResult(
        case_id=case_id,
        tool=tool,
        context_level="diff-only",
        comments=[
            Comment(file="src/parser.rs", line=44, body="Potential off-by-one error in parser"),
        ],
        time_seconds=5.0,
        cost_usd=0.10,
    )


class TestPipelineChain:
    def test_evaluate_score_analyze(self, tmp_path: Path) -> None:
        """Full pipeline: pre-seeded results -> score -> analyze."""
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        results_dir = run_dir / "results"
        results_dir.mkdir()

        # Setup: write case and pre-seeded result
        case = _make_bug_case()
        save_case(case, cases_dir / "test-001.yaml")
        result = _make_result()
        save_result(result, results_dir / "test-001__agent__diff-only.yaml")

        # Step 1: Score (dry-run = mechanical only, no LLM)
        score_run(run_dir, cases_dir, dry_run=True)
        scores_dir = run_dir / "scores"
        assert scores_dir.exists()
        score_files = list(scores_dir.glob("*.yaml"))
        assert len(score_files) >= 1

        # Step 2: Analyze
        run_analysis(run_dir, cases_dir, no_charts=True)
        assert (run_dir / "comparison.csv").exists()


class TestCheckpointResume:
    def test_score_checkpoint_skips_completed(self, tmp_path: Path) -> None:
        """Score run with existing checkpoint skips already-scored cases."""
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        results_dir = run_dir / "results"
        results_dir.mkdir()
        scores_dir = run_dir / "scores"
        scores_dir.mkdir()

        case = _make_bug_case()
        save_case(case, cases_dir / "test-001.yaml")
        result = _make_result()
        save_result(result, results_dir / "test-001__agent__diff-only.yaml")

        # Pre-seed checkpoint with the key that score_run would generate
        checkpoint_path = scores_dir / "checkpoint.json"
        checkpoint_path.write_text(json.dumps(["test-001__agent"]))

        # Run score — should skip test-001 since it's in the checkpoint
        score_run(run_dir, cases_dir, dry_run=True)

        # Verify checkpoint still has exactly 1 entry (no new entries added)
        cp = json.loads(checkpoint_path.read_text())
        assert len(cp) == 1
