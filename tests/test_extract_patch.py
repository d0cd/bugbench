"""Tests for extract-patch command."""

import subprocess
from pathlib import Path

from click.testing import CliRunner

from bugeval.extract_patch import extract_patch
from bugeval.io import save_case
from bugeval.models import (
    Category,
    Difficulty,
    ExpectedFinding,
    PRSize,
    Severity,
    TestCase,
)


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True, capture_output=True)
    (path / "code.rs").write_text("fn foo() {}\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def get_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def add_commit(repo: Path, content: str) -> str:
    (repo / "code.rs").write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "fix"], cwd=repo, check=True, capture_output=True)
    return get_sha(repo)


def make_case(id_: str, base: str, head: str) -> TestCase:
    return TestCase(
        id=id_,
        repo="foo/bar",
        base_commit=base,
        head_commit=head,
        fix_commit=head,
        category=Category.logic,
        difficulty=Difficulty.easy,
        severity=Severity.low,
        language="rust",
        pr_size=PRSize.tiny,
        description="Test",
        expected_findings=[ExpectedFinding(file="code.rs", line=1, summary="change")],
    )


class TestExtractPatchHelp:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(extract_patch, ["--help"])
        assert result.exit_code == 0
        assert "--case" in result.output
        assert "--all" in result.output
        assert "--repo-dir" in result.output
        assert "--output-dir" in result.output


class TestExtractPatchSingle:
    def test_creates_patch_file(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        base = get_sha(repo)
        head = add_commit(repo, "fn foo() { let x = 1; }\n")

        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        patches_dir = tmp_path / "patches"

        case = make_case("ep-001", base, head)
        save_case(case, cases_dir / "ep-001.yaml")

        runner = CliRunner()
        result = runner.invoke(
            extract_patch,
            [
                "--case",
                "ep-001",
                "--repo-dir",
                str(repo),
                "--cases-dir",
                str(cases_dir),
                "--output-dir",
                str(patches_dir),
            ],
        )
        assert result.exit_code == 0
        patch_file = patches_dir / "ep-001.patch"
        assert patch_file.exists()

    def test_patch_content_valid(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        base = get_sha(repo)
        head = add_commit(repo, "fn foo() { let x = 1; }\n")

        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        patches_dir = tmp_path / "patches"

        case = make_case("ep-002", base, head)
        save_case(case, cases_dir / "ep-002.yaml")

        runner = CliRunner()
        runner.invoke(
            extract_patch,
            [
                "--case",
                "ep-002",
                "--repo-dir",
                str(repo),
                "--cases-dir",
                str(cases_dir),
                "--output-dir",
                str(patches_dir),
            ],
        )
        patch_content = (patches_dir / "ep-002.patch").read_text()
        assert "code.rs" in patch_content
        assert "@@" in patch_content

    def test_missing_case_fails(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        runner = CliRunner()
        result = runner.invoke(
            extract_patch,
            [
                "--case",
                "nonexistent",
                "--repo-dir",
                str(repo),
                "--cases-dir",
                str(cases_dir),
                "--output-dir",
                str(tmp_path / "patches"),
            ],
        )
        assert result.exit_code != 0


class TestExtractPatchAll:
    def test_all_creates_multiple_patches(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        base = get_sha(repo)
        head1 = add_commit(repo, "fn foo() { 1 }\n")
        head2 = add_commit(repo, "fn foo() { 2 }\n")

        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        patches_dir = tmp_path / "patches"

        save_case(make_case("all-001", base, head1), cases_dir / "all-001.yaml")
        save_case(make_case("all-002", head1, head2), cases_dir / "all-002.yaml")

        runner = CliRunner()
        result = runner.invoke(
            extract_patch,
            [
                "--all",
                "--repo-dir",
                str(repo),
                "--cases-dir",
                str(cases_dir),
                "--output-dir",
                str(patches_dir),
            ],
        )
        assert result.exit_code == 0
        assert (patches_dir / "all-001.patch").exists()
        assert (patches_dir / "all-002.patch").exists()
