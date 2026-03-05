# tests/test_pipeline.py
"""Tests for the pipeline command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from bugeval.cli import cli


def _make_config(tmp_path: Path, tool_type: str = "api") -> Path:
    """Write a minimal config.yaml and return its path."""
    config_data = {
        "github": {"eval_org": "provable-eval"},
        "tools": [{"name": "greptile", "type": tool_type, "cooldown_seconds": 0}],
        "repos": {},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config_data))
    return config_path


def _make_raw_findings(tmp_path: Path, case_id: str = "case-001", tool: str = "greptile") -> Path:
    """Create a raw findings directory with a minimal findings.json."""
    raw_dir = tmp_path / "raw" / f"{case_id}-{tool}"
    raw_dir.mkdir(parents=True)
    findings = [{"path": "src/main.rs", "line": 10, "body": "possible bug"}]
    (raw_dir / "findings.json").write_text(json.dumps(findings))
    return raw_dir


def _make_cases_dir(tmp_path: Path) -> Path:
    """Create a minimal cases dir with a valid TestCase YAML."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case_data = {
        "id": "case-001",
        "repo": "org/repo",
        "base_commit": "abc",
        "head_commit": "def",
        "fix_commit": "ghi",
        "category": "logic",
        "difficulty": "medium",
        "severity": "high",
        "language": "rust",
        "pr_size": "small",
        "description": "test case",
        "expected_findings": [{"file": "src/main.rs", "line": 10, "summary": "bug"}],
    }
    (cases_dir / "case-001.yaml").write_text(yaml.safe_dump(case_data))
    return cases_dir


def test_pipeline_help() -> None:
    """--help exits 0 and shows all options."""
    runner = CliRunner()
    result = runner.invoke(cli, ["pipeline", "--help"])
    assert result.exit_code == 0
    assert "--run-dir" in result.output
    assert "--config" in result.output
    assert "--cases-dir" in result.output
    assert "--context-level" in result.output
    assert "--no-charts" in result.output
    assert "--dry-run" in result.output


def test_pipeline_dry_run_no_output_files(tmp_path: Path) -> None:
    """In dry-run mode, no normalized YAML files are written at run_dir root."""
    config_path = _make_config(tmp_path)
    _make_raw_findings(tmp_path)
    cases_dir = _make_cases_dir(tmp_path)

    # Patch judge so it doesn't need LLM
    with patch("bugeval.pipeline.judge_normalized_results", return_value=0) as mock_judge:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pipeline",
                "--run-dir",
                str(tmp_path),
                "--config",
                str(config_path),
                "--cases-dir",
                str(cases_dir),
                "--dry-run",
            ],
        )

    assert result.exit_code == 0, result.output
    # No normalized *.yaml at run_dir root
    assert not (tmp_path / "case-001-greptile.yaml").exists()
    # dry_run=True was passed to judge
    mock_judge.assert_called_once()
    call_kwargs = mock_judge.call_args
    assert call_kwargs.args[2] is True or call_kwargs.kwargs.get("dry_run") is True


def test_pipeline_normalize_stage(tmp_path: Path) -> None:
    """The normalize stage writes a YAML file for each raw dir."""
    config_path = _make_config(tmp_path)
    _make_raw_findings(tmp_path)
    cases_dir = _make_cases_dir(tmp_path)

    with patch("bugeval.pipeline.judge_normalized_results", return_value=0):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pipeline",
                "--run-dir",
                str(tmp_path),
                "--config",
                str(config_path),
                "--cases-dir",
                str(cases_dir),
            ],
        )

    assert result.exit_code == 0, result.output
    normalized = tmp_path / "case-001-greptile.yaml"
    assert normalized.exists(), "Normalized YAML should have been written"
    data = yaml.safe_load(normalized.read_text())
    assert data["tool"] == "greptile"
    assert data["test_case_id"] == "case-001"


def test_pipeline_skips_analyze_if_no_normalized(tmp_path: Path) -> None:
    """Empty run dir (no raw/ dirs) exits 0 without crashing."""
    config_path = _make_config(tmp_path)
    cases_dir = _make_cases_dir(tmp_path)

    with patch("bugeval.pipeline.judge_normalized_results", return_value=0):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pipeline",
                "--run-dir",
                str(tmp_path),
                "--config",
                str(config_path),
                "--cases-dir",
                str(cases_dir),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Pipeline complete." in result.output


def test_pipeline_full_chain(tmp_path: Path) -> None:
    """Full pipeline runs all three stages without crashing."""
    config_path = _make_config(tmp_path)
    _make_raw_findings(tmp_path)
    cases_dir = _make_cases_dir(tmp_path)

    # Provide a score file so analyze has something to work with
    scores_dir = tmp_path / "scores"
    scores_dir.mkdir()
    score_data = {
        "test_case_id": "case-001",
        "tool": "greptile",
        "score": 2,
        "votes": [2, 2, 2],
        "reasoning": "test",
        "noise": {"total_comments": 1, "true_positives": 1, "snr": 1.0},
        "comment_judgments": [],
    }
    (scores_dir / "case-001-greptile.yaml").write_text(yaml.safe_dump(score_data))

    with patch("bugeval.pipeline.judge_normalized_results", return_value=1):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pipeline",
                "--run-dir",
                str(tmp_path),
                "--config",
                str(config_path),
                "--cases-dir",
                str(cases_dir),
                "--no-charts",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Stage 1: normalize" in result.output
    assert "Stage 2: judge" in result.output
    assert "Stage 3: analyze" in result.output
    assert "Pipeline complete." in result.output
    # Analysis output should exist
    assert (tmp_path / "analysis" / "report.md").exists()
    assert (tmp_path / "analysis" / "scores.csv").exists()


def test_pipeline_no_charts_flag() -> None:
    """The --no-charts option exists in --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["pipeline", "--help"])
    assert result.exit_code == 0
    assert "--no-charts" in result.output
