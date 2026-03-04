"""Shared fixtures for the test suite."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from bugeval.models import (
    CaseStats,
    Category,
    Difficulty,
    ExpectedFinding,
    PRSize,
    Severity,
    TestCase,
)


def make_case(**kwargs: Any) -> TestCase:
    """Build a TestCase with sensible defaults. Override any field via kwargs."""
    defaults: dict[str, Any] = {
        "id": "case-001",
        "repo": "provable-org/aleo-lang",
        "base_commit": "abc123",
        "head_commit": "def456",
        "fix_commit": "ghi789",
        "category": Category.logic,
        "difficulty": Difficulty.medium,
        "severity": Severity.high,
        "language": "rust",
        "pr_size": PRSize.small,
        "description": "A test case",
        "expected_findings": [ExpectedFinding(file="src/main.rs", line=42, summary="bug")],
        "stats": CaseStats(lines_added=10, lines_deleted=5, files_changed=1, hunks=1),
    }
    defaults.update(kwargs)
    return TestCase(**defaults)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    return path


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A tiny git repo with two commits (base and head)."""
    repo = _init_repo(tmp_path / "sample_repo")
    (repo / "main.rs").write_text("fn main() {}\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"], cwd=repo, check=True, capture_output=True
    )
    (repo / "main.rs").write_text("fn main() { let _x = 1; }\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "fix: add variable"], cwd=repo, check=True, capture_output=True
    )
    return repo
