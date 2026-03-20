"""Tests for run_pr_eval CLI and helpers."""

from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from bugeval.models import Category, Difficulty, PRSize, Severity, TestCase
from bugeval.pr_eval_models import CaseToolStatus
from bugeval.run_pr_eval import load_cases, make_run_id, process_case_tool, run_pr_eval


def _make_config_file(
    tmp_path: Path, eval_org: str = "provable-eval", fresh_repo: bool = False
) -> Path:
    tools: list[dict] = [
        {
            "name": "coderabbit",
            "type": "pr",
            "github_app": "coderabbit-ai",
            "cooldown_seconds": 0,
        },
    ]
    if fresh_repo:
        tools.append(
            {
                "name": "github-copilot",
                "type": "pr",
                "github_app": "copilot",
                "reviewer": "copilot",
                "fresh_repo": True,
                "cooldown_seconds": 0,
            }
        )
    data = {
        "github": {"eval_org": eval_org},
        "tools": tools,
        "repos": {"aleo-lang": "provable-org/aleo-lang"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(data))
    return config_path


def _make_case_file(cases_dir: Path, case_id: str = "case-001") -> Path:
    cases_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": case_id,
        "repo": "provable-org/aleo-lang",
        "base_commit": "abc123",
        "head_commit": "def456",
        "fix_commit": "def456",
        "category": "logic",
        "difficulty": "medium",
        "severity": "high",
        "language": "rust",
        "pr_size": "small",
        "description": "A test bug case",
        "expected_findings": [{"file": "src/lib.rs", "line": 10, "summary": "test bug"}],
    }
    path = cases_dir / f"{case_id}.yaml"
    path.write_text(yaml.dump(data))
    return path


def _make_case(case_id: str = "case-001") -> TestCase:
    return TestCase(
        id=case_id,
        repo="provable-org/aleo-lang",
        base_commit="abc123",
        head_commit="def456",
        fix_commit="def456",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.small,
        description="A test bug case",
        expected_findings=[],
    )


def test_make_run_id_format() -> None:
    run_id = make_run_id()
    assert run_id.startswith("run-")
    parts = run_id[4:].split("-")
    assert len(parts) == 3  # YYYY-MM-DD


def test_load_cases_missing_dir(tmp_path: Path) -> None:
    cases = load_cases(tmp_path / "nonexistent")
    assert cases == []


def test_load_cases_empty_dir(tmp_path: Path) -> None:
    empty_dir = tmp_path / "cases"
    empty_dir.mkdir()
    cases = load_cases(empty_dir)
    assert cases == []


def test_load_cases_excludes_invalid_for_code_review(tmp_path: Path) -> None:
    """Cases with valid_for_code_review: false must be excluded."""
    valid = {
        "id": "test-001",
        "repo": "org/repo",
        "base_commit": "aaa",
        "head_commit": "bbb",
        "fix_commit": "bbb",
        "category": "logic",
        "difficulty": "easy",
        "severity": "low",
        "language": "rust",
        "pr_size": "small",
        "description": "Valid case",
        "expected_findings": [{"file": "f.rs", "line": 1, "summary": "bug"}],
        "valid_for_code_review": True,
    }
    invalid = {**valid, "id": "test-002", "valid_for_code_review": False}
    (tmp_path / "test-001.yaml").write_text(yaml.safe_dump(valid, sort_keys=False))
    (tmp_path / "test-002.yaml").write_text(yaml.safe_dump(invalid, sort_keys=False))
    cases = load_cases(tmp_path)
    assert len(cases) == 1
    assert cases[0].id == "test-001"


def test_load_cases_excludes_empty_findings(tmp_path: Path) -> None:
    """Cases with no expected_findings cannot be scored — must be excluded."""
    case = {
        "id": "test-003",
        "repo": "org/repo",
        "base_commit": "aaa",
        "head_commit": "bbb",
        "fix_commit": "bbb",
        "category": "logic",
        "difficulty": "easy",
        "severity": "low",
        "language": "rust",
        "pr_size": "small",
        "description": "No findings",
        "expected_findings": [],
        "valid_for_code_review": True,
    }
    (tmp_path / "test-003.yaml").write_text(yaml.safe_dump(case, sort_keys=False))
    cases = load_cases(tmp_path)
    assert len(cases) == 0


def test_run_pr_eval_help() -> None:
    runner = CliRunner()
    result = runner.invoke(run_pr_eval, ["--help"])
    assert result.exit_code == 0
    assert "--cases-dir" in result.output
    assert "--run-dir" in result.output
    assert "--dry-run" in result.output
    assert "--max-concurrent" in result.output


