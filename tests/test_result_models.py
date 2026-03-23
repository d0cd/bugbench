"""Tests for result models."""

from __future__ import annotations

from bugeval.result_models import Comment, ToolResult


class TestComment:
    def test_defaults(self) -> None:
        c = Comment()
        assert c.file == ""
        assert c.line == 0
        assert c.body == ""
        assert c.suggested_fix == ""

    def test_with_data(self) -> None:
        c = Comment(file="x.rs", line=10, body="Bug here", suggested_fix="fix it")
        assert c.file == "x.rs"


class TestToolResult:
    def test_minimal(self) -> None:
        r = ToolResult(case_id="t-001", tool="copilot")
        assert r.comments == []
        assert r.error == ""
        assert r.pr_number == 0
        assert not r.potentially_contaminated

    def test_full(self, sample_result: ToolResult) -> None:
        assert sample_result.case_id == "snarkVM-001"
        assert len(sample_result.comments) == 2
        assert sample_result.time_seconds == 45.2

    def test_pr_number(self) -> None:
        r = ToolResult(case_id="t-002", tool="copilot", pr_number=42)
        assert r.pr_number == 42
        data = r.model_dump(mode="json")
        assert data["pr_number"] == 42

    def test_pr_number_round_trip(self) -> None:
        r = ToolResult(case_id="t-003", tool="greptile", pr_number=99)
        rebuilt = ToolResult.model_validate(r.model_dump(mode="json"))
        assert rebuilt.pr_number == 99

    def test_pr_state_field(self) -> None:
        r = ToolResult(case_id="t-004", tool="copilot", pr_state="pending-review")
        assert r.pr_state == "pending-review"
        data = r.model_dump(mode="json")
        assert data["pr_state"] == "pending-review"

    def test_pr_state_default(self) -> None:
        r = ToolResult(case_id="t-005", tool="copilot")
        assert r.pr_state == ""

    def test_pr_branch_fields(self) -> None:
        r = ToolResult(
            case_id="t-006",
            tool="greptile",
            pr_head_branch="bugeval/snarkVM-001",
            pr_base_branch="testnet-beta",
        )
        assert r.pr_head_branch == "bugeval/snarkVM-001"
        assert r.pr_base_branch == "testnet-beta"
        rebuilt = ToolResult.model_validate(r.model_dump(mode="json"))
        assert rebuilt.pr_head_branch == "bugeval/snarkVM-001"
        assert rebuilt.pr_base_branch == "testnet-beta"
