"""Tests for experiment models and persistence."""

from __future__ import annotations

from pathlib import Path

import yaml

from bugeval.dashboard_models import (
    Experiment,
    ExperimentStore,
    load_experiments,
    save_experiments,
    slugify,
)

# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify_basic() -> None:
    assert slugify("Baseline v3") == "baseline-v3"


def test_slugify_special_chars() -> None:
    assert slugify("Run — corrected prompt!") == "run-corrected-prompt"


def test_slugify_extra_spaces() -> None:
    assert slugify("  lots   of   spaces  ") == "lots-of-spaces"


# ---------------------------------------------------------------------------
# load / save round-trip
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = load_experiments(tmp_path)
    assert store.experiments == []


def test_load_empty_file_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "experiments.yaml").write_text("")
    store = load_experiments(tmp_path)
    assert store.experiments == []


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    exp = Experiment(
        id="baseline-v3",
        name="Baseline v3",
        status="active",
        runs=["run-2026-03-18-baseline"],
        notes="First run",
        created="2026-03-18",
    )
    store = ExperimentStore(experiments=[exp])
    save_experiments(tmp_path, store)

    loaded = load_experiments(tmp_path)
    assert len(loaded.experiments) == 1
    assert loaded.experiments[0].id == "baseline-v3"
    assert loaded.experiments[0].name == "Baseline v3"
    assert loaded.experiments[0].runs == ["run-2026-03-18-baseline"]
    assert loaded.experiments[0].notes == "First run"


def test_save_creates_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    store = ExperimentStore(experiments=[])
    save_experiments(nested, store)
    assert (nested / "experiments.yaml").exists()


def test_multiple_experiments_roundtrip(tmp_path: Path) -> None:
    store = ExperimentStore(
        experiments=[
            Experiment(id="exp-1", name="Exp 1", runs=["run-a"]),
            Experiment(id="exp-2", name="Exp 2", runs=["run-b", "run-c"]),
        ]
    )
    save_experiments(tmp_path, store)
    loaded = load_experiments(tmp_path)
    assert len(loaded.experiments) == 2
    assert loaded.experiments[1].runs == ["run-b", "run-c"]


def test_find_experiment_by_id(tmp_path: Path) -> None:
    store = ExperimentStore(
        experiments=[
            Experiment(id="alpha", name="Alpha"),
            Experiment(id="beta", name="Beta"),
        ]
    )
    save_experiments(tmp_path, store)
    loaded = load_experiments(tmp_path)
    found = [e for e in loaded.experiments if e.id == "beta"]
    assert len(found) == 1
    assert found[0].name == "Beta"


def test_experiment_defaults() -> None:
    exp = Experiment(id="test", name="Test")
    assert exp.status == "active"
    assert exp.runs == []
    assert exp.notes == ""
    assert exp.created == ""


def test_saved_yaml_is_plain(tmp_path: Path) -> None:
    store = ExperimentStore(experiments=[Experiment(id="x", name="X", status="active")])
    save_experiments(tmp_path, store)
    raw = yaml.safe_load((tmp_path / "experiments.yaml").read_text())
    # Should be plain dicts, not tagged Pydantic objects
    assert isinstance(raw["experiments"][0]["status"], str)
