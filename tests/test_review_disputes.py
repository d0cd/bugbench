"""Tests for review_disputes module."""

from __future__ import annotations

from pathlib import Path

import yaml

from bugeval.io import load_case, save_case
from bugeval.models import ExpectedFinding
from bugeval.review_disputes import (
    apply_dispute_decisions,
    export_disputes,
    read_disputes_csv,
    write_disputes_csv,
)
from tests.conftest import make_case


class TestExportDisputes:
    def test_collects_disputed_findings(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        data = {
            "case_id": "case-001",
            "model": "gemini-2.5-pro",
            "verdicts": [
                {
                    "index": 0,
                    "finding_summary": "bug1",
                    "verdict": "confirmed",
                    "reason": "ok",
                },
                {
                    "index": 1,
                    "finding_summary": "bug2",
                    "verdict": "disputed",
                    "reason": "not real",
                },
            ],
        }
        (results_dir / "case-001.yaml").write_text(
            yaml.safe_dump(data)
        )
        rows = export_disputes(results_dir)
        assert len(rows) == 1
        assert rows[0]["case_id"] == "case-001"
        assert rows[0]["finding_index"] == "1"
        assert rows[0]["model_reason"] == "not real"

    def test_empty_dir(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        assert export_disputes(results_dir) == []


class TestCsvRoundTrip:
    def test_write_and_read(self, tmp_path: Path) -> None:
        rows = [
            {
                "case_id": "case-001",
                "finding_index": "0",
                "finding_summary": "bug",
                "model_reason": "not real",
                "human_decision": "keep",
                "updated_summary": "",
            }
        ]
        csv_path = tmp_path / "disputes.csv"
        write_disputes_csv(rows, csv_path)
        loaded = read_disputes_csv(csv_path)
        assert len(loaded) == 1
        assert loaded[0]["case_id"] == "case-001"
        assert loaded[0]["human_decision"] == "keep"


class TestApplyDisputeDecisions:
    def test_keep_marks_verified(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        case = make_case(id="case-001")
        save_case(case, cases_dir / "case-001.yaml")

        rows = [{
            "case_id": "case-001", "finding_index": "0",
            "finding_summary": "bug", "model_reason": "",
            "human_decision": "keep", "updated_summary": "",
        }]
        updated, removed, skipped = apply_dispute_decisions(
            rows, cases_dir
        )
        assert updated == 1
        assert removed == 0

        reloaded = load_case(cases_dir / "case-001.yaml")
        assert reloaded.verified is True

    def test_fix_updates_summary(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        case = make_case(id="case-002", expected_findings=[
            ExpectedFinding(
                file="a.rs", line=10, summary="old summary"
            ),
        ])
        save_case(case, cases_dir / "case-002.yaml")

        rows = [{
            "case_id": "case-002", "finding_index": "0",
            "finding_summary": "old summary", "model_reason": "",
            "human_decision": "fix", "updated_summary": "new summary",
        }]
        updated, removed, skipped = apply_dispute_decisions(
            rows, cases_dir
        )
        assert updated == 1

        reloaded = load_case(cases_dir / "case-002.yaml")
        assert reloaded.expected_findings[0].summary == "new summary"

    def test_remove_deletes_finding(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        case = make_case(id="case-003", expected_findings=[
            ExpectedFinding(file="a.rs", line=10, summary="bug1"),
            ExpectedFinding(file="b.rs", line=20, summary="bug2"),
        ])
        save_case(case, cases_dir / "case-003.yaml")

        rows = [{
            "case_id": "case-003", "finding_index": "0",
            "finding_summary": "bug1", "model_reason": "",
            "human_decision": "remove", "updated_summary": "",
        }]
        updated, removed, skipped = apply_dispute_decisions(
            rows, cases_dir
        )
        assert removed == 1

        reloaded = load_case(cases_dir / "case-003.yaml")
        assert len(reloaded.expected_findings) == 1
        assert reloaded.expected_findings[0].summary == "bug2"

    def test_invalidate_marks_invalid(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        case = make_case(id="case-004")
        save_case(case, cases_dir / "case-004.yaml")

        rows = [{
            "case_id": "case-004", "finding_index": "0",
            "finding_summary": "bug", "model_reason": "",
            "human_decision": "invalidate", "updated_summary": "",
        }]
        updated, removed, skipped = apply_dispute_decisions(
            rows, cases_dir
        )
        assert removed == 1

        reloaded = load_case(cases_dir / "case-004.yaml")
        assert reloaded.valid_for_code_review is False

    def test_empty_decision_skipped(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        case = make_case(id="case-005")
        save_case(case, cases_dir / "case-005.yaml")

        rows = [{
            "case_id": "case-005", "finding_index": "0",
            "finding_summary": "bug", "model_reason": "",
            "human_decision": "", "updated_summary": "",
        }]
        updated, removed, skipped = apply_dispute_decisions(
            rows, cases_dir
        )
        assert skipped == 1

    def test_missing_case_skipped(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        rows = [{
            "case_id": "nonexistent", "finding_index": "0",
            "finding_summary": "bug", "model_reason": "",
            "human_decision": "keep", "updated_summary": "",
        }]
        updated, removed, skipped = apply_dispute_decisions(
            rows, cases_dir
        )
        assert skipped == 1


def test_review_disputes_help() -> None:
    from click.testing import CliRunner

    from bugeval.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["review-disputes", "--help"])
    assert result.exit_code == 0
    assert "export" in result.output
    assert "import" in result.output
