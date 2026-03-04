"""Tests for the curate command: prompt construction, response parsing, CLI."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from bugeval.curate import build_curation_prompt, curate, parse_llm_response
from bugeval.io import save_candidates
from bugeval.models import (
    Candidate,
    CaseStats,
    Category,
    Difficulty,
    ExpectedFinding,
    PRSize,
    Severity,
    TestCase,
)


def make_candidate(pr_number: int = 1) -> Candidate:
    return Candidate(
        repo="foo/bar",
        pr_number=pr_number,
        fix_commit="abc123def456abc123def456abc123def456abc1",
        confidence=0.8,
        signals=["has_bug_label", "pr_references_issue"],
        title=f"Fix off-by-one error in parser (PR #{pr_number})",
        body="Fixes #42\n\nThis PR fixes a critical off-by-one error.",
        labels=["bug"],
        files_changed=["src/parser.rs"],
        diff_stats=CaseStats(lines_added=5, lines_deleted=3, files_changed=1, hunks=1),
        expected_findings=[
            ExpectedFinding(file="src/parser.rs", line=42, summary="[auto] off-by-one")
        ],
        language="rust",
        pr_size=PRSize.tiny,
    )


def make_llm_response_data() -> dict[str, object]:
    return {
        "category": "logic",
        "difficulty": "easy",
        "severity": "low",
        "description": "An off-by-one error in the parser causes incorrect range calculations.",
        "expected_findings": [
            {"file": "src/parser.rs", "line": 42, "summary": "off-by-one in range calc"}
        ],
        "head_commit": None,
        "base_commit": None,
        "needs_manual_review": True,
    }


class TestBuildCurationPrompt:
    def test_contains_pr_title(self) -> None:
        candidate = make_candidate()
        prompt = build_curation_prompt(candidate, diff_context="diff here")
        assert candidate.title in prompt

    def test_contains_repo(self) -> None:
        candidate = make_candidate()
        prompt = build_curation_prompt(candidate, diff_context="")
        assert candidate.repo in prompt

    def test_contains_diff(self) -> None:
        candidate = make_candidate()
        prompt = build_curation_prompt(candidate, diff_context="--- a/parser.rs\n+++ b/parser.rs")
        assert "parser.rs" in prompt

    def test_contains_language(self) -> None:
        candidate = make_candidate()
        prompt = build_curation_prompt(candidate, diff_context="")
        assert "rust" in prompt.lower()

    def test_with_git_log(self) -> None:
        candidate = make_candidate()
        prompt = build_curation_prompt(
            candidate, diff_context="", git_log="abc123 introduce parser bug"
        )
        assert "abc123" in prompt

    def test_returns_nonempty_string(self) -> None:
        candidate = make_candidate()
        prompt = build_curation_prompt(candidate, diff_context="")
        assert len(prompt) > 100


class TestParseLlmResponse:
    def test_valid_response_creates_test_case(self) -> None:
        candidate = make_candidate()
        data = make_llm_response_data()
        case = parse_llm_response(data, case_id="bar-001", candidate=candidate)
        assert isinstance(case, TestCase)
        assert case.id == "bar-001"
        assert case.category == Category.logic
        assert case.difficulty == Difficulty.easy
        assert case.severity == Severity.low

    def test_uses_candidate_metadata(self) -> None:
        candidate = make_candidate()
        data = make_llm_response_data()
        case = parse_llm_response(data, case_id="bar-001", candidate=candidate)
        assert case.repo == candidate.repo
        assert case.language == candidate.language
        assert case.fix_commit == candidate.fix_commit

    def test_expected_findings_parsed(self) -> None:
        candidate = make_candidate()
        data = make_llm_response_data()
        case = parse_llm_response(data, case_id="bar-001", candidate=candidate)
        assert len(case.expected_findings) == 1
        assert case.expected_findings[0].file == "src/parser.rs"
        assert case.expected_findings[0].line == 42

    def test_head_base_commit_set(self) -> None:
        candidate = make_candidate()
        data = {
            **make_llm_response_data(),
            "head_commit": "deadbeef" * 5,
            "base_commit": "cafebabe" * 5,
        }
        case = parse_llm_response(data, case_id="bar-002", candidate=candidate)
        assert case.head_commit == "deadbeef" * 5

    def test_empty_findings(self) -> None:
        candidate = make_candidate()
        data = {**make_llm_response_data(), "expected_findings": []}
        case = parse_llm_response(data, case_id="bar-003", candidate=candidate)
        assert case.expected_findings == []

    def test_null_commits_fallback(self) -> None:
        candidate = make_candidate()
        data = make_llm_response_data()  # head_commit: None, base_commit: None
        case = parse_llm_response(data, case_id="bar-004", candidate=candidate)
        assert case.head_commit == candidate.fix_commit
        assert case.base_commit == f"{candidate.fix_commit}^"


class TestCurateCliHelp:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(curate, ["--help"])
        assert result.exit_code == 0
        assert "--candidates" in result.output
        assert "--dry-run" in result.output
        assert "--output-dir" in result.output


class TestCurateDryRun:
    def test_dry_run_does_not_call_api(self, tmp_path: Path) -> None:
        candidates = [make_candidate(1)]
        candidates_path = tmp_path / "candidates.yaml"
        save_candidates(candidates, candidates_path)

        runner = CliRunner()
        with patch("bugeval.curate.Anthropic") as mock_cls:
            result = runner.invoke(
                curate,
                [
                    "--candidates",
                    str(candidates_path),
                    "--dry-run",
                    "--output-dir",
                    str(tmp_path / "cases"),
                ],
            )
        assert result.exit_code == 0
        mock_cls.assert_not_called()

    def test_dry_run_prints_candidate_info(self, tmp_path: Path) -> None:
        candidate = make_candidate(42)
        candidates_path = tmp_path / "cands.yaml"
        save_candidates([candidate], candidates_path)

        runner = CliRunner()
        with patch("bugeval.curate.Anthropic"):
            result = runner.invoke(
                curate,
                [
                    "--candidates",
                    str(candidates_path),
                    "--dry-run",
                    "--output-dir",
                    str(tmp_path / "cases"),
                ],
            )
        assert result.exit_code == 0
        assert "42" in result.output  # PR number


class TestCurateWithMockedApi:
    def _make_mock_client(self, response_data: dict[str, object]) -> MagicMock:
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(type="thinking", thinking="Analyzing the bug..."),
            MagicMock(type="text", text=json.dumps(response_data)),
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_curate_writes_case_file(self, tmp_path: Path) -> None:
        candidate = make_candidate(1)
        candidates_path = tmp_path / "candidates.yaml"
        save_candidates([candidate], candidates_path)

        mock_client = self._make_mock_client(make_llm_response_data())
        cases_dir = tmp_path / "cases"

        runner = CliRunner()
        with patch("bugeval.curate.Anthropic", return_value=mock_client):
            result = runner.invoke(
                curate,
                [
                    "--candidates",
                    str(candidates_path),
                    "--output-dir",
                    str(cases_dir),
                    "--api-delay",
                    "0",
                ],
            )
        assert result.exit_code == 0
        yaml_files = list(cases_dir.glob("*.yaml"))
        assert len(yaml_files) == 1

    def test_curate_skips_low_confidence(self, tmp_path: Path) -> None:
        candidate = make_candidate(1)
        candidate = candidate.model_copy(update={"confidence": 0.2})
        candidates_path = tmp_path / "candidates.yaml"
        save_candidates([candidate], candidates_path)

        mock_client = self._make_mock_client(make_llm_response_data())
        cases_dir = tmp_path / "cases"

        runner = CliRunner()
        with patch("bugeval.curate.Anthropic", return_value=mock_client):
            result = runner.invoke(
                curate,
                [
                    "--candidates",
                    str(candidates_path),
                    "--output-dir",
                    str(cases_dir),
                    "--min-confidence",
                    "0.5",
                    "--api-delay",
                    "0",
                ],
            )
        assert result.exit_code == 0
        # No cases created since confidence is below threshold
        yaml_files = list(cases_dir.glob("*.yaml"))
        assert len(yaml_files) == 0

    def test_api_called_with_correct_model(self, tmp_path: Path) -> None:
        candidate = make_candidate(1)
        candidates_path = tmp_path / "candidates.yaml"
        save_candidates([candidate], candidates_path)

        mock_client = self._make_mock_client(make_llm_response_data())
        cases_dir = tmp_path / "cases"

        runner = CliRunner()
        with patch("bugeval.curate.Anthropic", return_value=mock_client):
            runner.invoke(
                curate,
                [
                    "--candidates",
                    str(candidates_path),
                    "--output-dir",
                    str(cases_dir),
                    "--api-delay",
                    "0",
                ],
            )
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs["model"] == "claude-opus-4-6"
