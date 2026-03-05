"""Tests for mine-candidates CLI command."""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from bugeval.cli import cli


def _make_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True, capture_output=True)


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_mine_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["mine-candidates", "--help"])
    assert result.exit_code == 0
    assert "--repo-dir" in result.output
    assert "--branch" in result.output
    assert "--use-llm" in result.output
    assert "--dry-run" in result.output


def test_mine_dry_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo)
    _commit(repo, "main.rs", "fn foo() { bug }\n", "initial commit")
    _commit(repo, "main.rs", "fn foo() { ok }\n", "fix: fix the bug closes #1")

    output_dir = tmp_path / "candidates"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "mine-candidates",
            "--repo-dir",
            str(repo),
            "--repo-name",
            "test/repo",
            "--branch",
            "main",
            "--limit",
            "10",
            "--min-confidence",
            "0.1",
            "--output-dir",
            str(output_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    # No YAML files should be written
    assert not output_dir.exists() or not list(output_dir.glob("*.yaml"))


def test_mine_writes_candidates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo)
    _commit(repo, "main.rs", "fn foo() { bug }\n", "initial commit")
    _commit(repo, "main.rs", "fn foo() { ok }\n", "fix: fix the bug closes #1")

    output_dir = tmp_path / "candidates"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "mine-candidates",
            "--repo-dir",
            str(repo),
            "--repo-name",
            "test/repo",
            "--branch",
            "main",
            "--limit",
            "10",
            "--min-confidence",
            "0.1",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0
    yaml_files = list(output_dir.glob("*.yaml"))
    assert len(yaml_files) >= 1
