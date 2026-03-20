"""Tests for manage_fresh_repos CLI and helpers."""

from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from bugeval.manage_fresh_repos import (
    fresh_repo_name,
    manage_fresh_repos,
    push_case_branches,
)


def _make_config(
    tmp_path: Path,
    eval_org: str = "bug-tools-eval",
    repos: dict | None = None,
    fresh_repo: bool = True,
) -> Path:
    if repos is None:
        repos = {"leo": "ProvableHQ/leo"}
    data = {
        "github": {"eval_org": eval_org},
        "tools": [
            {
                "name": "github-copilot",
                "type": "pr",
                "github_app": "copilot",
                "reviewer": "copilot",
                "cooldown_seconds": 30,
                "fresh_repo": fresh_repo,
            },
            {"name": "greptile", "type": "api", "github_app": "greptile", "cooldown_seconds": 30},
            {
                "name": "coderabbit",
                "type": "pr",
                "github_app": "coderabbit-ai",
                "cooldown_seconds": 30,
            },
        ],
        "repos": repos,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(data))
    return config_path


def test_fresh_repo_name_format() -> None:
    assert fresh_repo_name("ProvableHQ/leo", "github-copilot") == "leo-github-copilot"
    assert fresh_repo_name("org/snarkVM", "bugbot") == "snarkVM-bugbot"


def test_manage_fresh_repos_help() -> None:
    runner = CliRunner()
    result = runner.invoke(manage_fresh_repos, ["--help"])
    assert result.exit_code == 0
    assert "--action" in result.output
    assert "--tools" in result.output
    assert "--dry-run" in result.output


