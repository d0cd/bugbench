"""YAML load/save helpers for test cases and candidates."""

from __future__ import annotations

from pathlib import Path

import yaml

from bugeval.models import Candidate, TestCase


def save_case(case: TestCase, path: Path) -> None:
    """Serialize a TestCase to YAML and write to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(case.model_dump(mode="json"), f, sort_keys=False)


def load_case(path: Path) -> TestCase:
    """Load a TestCase from a YAML file."""
    if not path.exists():
        raise FileNotFoundError(f"Case file not found: {path}")
    with open(path) as f:
        data = yaml.safe_load(f)
    return TestCase(**data)


def save_candidates(candidates: list[Candidate], path: Path) -> None:
    """Serialize a list of Candidates to YAML and write to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [c.model_dump(mode="json") for c in candidates]
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def load_candidates(path: Path) -> list[Candidate]:
    """Load a list of Candidates from a YAML file."""
    if not path.exists():
        raise FileNotFoundError(f"Candidates file not found: {path}")
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data:
        return []
    return [Candidate(**item) for item in data]


def load_all_cases(cases_dir: Path) -> list[TestCase]:
    """Load all TestCase YAML files from a directory."""
    cases: list[TestCase] = []
    for yaml_file in sorted(cases_dir.glob("*.yaml")):
        cases.append(load_case(yaml_file))
    return cases
