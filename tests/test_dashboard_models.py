"""Tests for dashboard models and persistence helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

from bugeval.dashboard_models import (
    GoldenEntry,
    HumanScore,
    RunNote,
    add_run_note,
    load_all_human_scores,
    load_golden_set,
    load_human_score,
    load_run_notes,
    load_tool_blind_map,
    save_golden_set,
    save_human_score,
    save_run_notes,
    save_tool_blind_map,
    set_golden_status,
)

# ---------------------------------------------------------------------------
# RunNote
# ---------------------------------------------------------------------------


def test_run_note_model_defaults() -> None:
    note = RunNote(timestamp="2026-03-20T10:00:00", text="hello")
    assert note.timestamp == "2026-03-20T10:00:00"
    assert note.text == "hello"


def test_load_run_notes_empty(tmp_path: Path) -> None:
    assert load_run_notes(tmp_path) == []


def test_save_and_load_run_notes(tmp_path: Path) -> None:
    notes = [
        RunNote(timestamp="2026-03-20T10:00:00", text="first"),
        RunNote(timestamp="2026-03-20T11:00:00", text="second"),
    ]
    save_run_notes(tmp_path, notes)
    loaded = load_run_notes(tmp_path)
    assert len(loaded) == 2
    assert loaded[0].text == "first"
    assert loaded[1].text == "second"


def test_add_run_note(tmp_path: Path) -> None:
    note = add_run_note(tmp_path, "Test note")
    assert note.text == "Test note"
    assert note.timestamp != ""

    loaded = load_run_notes(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].text == "Test note"


def test_add_run_note_appends(tmp_path: Path) -> None:
    add_run_note(tmp_path, "first")
    add_run_note(tmp_path, "second")
    loaded = load_run_notes(tmp_path)
    assert len(loaded) == 2


# ---------------------------------------------------------------------------
# GoldenEntry
# ---------------------------------------------------------------------------


def test_golden_entry_defaults() -> None:
    entry = GoldenEntry(case_id="leo-001")
    assert entry.status == "unreviewed"
    assert entry.reviewer == ""
    assert entry.notes == ""


def test_load_golden_set_empty(tmp_path: Path) -> None:
    assert load_golden_set(tmp_path) == {}


def test_save_and_load_golden_set(tmp_path: Path) -> None:
    entries = {
        "leo-001": GoldenEntry(case_id="leo-001", status="confirmed", reviewer="alice"),
        "leo-002": GoldenEntry(case_id="leo-002", status="disputed"),
    }
    save_golden_set(tmp_path, entries)
    loaded = load_golden_set(tmp_path)
    assert len(loaded) == 2
    assert loaded["leo-001"].status == "confirmed"
    assert loaded["leo-001"].reviewer == "alice"
    assert loaded["leo-002"].status == "disputed"


def test_set_golden_status(tmp_path: Path) -> None:
    entry = set_golden_status(tmp_path, "leo-001", "confirmed", reviewer="bob", notes="looks good")
    assert entry.status == "confirmed"
    assert entry.reviewer == "bob"
    assert entry.notes == "looks good"
    assert entry.timestamp != ""

    loaded = load_golden_set(tmp_path)
    assert loaded["leo-001"].status == "confirmed"


def test_set_golden_status_updates_existing(tmp_path: Path) -> None:
    set_golden_status(tmp_path, "leo-001", "confirmed")
    set_golden_status(tmp_path, "leo-001", "disputed", notes="changed mind")
    loaded = load_golden_set(tmp_path)
    assert loaded["leo-001"].status == "disputed"
    assert loaded["leo-001"].notes == "changed mind"


# ---------------------------------------------------------------------------
# HumanScore
# ---------------------------------------------------------------------------


def test_human_score_defaults() -> None:
    score = HumanScore(case_id="leo-001", tool="greptile")
    assert score.detection_score == 0
    assert score.review_quality == 0
    assert score.comment_verdicts == []
    assert score.notes == ""


def test_load_human_score_missing(tmp_path: Path) -> None:
    assert load_human_score(tmp_path, "leo-001", "greptile") is None


def test_save_and_load_human_score(tmp_path: Path) -> None:
    score = HumanScore(
        case_id="leo-001",
        tool="greptile",
        detection_score=2,
        review_quality=3,
        comment_verdicts=["TP-expected", "FP"],
        notes="Good detection",
        timestamp="2026-03-20T10:00:00",
    )
    save_human_score(tmp_path, score)
    loaded = load_human_score(tmp_path, "leo-001", "greptile")
    assert loaded is not None
    assert loaded.detection_score == 2
    assert loaded.review_quality == 3
    assert loaded.comment_verdicts == ["TP-expected", "FP"]
    assert loaded.notes == "Good detection"


def test_load_all_human_scores(tmp_path: Path) -> None:
    for tool in ("greptile", "claude-cli"):
        score = HumanScore(case_id="leo-001", tool=tool, detection_score=2)
        save_human_score(tmp_path, score)
    all_scores = load_all_human_scores(tmp_path)
    assert len(all_scores) == 2


def test_load_all_human_scores_empty(tmp_path: Path) -> None:
    assert load_all_human_scores(tmp_path) == []


def test_human_score_yaml_is_plain(tmp_path: Path) -> None:
    score = HumanScore(case_id="leo-001", tool="greptile", detection_score=2)
    save_human_score(tmp_path, score)
    path = tmp_path / "human_scores" / "leo-001__greptile.yaml"
    raw = yaml.safe_load(path.read_text())
    assert isinstance(raw["detection_score"], int)
    assert isinstance(raw["case_id"], str)


# ---------------------------------------------------------------------------
# Tool blind map
# ---------------------------------------------------------------------------


def test_load_tool_blind_map_missing(tmp_path: Path) -> None:
    assert load_tool_blind_map(tmp_path) == {}


def test_save_and_load_tool_blind_map(tmp_path: Path) -> None:
    mapping = {"Tool A": "greptile", "Tool B": "claude-cli"}
    save_tool_blind_map(tmp_path, mapping)
    loaded = load_tool_blind_map(tmp_path)
    assert loaded == mapping
