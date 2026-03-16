"""Tests for the merge-cases command."""

from pathlib import Path

from click.testing import CliRunner

from bugeval.io import load_case, save_case
from bugeval.merge_cases import merge_cases
from bugeval.models import (
    Category,
    Difficulty,
    ExpectedFinding,
    PRSize,
    Severity,
    TestCase,
)


def make_case(
    case_id: str,
    repo: str = "foo/bar",
    fix_commit: str | None = None,
) -> TestCase:
    commit = fix_commit or f"{case_id.replace('-', ''):<40}"[:40]
    return TestCase(
        id=case_id,
        repo=repo,
        base_commit=f"{commit}^",
        head_commit=commit,
        fix_commit=commit,
        category=Category.logic,
        difficulty=Difficulty.easy,
        severity=Severity.low,
        language="rust",
        pr_size=PRSize.small,
        description="A test bug.",
        expected_findings=[ExpectedFinding(file="src/lib.rs", line=1, summary="bug here")],
    )


def populate_dir(directory: Path, cases: list[TestCase]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for case in cases:
        save_case(case, directory / f"{case.id}.yaml")


class TestMergeCasesBasic:
    def test_merges_two_dirs(self, tmp_path: Path) -> None:
        d0 = tmp_path / "shard0"
        d1 = tmp_path / "shard1"
        populate_dir(d0, [make_case("bar-001", fix_commit="a" * 40)])
        populate_dir(d1, [make_case("bar-001", fix_commit="b" * 40)])

        out = tmp_path / "merged"
        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(d0), "-i", str(d1), "-o", str(out)])
        assert result.exit_code == 0, result.output
        files = list(out.glob("*.yaml"))
        assert len(files) == 2

    def test_ids_renumbered_sequentially(self, tmp_path: Path) -> None:
        d0 = tmp_path / "shard0"
        d1 = tmp_path / "shard1"
        populate_dir(d0, [make_case("bar-001", fix_commit="a" * 40)])
        populate_dir(d1, [make_case("bar-001", fix_commit="b" * 40)])

        out = tmp_path / "merged"
        runner = CliRunner()
        runner.invoke(merge_cases, ["-i", str(d0), "-i", str(d1), "-o", str(out)])
        ids = sorted(f.stem for f in out.glob("*.yaml"))
        assert ids == ["bar-001", "bar-002"]

    def test_deduplicates_by_fix_commit(self, tmp_path: Path) -> None:
        d0 = tmp_path / "shard0"
        d1 = tmp_path / "shard1"
        same_commit = "c" * 40
        populate_dir(d0, [make_case("bar-001", fix_commit=same_commit)])
        populate_dir(d1, [make_case("bar-002", fix_commit=same_commit)])

        out = tmp_path / "merged"
        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(d0), "-i", str(d1), "-o", str(out)])
        assert result.exit_code == 0
        assert len(list(out.glob("*.yaml"))) == 1
        assert "1 duplicates skipped" in result.output

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        d0 = tmp_path / "shard0"
        populate_dir(d0, [make_case("bar-001", fix_commit="a" * 40)])

        out = tmp_path / "merged"
        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(d0), "-o", str(out), "--dry-run"])
        assert result.exit_code == 0
        assert not out.exists()
        assert "[dry-run]" in result.output

    def test_deduplicates_within_same_dir(self, tmp_path: Path) -> None:
        """Two files in the same shard dir with the same fix_commit → one output case."""
        same_commit = "e" * 40
        d = tmp_path / "shard0"
        # Manually write two files with different IDs but identical fix_commit
        populate_dir(
            d,
            [
                make_case("bar-001", fix_commit=same_commit),
                make_case("bar-002", fix_commit=same_commit),
            ],
        )

        out = tmp_path / "merged"
        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(d), "-o", str(out)])
        assert result.exit_code == 0
        assert len(list(out.glob("*.yaml"))) == 1
        assert "1 duplicates skipped" in result.output

    def test_same_commit_across_three_shards_deduped(self, tmp_path: Path) -> None:
        """A fix_commit appearing in all 3 shards produces exactly 1 output case."""
        same_commit = "d" * 40
        for i in range(3):
            d = tmp_path / f"shard{i}"
            populate_dir(d, [make_case(f"bar-00{i + 1}", fix_commit=same_commit)])

        out = tmp_path / "merged"
        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(tmp_path / "shard*"), "-o", str(out)])
        assert result.exit_code == 0
        assert len(list(out.glob("*.yaml"))) == 1
        assert "2 duplicates skipped" in result.output

    def test_multiple_repos_numbered_independently(self, tmp_path: Path) -> None:
        d = tmp_path / "mixed"
        populate_dir(
            d,
            [
                make_case("foo-001", repo="org/foo", fix_commit="a" * 40),
                make_case("bar-001", repo="org/bar", fix_commit="b" * 40),
                make_case("foo-002", repo="org/foo", fix_commit="c" * 40),
            ],
        )
        out = tmp_path / "merged"
        runner = CliRunner()
        runner.invoke(merge_cases, ["-i", str(d), "-o", str(out)])
        ids = sorted(f.stem for f in out.glob("*.yaml"))
        assert ids == ["bar-001", "foo-001", "foo-002"]


