"""Tests for gen_introducing_cases module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bugeval.gen_introducing_cases import _rephrase_finding, generate_introducing_case
from bugeval.models import ExpectedFinding
from tests.conftest import make_case


def test_rephrase_finding_adds_prefix() -> None:
    f = ExpectedFinding(file="a.rs", line=10, summary="Off-by-one in loop")
    rephrased = _rephrase_finding(f)
    assert "introduces" in rephrased.summary.lower()
    assert rephrased.line_side == "post_fix"


def test_rephrase_finding_preserves_existing_prefix() -> None:
    f = ExpectedFinding(file="a.rs", line=10, summary="This change breaks the loop")
    rephrased = _rephrase_finding(f)
    assert "This change breaks the loop" in rephrased.summary


def test_generate_introducing_case_basic() -> None:
    fix_case = make_case(id="leo-001")
    with patch("bugeval.gen_introducing_cases.run_git") as mock_git:
        mock_git.return_value = "parentsha123" + "0" * 28
        result = generate_introducing_case(fix_case, "introducing123" + "0" * 27, cwd=Path("/tmp"))
    assert result is not None
    assert result.case_type == "introducing"
    assert result.id == "leo-intro-001"
    assert len(result.expected_findings) == len(fix_case.expected_findings)
    assert result.fix_commit == fix_case.fix_commit


def test_generate_introducing_case_git_failure() -> None:
    from bugeval.git_utils import GitError

    fix_case = make_case(id="leo-002")
    with patch("bugeval.gen_introducing_cases.run_git") as mock_git:
        mock_git.side_effect = GitError(["git"], "failed")
        result = generate_introducing_case(fix_case, "abc123" + "0" * 34, cwd=Path("/tmp"))
    assert result is None


def test_gen_introducing_cases_help() -> None:
    from click.testing import CliRunner

    from bugeval.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["gen-introducing-cases", "--help"])
    assert result.exit_code == 0
    assert "--repo-dir" in result.output
    assert "--dry-run" in result.output
