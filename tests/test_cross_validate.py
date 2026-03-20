"""Tests for cross_validate module."""

from __future__ import annotations

from unittest.mock import patch

from bugeval.cross_validate import _parse_verdicts, cross_validate_case
from bugeval.models import ExpectedFinding
from tests.conftest import make_case


def test_parse_verdicts_valid_json() -> None:
    case = make_case(expected_findings=[
        ExpectedFinding(file="a.rs", line=10, summary="off by one"),
        ExpectedFinding(file="b.rs", line=20, summary="null deref"),
    ])
    text = (
        '{"verdicts": ['
        '{"index": 0, "verdict": "confirmed", "reason": "visible in diff"}, '
        '{"index": 1, "verdict": "disputed", "reason": "not visible"}'
        ']}'
    )
    results = _parse_verdicts(text, case)
    assert len(results) == 2
    assert results[0]["verdict"] == "confirmed"
    assert results[1]["verdict"] == "disputed"


def test_parse_verdicts_fenced_json() -> None:
    case = make_case(expected_findings=[
        ExpectedFinding(file="a.rs", line=10, summary="bug"),
    ])
    text = (
        '```json\n'
        '{"verdicts": [{"index": 0, "verdict": "confirmed", '
        '"reason": "ok"}]}\n'
        '```'
    )
    results = _parse_verdicts(text, case)
    assert results[0]["verdict"] == "confirmed"


def test_parse_verdicts_invalid_response() -> None:
    case = make_case(expected_findings=[
        ExpectedFinding(file="a.rs", line=10, summary="bug"),
    ])
    results = _parse_verdicts("I cannot evaluate this.", case)
    assert len(results) == 1
    assert results[0]["verdict"] == "ambiguous"


def test_parse_verdicts_missing_index() -> None:
    case = make_case(expected_findings=[
        ExpectedFinding(file="a.rs", line=10, summary="bug1"),
        ExpectedFinding(file="b.rs", line=20, summary="bug2"),
    ])
    text = (
        '{"verdicts": ['
        '{"index": 0, "verdict": "confirmed", "reason": "ok"}'
        ']}'
    )
    results = _parse_verdicts(text, case)
    assert results[0]["verdict"] == "confirmed"
    assert results[1]["verdict"] == "ambiguous"


def test_parse_verdicts_invalid_verdict_value() -> None:
    case = make_case(expected_findings=[
        ExpectedFinding(file="a.rs", line=10, summary="bug"),
    ])
    text = (
        '{"verdicts": ['
        '{"index": 0, "verdict": "maybe", "reason": "unsure"}'
        ']}'
    )
    results = _parse_verdicts(text, case)
    assert results[0]["verdict"] == "ambiguous"


def test_cross_validate_case_calls_model() -> None:
    case = make_case()
    with patch("bugeval.cross_validate._call_model") as mock:
        mock.return_value = (
            '{"verdicts": ['
            '{"index": 0, "verdict": "confirmed", "reason": "ok"}'
            ']}'
        )
        results = cross_validate_case(
            case, "diff content", model="gemini-2.5-pro"
        )
    assert len(results) == 1
    assert results[0]["verdict"] == "confirmed"
    mock.assert_called_once()


def test_cross_validate_help() -> None:
    from click.testing import CliRunner

    from bugeval.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["cross-validate", "--help"])
    assert result.exit_code == 0
    assert "--model" in result.output
    assert "--sample-rate" in result.output
