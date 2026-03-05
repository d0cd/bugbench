"""Tests for status command."""

from pathlib import Path

import yaml
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


def test_status_with_checkpoint(tmp_path: Path) -> None:
    # Build a minimal checkpoint YAML with 2 done and 1 failed.
    # RunState._key() uses "::" as separator, so keys must match.
    checkpoint_data = {
        "pairs": {
            "c1::t1": {"case_id": "c1", "tool": "t1", "status": "done"},
            "c2::t1": {"case_id": "c2", "tool": "t1", "status": "done"},
            "c3::t1": {"case_id": "c3", "tool": "t1", "status": "failed"},
        }
    }
    (tmp_path / "checkpoint.yaml").write_text(yaml.dump(checkpoint_data))
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
