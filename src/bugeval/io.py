"""YAML load/save helpers for test cases and candidates."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
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


def write_run_metadata(
    run_dir: Path,
    tools: list[str],
    context_level: str,
    cases_dir: Path,
    *,
    limit: int = 0,
    patches_dir: Path | None = None,
    config_path: str = "config/config.yaml",
) -> None:
    """Write run_metadata.json for reproducibility tracing."""
    git_sha = ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        git_sha = result.stdout.strip() if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    dataset_commit = ""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(cases_dir)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        dataset_commit = result.stdout.strip() if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    config_hash = ""
    config_file = Path(config_path)
    if config_file.exists():
        config_hash = "sha256:" + hashlib.sha256(config_file.read_bytes()).hexdigest()

    agent_prompt_hash = ""
    agent_prompt_path = Path("config") / "agent_prompt.md"
    if agent_prompt_path.exists():
        agent_prompt_hash = "sha256:" + hashlib.sha256(agent_prompt_path.read_bytes()).hexdigest()

    total_cases = sum(1 for _ in cases_dir.glob("*.yaml")) if cases_dir.exists() else 0

    metadata = {
        "created_at": datetime.now(tz=UTC).isoformat(),
        "git_sha": git_sha,
        "config_hash": config_hash,
        "context_level": context_level,
        "tools": tools,
        "cases_dir": str(cases_dir),
        "limit": limit,
        "patches_dir": str(patches_dir) if patches_dir is not None else None,
        "dataset_commit": dataset_commit,
        "total_cases": total_cases,
        "agent_prompt_hash": agent_prompt_hash,
        "python_version": sys.version.split()[0],
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2))
