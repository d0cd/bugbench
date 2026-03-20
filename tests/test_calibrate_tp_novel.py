"""Tests for calibrate_tp_novel module."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from bugeval.calibrate_tp_novel import (
    CalibrationResult,
    build_precision_case,
    build_recall_case,
    run_precision_test,
    run_recall_test,
)
from tests.conftest import make_case


def test_calibration_result_recall() -> None:
    r = CalibrationResult(recall_total=10, recall_correct=8)
    assert r.recall == pytest.approx(0.8)


def test_calibration_result_precision() -> None:
    r = CalibrationResult(precision_total=10, precision_correct=9)
    assert r.precision == pytest.approx(0.9)


def test_calibration_result_empty() -> None:
    r = CalibrationResult()
    assert r.recall == 0.0
    assert r.precision == 0.0


def test_build_recall_case() -> None:
    case = make_case()
    result = build_recall_case(case, "off-by-one in loop", "a.rs", 10)
    assert result.tool == "calibration-recall"
    assert len(result.comments) == 1
    assert result.comments[0].body == "off-by-one in loop"


def test_build_precision_case() -> None:
    case = make_case()
    result = build_precision_case(case, "fake null check issue", "a.rs", 99)
    assert result.tool == "calibration-precision"
    assert result.comments[0].body == "fake null check issue"


def test_run_recall_test_dry_run() -> None:
    case = make_case()
    result = build_recall_case(case, "bug", "a.rs", 10)
    correct, score = run_recall_test(case, result, "prompt", dry_run=True)
    assert not correct
    assert score.score == 0


def test_run_precision_test_dry_run() -> None:
    case = make_case()
    result = build_precision_case(case, "fake", "a.rs", 99)
    correct, score = run_precision_test(case, result, "prompt", dry_run=True)
    assert not correct
    assert score.score == 0


def test_run_recall_test_tp_novel_detected() -> None:
    case = make_case()
    result = build_recall_case(case, "real bug", "a.rs", 10)
    response = json.dumps({
        "score": 0,
        "reasoning": "Found novel issue",
        "comment_judgments": [
            {
                "id": 0, "classification": "TP-novel",
                "severity": "medium", "actionability": "directional",
                "relevance": "direct",
            },
        ],
    })
    with patch("bugeval.judge._call_anthropic_judge", return_value=response):
        correct, score = run_recall_test(case, result, "prompt")
    assert correct


def test_run_precision_test_fp_rejected() -> None:
    case = make_case()
    result = build_precision_case(case, "fake issue", "a.rs", 99)
    response = json.dumps({
        "score": 0,
        "reasoning": "Not real",
        "comment_judgments": [
            {"id": 0, "classification": "FP", "relevance": "unrelated"},
        ],
    })
    with patch("bugeval.judge._call_anthropic_judge", return_value=response):
        correct, score = run_precision_test(case, result, "prompt")
    assert correct


def test_calibrate_tp_novel_help() -> None:
    from click.testing import CliRunner

    from bugeval.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["calibrate-tp-novel", "--help"])
    assert result.exit_code == 0
    assert "--cases-dir" in result.output
    assert "--dry-run" in result.output
