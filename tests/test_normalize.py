# tests/test_normalize.py
"""Tests for normalize module."""

from __future__ import annotations

import json
from pathlib import Path

from bugeval.normalize import (
    discover_raw_dirs,
    normalize_agent_result,
    normalize_api_result,
    normalize_pr_result,
)
from bugeval.result_models import CommentType

# --- normalize_pr_result ---


def test_normalize_pr_result_inline_comment(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw" / "case-001-coderabbit"
    raw_dir.mkdir(parents=True)
    comments = [
        {"source": "inline_comment", "body": "Off by one", "path": "a.rs", "line": 42},
    ]
    (raw_dir / "comments.json").write_text(json.dumps(comments))
    result = normalize_pr_result("case-001", "coderabbit", raw_dir)
    assert result.test_case_id == "case-001"
    assert result.tool == "coderabbit"
    assert len(result.comments) == 1
    assert result.comments[0].file == "a.rs"
    assert result.comments[0].line == 42
    assert result.comments[0].type == CommentType.inline


def test_normalize_pr_result_review_comment(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw" / "case-001-coderabbit"
    raw_dir.mkdir(parents=True)
    comments = [
        {"source": "review", "body": "Looks risky overall", "state": "COMMENT"},
    ]
    (raw_dir / "comments.json").write_text(json.dumps(comments))
    result = normalize_pr_result("case-001", "coderabbit", raw_dir)
    assert result.comments[0].type == CommentType.pr_level
    assert result.comments[0].file == ""
    assert result.comments[0].line == 0


def test_normalize_pr_result_empty(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw" / "case-001-coderabbit"
    raw_dir.mkdir(parents=True)
    (raw_dir / "comments.json").write_text("[]")
    result = normalize_pr_result("case-001", "coderabbit", raw_dir)
    assert result.comments == []


# --- normalize_api_result ---


def test_normalize_api_result(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw" / "case-001-greptile"
    raw_dir.mkdir(parents=True)
    findings = [
        {"source": "greptile", "body": "Buffer overread", "path": "src/main.rs", "line": 10},
    ]
    (raw_dir / "findings.json").write_text(json.dumps(findings))
    result = normalize_api_result("case-001", "greptile", "diff-only", raw_dir)
    assert result.comments[0].body == "Buffer overread"
    assert result.comments[0].file == "src/main.rs"
    assert result.context_level == "diff-only"


def test_normalize_api_result_empty(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw" / "case-001-greptile"
    raw_dir.mkdir(parents=True)
    (raw_dir / "findings.json").write_text("[]")
    result = normalize_api_result("case-001", "greptile", "diff-only", raw_dir)
    assert result.comments == []


# --- normalize_agent_result ---


def test_normalize_agent_result(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw" / "case-001-anthropic-api"
    raw_dir.mkdir(parents=True)
    findings = [{"file": "b.rs", "line": 5, "summary": "logic error"}]
    metadata = {
        "token_count": 500,
        "cost_usd": 0.02,
        "wall_time_seconds": 3.1,
        "context_level": "diff+repo",
        "model": "claude-sonnet-4-6",
        "turns": 2,
        "error": None,
    }
    (raw_dir / "findings.json").write_text(json.dumps(findings))
    (raw_dir / "metadata.json").write_text(json.dumps(metadata))
    result = normalize_agent_result("case-001", "anthropic-api", raw_dir)
    assert result.comments[0].file == "b.rs"
    assert result.comments[0].body == "logic error"
    assert result.metadata.tokens == 500
    assert result.metadata.time_seconds == 3.1
    assert result.context_level == "diff+repo"


def test_normalize_agent_result_missing_metadata(tmp_path: Path) -> None:
    """If metadata.json is absent, use defaults."""
    raw_dir = tmp_path / "raw" / "case-001-claude-code-cli"
    raw_dir.mkdir(parents=True)
    (raw_dir / "findings.json").write_text("[]")
    result = normalize_agent_result("case-001", "claude-code-cli", raw_dir)
    assert result.metadata.tokens == 0


# --- discover_raw_dirs ---


def test_discover_raw_dirs(tmp_path: Path) -> None:
    (tmp_path / "raw" / "case-001-greptile").mkdir(parents=True)
    (tmp_path / "raw" / "case-002-coderabbit").mkdir(parents=True)
    dirs = discover_raw_dirs(tmp_path)
    names = {d.name for d in dirs}
    assert names == {"case-001-greptile", "case-002-coderabbit"}


def test_discover_raw_dirs_missing(tmp_path: Path) -> None:
    """Returns empty list if raw/ doesn't exist."""
    dirs = discover_raw_dirs(tmp_path)
    assert dirs == []


# --- _parse_raw_dir_name ---


def test_parse_raw_dir_name() -> None:
    from bugeval.normalize import _parse_raw_dir_name

    # Regex path: case ID ends in -NNN
    assert _parse_raw_dir_name("case-001-coderabbit") == ("case-001", "coderabbit")
    assert _parse_raw_dir_name("aleo-lang-042-anthropic-api") == ("aleo-lang-042", "anthropic-api")
    # Fallback path: no three-digit suffix
    assert _parse_raw_dir_name("foo-bar") == ("foo", "bar")
