"""Tests for YAML I/O round-trips."""

from pathlib import Path

import pytest

from bugeval.io import load_all_cases, load_candidates, load_case, save_candidates, save_case
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


def make_test_case(id_: str = "test-001") -> TestCase:
    return TestCase(
        id=id_,
        repo="provable-org/aleo-lang",
        base_commit="abc123def456abc123def456abc123def456abc1",
        head_commit="def456abc123def456abc123def456abc123def4",
        fix_commit="ghi789ghi789ghi789ghi789ghi789ghi789ghi7",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.small,
        description="A logic bug in the type checker",
        expected_findings=[ExpectedFinding(file="src/main.rs", line=42, summary="Off-by-one")],
        stats=None,
    )


def make_candidate(pr_number: int = 1) -> Candidate:
    return Candidate(
        repo="provable-org/aleo-lang",
        pr_number=pr_number,
        fix_commit="abc123def456abc123def456abc123def456abc1",
        confidence=0.7,
        signals=["has_bug_label"],
        title="Fix bug",
        body="Fixes #1",
        labels=["bug"],
        files_changed=["src/main.rs"],
        diff_stats=CaseStats(lines_added=5, lines_deleted=3, files_changed=1, hunks=2),
        expected_findings=[],
        language="rust",
        pr_size=PRSize.tiny,
    )


class TestSaveLoadCase:
    def test_round_trip(self, tmp_path: Path) -> None:
        case = make_test_case()
        path = tmp_path / "test-001.yaml"
        save_case(case, path)
        loaded = load_case(path)
        assert loaded == case

    def test_saves_yaml_file(self, tmp_path: Path) -> None:
        case = make_test_case()
        path = tmp_path / "case.yaml"
        save_case(case, path)
        assert path.exists()

    def test_stats_round_trip(self, tmp_path: Path) -> None:
        case = make_test_case()
        case = case.model_copy(
            update={"stats": CaseStats(lines_added=10, lines_deleted=5, files_changed=2, hunks=3)}
        )
        path = tmp_path / "case-stats.yaml"
        save_case(case, path)
        loaded = load_case(path)
        assert loaded.stats is not None
        assert loaded.stats.lines_added == 10

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_case(tmp_path / "nonexistent.yaml")


class TestSaveLoadCandidates:
    def test_round_trip(self, tmp_path: Path) -> None:
        candidates = [make_candidate(1), make_candidate(2)]
        path = tmp_path / "candidates.yaml"
        save_candidates(candidates, path)
        loaded = load_candidates(path)
        assert len(loaded) == 2
        assert loaded[0].pr_number == 1
        assert loaded[1].pr_number == 2

    def test_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        save_candidates([], path)
        loaded = load_candidates(path)
        assert loaded == []

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_candidates(tmp_path / "nonexistent.yaml")


class TestLoadAllCases:
    def test_loads_multiple_files(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        save_case(make_test_case("a-001"), cases_dir / "a-001.yaml")
        save_case(make_test_case("a-002"), cases_dir / "a-002.yaml")
        loaded = load_all_cases(cases_dir)
        assert len(loaded) == 2
        ids = {c.id for c in loaded}
        assert ids == {"a-001", "a-002"}

    def test_empty_dir(self, tmp_path: Path) -> None:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        loaded = load_all_cases(cases_dir)
        assert loaded == []
