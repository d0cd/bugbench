"""Tests for status command."""

import json
from pathlib import Path

from click.testing import CliRunner

from bugeval.cli import cli
from bugeval.status_cmd import get_run_status


def test_status_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--help"])
    assert result.exit_code == 0
    assert "--run-dir" in result.output


def test_status_empty_run_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--run-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "0/0 done" in result.output
    assert "Normalized:  0" in result.output


def test_status_with_raw_dirs(tmp_path: Path) -> None:
    """Status counts done/failed from raw/ directory contents."""
    raw_dir = tmp_path / "raw"
    # 2 done cases (with metadata.json)
    for name in ("c1-tool-diff-only", "c2-tool-diff-only"):
        d = raw_dir / name
        d.mkdir(parents=True)
        (d / "metadata.json").write_text(json.dumps({"context_level": "diff-only"}))
    # 1 failed case (with error.json only)
    d = raw_dir / "c3-tool-diff-only"
    d.mkdir(parents=True)
    (d / "error.json").write_text(json.dumps({"error": "patch not found"}))

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--run-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "2/3 done" in result.output
    assert "1 failed" in result.output


def test_status_counts_normalized_yaml(tmp_path: Path) -> None:
    # Write 3 normalized YAMLs + 1 checkpoint (should NOT be counted)
    (tmp_path / "checkpoint.yaml").write_text("pairs: {}")
    for i in range(3):
        (tmp_path / f"norm-{i}.yaml").write_text("score: 0")
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--run-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Normalized:  3" in result.output


def test_status_counts_scores(tmp_path: Path) -> None:
    scores_dir = tmp_path / "scores"
    scores_dir.mkdir()
    (scores_dir / "c1-tool.yaml").write_text("score: 2")
    (scores_dir / "c2-tool.yaml").write_text("score: 3")
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--run-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Scored:      2" in result.output


def test_status_shows_analysis_when_present(tmp_path: Path) -> None:
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "report.md").write_text("# Report")
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--run-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Analysis:    yes" in result.output


def test_get_run_status_returns_dict(tmp_path: Path) -> None:
    info = get_run_status(tmp_path)
    assert info["total"] == 0
    assert not info["has_analysis"]
    assert info["normalized"] == 0


def test_status_pr_done_via_comments_json(tmp_path: Path) -> None:
    """PR tools write comments.json — should count as done."""
    raw_dir = tmp_path / "raw" / "case-001-coderabbit"
    raw_dir.mkdir(parents=True)
    (raw_dir / "comments.json").write_text(json.dumps([]))
    info = get_run_status(tmp_path)
    assert info["done"] == 1