class TestMergeCasesSafety:
    def test_refuses_non_empty_output_without_force(self, tmp_path: Path) -> None:
        d0 = tmp_path / "shard0"
        populate_dir(d0, [make_case("bar-001", fix_commit="a" * 40)])

        out = tmp_path / "merged"
        populate_dir(out, [make_case("bar-001", fix_commit="z" * 40)])

        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(d0), "-o", str(out)])
        assert result.exit_code != 0
        assert "already contains" in result.output

    def test_force_appends_without_overwriting(self, tmp_path: Path) -> None:
        d0 = tmp_path / "shard0"
        existing = make_case("bar-001", fix_commit="z" * 40)
        new_case = make_case("bar-001", fix_commit="a" * 40)
        populate_dir(d0, [new_case])

        out = tmp_path / "merged"
        populate_dir(out, [existing])

        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(d0), "-o", str(out), "--force"])
        assert result.exit_code == 0
        # bar-001 exists → new case goes to bar-002
        assert (out / "bar-002.yaml").exists()
        # Original bar-001 must be unchanged
        original = load_case(out / "bar-001.yaml")
        assert original.fix_commit == "z" * 40

    def test_never_overwrites_even_with_force(self, tmp_path: Path) -> None:
        """Even if a destination filename would collide, it is skipped, not overwritten."""
        d0 = tmp_path / "shard0"
        # Two cases that would both want bar-001
        populate_dir(d0, [make_case("bar-001", fix_commit="a" * 40)])

        out = tmp_path / "merged"
        existing = make_case("bar-001", fix_commit="original" + "x" * 32)
        populate_dir(out, [existing])

        runner = CliRunner()
        runner.invoke(merge_cases, ["-i", str(d0), "-o", str(out), "--force"])
        kept = load_case(out / "bar-001.yaml")
        assert kept.fix_commit == existing.fix_commit  # original untouched

    def test_error_on_no_matching_dirs(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            merge_cases,
            ["-i", str(tmp_path / "nonexistent-*"), "-o", str(tmp_path / "out")],
        )
        assert result.exit_code != 0

    def test_skips_checkpoint_files(self, tmp_path: Path) -> None:
        d0 = tmp_path / "shard0"
        populate_dir(d0, [make_case("bar-001", fix_commit="a" * 40)])
        (d0 / ".curate_checkpoint.json").write_text('["a"*40]')

        out = tmp_path / "merged"
        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(d0), "-o", str(out)])
        assert result.exit_code == 0
        assert len(list(out.glob("*.yaml"))) == 1

    def test_glob_pattern_expands_dirs(self, tmp_path: Path) -> None:
        for i in range(3):
            d = tmp_path / f"shard{i}"
            populate_dir(d, [make_case(f"bar-00{i + 1}", fix_commit=str(i) * 40)])

        out = tmp_path / "merged"
        runner = CliRunner()
        result = runner.invoke(merge_cases, ["-i", str(tmp_path / "shard*"), "-o", str(out)])
        assert result.exit_code == 0
        assert len(list(out.glob("*.yaml"))) == 3