def test_run_pr_eval_no_cases(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    run_dir = tmp_path / "results"

    runner = CliRunner()
    result = runner.invoke(
        run_pr_eval,
        [
            "--config",
            str(config_path),
            "--cases-dir",
            str(cases_dir),
            "--patches-dir",
            str(tmp_path / "patches"),
            "--run-dir",
            str(run_dir),
        ],
    )
    assert result.exit_code == 0
    assert "No cases" in result.output


def test_dry_run_completes(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    cases_dir = tmp_path / "cases"
    _make_case_file(cases_dir)
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    run_dir = tmp_path / "results"

    runner = CliRunner()
    result = runner.invoke(
        run_pr_eval,
        [
            "--config",
            str(config_path),
            "--cases-dir",
            str(cases_dir),
            "--patches-dir",
            str(patches_dir),
            "--run-dir",
            str(run_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "done" in result.output


def test_process_case_tool_dry_run_no_gh_calls(tmp_path: Path) -> None:
    from bugeval.pr_eval_models import load_eval_config

    config_path = _make_config_file(tmp_path)
    config = load_eval_config(config_path)
    tool = config.pr_tools[0]

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    run_dir = tmp_path / "results"

    case = _make_case()

    # In dry-run mode, open_pr returns 0 and no gh calls are made
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        state = process_case_tool(case, tool, config, patches_dir, run_dir, None, dry_run=True)

    mock_gh.assert_not_called()
    assert state.status == CaseToolStatus.done


def test_process_case_tool_missing_patch(tmp_path: Path) -> None:
    from bugeval.pr_eval_models import load_eval_config

    config_path = _make_config_file(tmp_path)
    config = load_eval_config(config_path)
    tool = config.pr_tools[0]

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()  # no patch file
    run_dir = tmp_path / "results"

    case = _make_case()
    state = process_case_tool(case, tool, config, patches_dir, run_dir, None, dry_run=True)

    assert state.status == CaseToolStatus.failed
    assert "patch not found" in (state.error or "")


def test_process_case_tool_with_repo_dir(tmp_path: Path) -> None:
    """When repo_dir is provided, apply_patch_to_branch is called."""
    from bugeval.pr_eval_models import load_eval_config

    config_path = _make_config_file(tmp_path)
    config = load_eval_config(config_path)
    tool = config.pr_tools[0]

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    run_dir = tmp_path / "results"

    case = _make_case()

    with patch("bugeval.run_pr_eval.apply_patch_to_branch"):
        state = process_case_tool(case, tool, config, patches_dir, run_dir, repo_dir, dry_run=True)

    # dry_run=True skips the actual git call, but code path is exercised
    assert state.status == CaseToolStatus.done


def test_process_case_tool_with_repo_dir_applies_patch(tmp_path: Path) -> None:
    """With repo_dir and dry_run=False, apply_patch_to_branch is invoked."""
    from bugeval.pr_eval_models import load_eval_config

    config_path = _make_config_file(tmp_path)
    config = load_eval_config(config_path)
    tool = config.pr_tools[0]

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    run_dir = tmp_path / "results"

    case = _make_case()

    with (
        patch("bugeval.run_pr_eval.apply_patch_to_branch") as mock_apply,
        patch("bugeval.run_pr_eval.open_pr", return_value=7),
        patch("bugeval.run_pr_eval.poll_for_review", return_value=False),
        patch("bugeval.run_pr_eval.scrape_review_comments", return_value=[]),
        patch("bugeval.run_pr_eval.close_pr_delete_branch"),
    ):
        state = process_case_tool(case, tool, config, patches_dir, run_dir, repo_dir, dry_run=False)

    mock_apply.assert_called_once()
    assert state.status == CaseToolStatus.done


def test_scrape_always_called_even_when_poll_times_out(tmp_path: Path) -> None:
    """scrape_review_comments must be called even when poll_for_review returns False.

    Tools that post findings only to issue_comments (not formal reviews) would
    be silently missed if scraping is gated on poll_for_review returning True.
    """
    from bugeval.pr_eval_models import load_eval_config

    config_path = _make_config_file(tmp_path)
    config = load_eval_config(config_path)
    tool = config.pr_tools[0]

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    run_dir = tmp_path / "results"

    case = _make_case()

    with (
        patch("bugeval.run_pr_eval.apply_patch_to_branch"),
        patch("bugeval.run_pr_eval.open_pr", return_value=7),
        patch("bugeval.run_pr_eval.poll_for_review", return_value=False),  # timeout
        patch("bugeval.run_pr_eval.scrape_review_comments", return_value=[]) as mock_scrape,
        patch("bugeval.run_pr_eval.close_pr_delete_branch"),
    ):
        state = process_case_tool(case, tool, config, patches_dir, run_dir, repo_dir, dry_run=False)

    # Scrape MUST be called even though poll timed out (issue_comment tools)
    mock_scrape.assert_called_once()
    assert state.status == CaseToolStatus.done


def test_resume_skips_done(tmp_path: Path) -> None:
    import json

    config_path = _make_config_file(tmp_path)
    cases_dir = tmp_path / "cases"
    _make_case_file(cases_dir)
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    run_dir = tmp_path / "results"
    run_dir.mkdir()

    # Pre-populate raw dir with comments.json to simulate completed PR case
    raw_dir = run_dir / "raw" / "case-001-coderabbit"
    raw_dir.mkdir(parents=True)
    (raw_dir / "comments.json").write_text(json.dumps([]))

    runner = CliRunner()
    with patch("bugeval.run_pr_eval.process_case_tool") as mock_process:
        result = runner.invoke(
            run_pr_eval,
            [
                "--config",
                str(config_path),
                "--cases-dir",
                str(cases_dir),
                "--patches-dir",
                str(patches_dir),
                "--run-dir",
                str(run_dir),
                "--dry-run",
            ],
        )

    # process_case_tool should NOT be called since already done
    mock_process.assert_not_called()
    assert result.exit_code == 0


def test_run_pr_eval_limit_slices_cases(tmp_path: Path) -> None:
    """--limit should process at most N cases per tool."""
    config_path = _make_config_file(tmp_path)
    cases_dir = tmp_path / "cases"
    for i in range(1, 4):
        _make_case_file(cases_dir, f"case-{i:03d}")
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    for i in range(1, 4):
        (patches_dir / f"case-{i:03d}.patch").write_text("--- a\n+++ b\n")
    run_dir = tmp_path / "results"

    runner = CliRunner()
    result = runner.invoke(
        run_pr_eval,
        [
            "--config",
            str(config_path),
            "--cases-dir",
            str(cases_dir),
            "--patches-dir",
            str(patches_dir),
            "--run-dir",
            str(run_dir),
            "--limit",
            "2",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert result.output.count("[done]") == 2


def test_run_pr_eval_fail_after_aborts(tmp_path: Path) -> None:
    """--fail-after should abort the tool loop after N consecutive failures."""
    config_path = _make_config_file(tmp_path)
    cases_dir = tmp_path / "cases"
    for i in range(1, 5):
        _make_case_file(cases_dir, f"case-{i:03d}")
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    # No patches — patch not found → failed state → circuit breaker triggers
    run_dir = tmp_path / "results"

    runner = CliRunner()
    result = runner.invoke(
        run_pr_eval,
        [
            "--config",
            str(config_path),
            "--cases-dir",
            str(cases_dir),
            "--patches-dir",
            str(patches_dir),
            "--run-dir",
            str(run_dir),
            "--fail-after",
            "2",
        ],
    )
    assert result.exit_code == 0
    assert "abort" in result.output


def test_process_case_tool_fresh_repo_dry_run(tmp_path: Path) -> None:
    """Fresh repo tools should complete in dry-run without calling git or gh."""
    from bugeval.pr_eval_models import load_eval_config

    config_path = _make_config_file(tmp_path, fresh_repo=True)
    config = load_eval_config(config_path)
    tool = next(t for t in config.pr_tools if t.fresh_repo)

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    run_dir = tmp_path / "results"

    case = _make_case()

    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        state = process_case_tool(
            case, tool, config, patches_dir, run_dir, None, dry_run=True
        )

    mock_gh.assert_not_called()
    assert state.status == CaseToolStatus.done


def test_process_case_tool_fresh_repo_uses_push_case_branches(tmp_path: Path) -> None:
    """Fresh repo tools should call push_case_branches when not dry-run."""
    from bugeval.pr_eval_models import load_eval_config

    config_path = _make_config_file(tmp_path, fresh_repo=True)
    config = load_eval_config(config_path)
    tool = next(t for t in config.pr_tools if t.fresh_repo)

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    run_dir = tmp_path / "results"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    case = _make_case()

    with (
        patch("bugeval.run_pr_eval.get_or_create_cached_repo", return_value=cache_dir),
        patch(
            "bugeval.run_pr_eval.push_case_branches",
            return_value=("bugeval-base/case-001", "bugeval/case-001-github-copilot"),
        ) as mock_push,
        patch("bugeval.run_pr_eval.open_pr", return_value=7),
        patch("bugeval.run_pr_eval.request_review"),
        patch("bugeval.run_pr_eval.poll_for_review", return_value=True),
        patch("bugeval.run_pr_eval.scrape_review_comments", return_value=[]),
        patch("bugeval.run_pr_eval.close_pr_delete_branch"),
    ):
        state = process_case_tool(
            case, tool, config, patches_dir, run_dir, None, dry_run=False,
            cache_dir=cache_dir,
        )

    mock_push.assert_called_once()
    assert state.status == CaseToolStatus.done


def test_process_case_tool_fresh_repo_no_cache_dir_fails(tmp_path: Path) -> None:
    """Fresh repo tools should fail if cache_dir is not provided."""
    from bugeval.pr_eval_models import load_eval_config

    config_path = _make_config_file(tmp_path, fresh_repo=True)
    config = load_eval_config(config_path)
    tool = next(t for t in config.pr_tools if t.fresh_repo)

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    (patches_dir / "case-001.patch").write_text("--- a\n+++ b\n")
    run_dir = tmp_path / "results"

    case = _make_case()

    state = process_case_tool(
        case, tool, config, patches_dir, run_dir, None, dry_run=False, cache_dir=None,
    )

    assert state.status == CaseToolStatus.failed
    assert "cache-dir" in (state.error or "")