def test_create_dry_run(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        manage_fresh_repos, ["--config", str(config_path), "--action", "create", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "[dry-run]" in result.output
    assert "leo-github-copilot" in result.output


def test_create_calls_gh(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch("bugeval.manage_fresh_repos.run_gh") as mock_gh:
        result = runner.invoke(
            manage_fresh_repos, ["--config", str(config_path), "--action", "create"]
        )
    assert result.exit_code == 0
    mock_gh.assert_called_once_with(
        "repo", "create", "bug-tools-eval/leo-github-copilot", "--public"
    )


def test_create_dry_run_does_not_call_gh(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch("bugeval.manage_fresh_repos.run_gh") as mock_gh:
        result = runner.invoke(
            manage_fresh_repos, ["--config", str(config_path), "--action", "create", "--dry-run"]
        )
    assert result.exit_code == 0
    mock_gh.assert_not_called()


def test_delete_calls_gh(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch("bugeval.manage_fresh_repos.run_gh") as mock_gh:
        result = runner.invoke(
            manage_fresh_repos, ["--config", str(config_path), "--action", "cleanup"]
        )
    assert result.exit_code == 0
    mock_gh.assert_called_once_with(
        "repo", "delete", "bug-tools-eval/leo-github-copilot", "--yes"
    )


def test_verify_calls_gh(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch("bugeval.manage_fresh_repos.run_gh") as mock_gh:
        result = runner.invoke(
            manage_fresh_repos, ["--config", str(config_path), "--action", "verify"]
        )
    assert result.exit_code == 0
    mock_gh.assert_called_once_with("api", "repos/bug-tools-eval/leo-github-copilot")


def test_only_fresh_repo_tools(tmp_path: Path) -> None:
    """manage-fresh-repos should only operate on PR tools with fresh_repo=True."""
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch("bugeval.manage_fresh_repos.run_gh") as mock_gh:
        result = runner.invoke(
            manage_fresh_repos, ["--config", str(config_path), "--action", "create"]
        )
    assert result.exit_code == 0
    # coderabbit is PR but not fresh_repo, greptile is API — neither should appear
    for call_args in mock_gh.call_args_list:
        args_str = " ".join(str(a) for a in call_args[0])
        assert "coderabbit" not in args_str
        assert "greptile" not in args_str


def test_tools_filter(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        manage_fresh_repos,
        ["--config", str(config_path), "--action", "create", "--tools", "nonexistent", "--dry-run"],
    )
    assert result.exit_code != 0


def test_missing_eval_org_exits(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path, eval_org="")
    runner = CliRunner()
    result = runner.invoke(
        manage_fresh_repos, ["--config", str(config_path), "--action", "create"]
    )
    assert result.exit_code != 0
    assert "eval_org" in result.output


def test_no_repos_configured_exits(tmp_path: Path) -> None:
    config_path = _make_config(tmp_path, repos={})
    runner = CliRunner()
    result = runner.invoke(
        manage_fresh_repos, ["--config", str(config_path), "--action", "create"]
    )
    assert result.exit_code != 0
    assert "repos" in result.output


def test_push_case_branches_git_sequence(tmp_path: Path) -> None:
    """Verify the git command sequence for push_case_branches."""
    upstream_cache = tmp_path / "cache"
    patch_path = tmp_path / "test.patch"
    patch_path.write_text("--- a\n+++ b\n")
    tmp_parent = tmp_path / "tmp"
    tmp_parent.mkdir()

    git_calls: list[tuple[str, ...]] = []

    def mock_run_git(*args: str, cwd: Path, timeout: int = 60) -> str:
        git_calls.append(args)
        return ""

    with (
        patch("bugeval.manage_fresh_repos.clone_repo_local") as mock_clone,
        patch("bugeval.manage_fresh_repos.run_git", side_effect=mock_run_git),
    ):
        base_branch, patch_branch = push_case_branches(
            upstream_cache,
            "abc123",
            patch_path,
            "leo-001",
            "github-copilot",
            "https://github.com/org/repo.git",
            tmp_parent,
        )

    mock_clone.assert_called_once()
    assert base_branch == "bugeval-base/leo-001"
    assert patch_branch.startswith("bugeval/")

    # Verify key git operations in order
    commands = [args[0] for args in git_calls]
    assert commands == [
        "checkout",   # detach at base_commit
        "checkout",   # --orphan
        "add",        # stage all
        "commit",     # orphan commit
        "push",       # push base branch
        "checkout",   # -b patch branch
        "apply",      # apply patch
        "add",        # stage
        "commit",     # patch commit
        "push",       # push patch branch
    ]

    # Verify orphan checkout
    assert git_calls[0] == ("checkout", "abc123")
    assert git_calls[1][:2] == ("checkout", "--orphan")
    assert "bugeval-base/leo-001" in git_calls[1]

    # Verify push targets
    assert "bugeval-base/leo-001:bugeval-base/leo-001" in git_calls[4]
    push_ref = git_calls[9][2]
    assert push_ref.startswith("bugeval/") and push_ref.endswith(f":{patch_branch}")


def test_push_case_branches_cleans_up_on_success(tmp_path: Path) -> None:
    upstream_cache = tmp_path / "cache"
    patch_path = tmp_path / "test.patch"
    patch_path.write_text("--- a\n+++ b\n")
    tmp_parent = tmp_path / "tmp"
    tmp_parent.mkdir()

    with (
        patch("bugeval.manage_fresh_repos.clone_repo_local"),
        patch("bugeval.manage_fresh_repos.run_git"),
        patch("bugeval.manage_fresh_repos.shutil.rmtree") as mock_rmtree,
    ):
        push_case_branches(
            upstream_cache, "abc123", patch_path, "leo-001",
            "github-copilot", "https://github.com/org/repo.git", tmp_parent,
        )

    mock_rmtree.assert_called_once()


def test_push_case_branches_cleans_up_on_failure(tmp_path: Path) -> None:
    upstream_cache = tmp_path / "cache"
    patch_path = tmp_path / "test.patch"
    patch_path.write_text("--- a\n+++ b\n")
    tmp_parent = tmp_path / "tmp"
    tmp_parent.mkdir()

    from bugeval.git_utils import GitError

    with (
        patch("bugeval.manage_fresh_repos.clone_repo_local"),
        patch("bugeval.manage_fresh_repos.run_git", side_effect=GitError(["git"], "fail")),
        patch("bugeval.manage_fresh_repos.shutil.rmtree") as mock_rmtree,
    ):
        try:
            push_case_branches(
                upstream_cache, "abc123", patch_path, "leo-001",
                "github-copilot", "https://github.com/org/repo.git", tmp_parent,
            )
        except GitError:
            pass

    mock_rmtree.assert_called_once()


def test_clean_branches_dry_run(tmp_path: Path) -> None:
    with patch("bugeval.manage_fresh_repos.run_gh") as mock_gh:
        mock_gh.return_value = (
            "refs/heads/bugeval-base/leo-001\n"
            "refs/heads/bugeval/leo-001-copilot\n"
            "refs/heads/main\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            manage_fresh_repos,
            [
                "--config", str(_make_config(tmp_path)),
                "--action", "clean-branches",
                "--dry-run",
            ],
        )
    assert result.exit_code == 0
    assert "[dry-run] delete branch bugeval-base/leo-001" in result.output
    assert "[dry-run] delete branch bugeval/leo-001-copilot" in result.output
    # main should not be deleted
    assert "main" not in result.output.replace("bugeval", "")
