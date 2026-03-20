"""Tests for agent_prompts."""

from pathlib import Path

from bugeval.agent_prompts import (
    _DEFAULT_SYSTEM_PROMPT,
    build_user_prompt,
    load_agent_prompt,
)
from bugeval.git_utils import sanitize_patch
from bugeval.models import Category, Difficulty, PRSize, Severity, TestCase
from bugeval.repo_setup import materialize_workspace


def _make_case(**overrides: object) -> TestCase:
    defaults: dict[str, object] = {
        "id": "aleo-lang-001",
        "repo": "provable-org/aleo-lang",
        "base_commit": "abc123",
        "head_commit": "def456",
        "fix_commit": "def456",
        "category": Category.logic,
        "difficulty": Difficulty.medium,
        "severity": Severity.high,
        "language": "rust",
        "pr_size": PRSize.small,
        "description": "Off-by-one in loop bound",
        "expected_findings": [],
    }
    defaults.update(overrides)
    return TestCase(**defaults)  # type: ignore[arg-type]


def _materialize_test_workspace(
    workspace: Path,
    case: TestCase,
    patch: str = "diff --git a/f.rs b/f.rs\n- old\n+ new\n",
    context_level: str = "diff-only",
) -> None:
    materialize_workspace(case, patch, context_level, workspace)


def test_load_agent_prompt_from_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "agent_prompt.md"
    prompt_file.write_text("Custom system prompt")
    result = load_agent_prompt(prompt_file)
    assert result == "Custom system prompt"


def test_load_agent_prompt_falls_back_to_default(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.md"
    result = load_agent_prompt(missing)
    assert result == _DEFAULT_SYSTEM_PROMPT


def test_build_user_prompt_diff_only(tmp_path: Path) -> None:
    case = _make_case()
    _materialize_test_workspace(
        tmp_path, case, "diff --git a/f.rs b/f.rs\n- old\n+ new\n", "diff-only"
    )
    prompt = build_user_prompt(case, tmp_path, "diff-only")
    # CLI agents read diff from file, not inlined
    assert "diff.patch" in prompt
    assert "repository" not in prompt.lower()
    assert "domain" not in prompt.lower()


def test_build_user_prompt_diff_plus_repo(tmp_path: Path) -> None:
    case = _make_case()
    _materialize_test_workspace(
        tmp_path, case, "diff --git a/f.rs b/f.rs\npatch content\n", "diff+repo"
    )
    prompt = build_user_prompt(case, tmp_path, "diff+repo")
    # CLI agents read diff from file, not inlined
    assert "diff.patch" in prompt
    assert "working directory" in prompt.lower()
    assert "Domain Context" not in prompt


def test_build_user_prompt_diff_plus_repo_directs_tool_use(tmp_path: Path) -> None:
    """diff+repo prompt must explicitly instruct the agent to use Read/Grep/Glob."""
    case = _make_case()
    _materialize_test_workspace(
        tmp_path, case, "diff --git a/f.rs b/f.rs\npatch content\n", "diff+repo"
    )
    prompt = build_user_prompt(case, tmp_path, "diff+repo")
    lower = prompt.lower()
    assert "read" in lower
    assert "grep" in lower
    assert "step 1" in lower or "understand" in lower


def test_build_user_prompt_diff_plus_repo_plus_domain(tmp_path: Path) -> None:
    case = _make_case()
    _materialize_test_workspace(
        tmp_path,
        case,
        "diff --git a/f.rs b/f.rs\npatch content\n",
        "diff+repo+domain",
    )
    prompt = build_user_prompt(case, tmp_path, "diff+repo+domain")
    assert "diff.patch" in prompt
    assert "working directory" in prompt.lower()
    assert "Domain Context" in prompt
    assert "logic" in prompt
    assert "high" in prompt
    assert "rust" in prompt
    assert "Off-by-one" in prompt


# ---------------------------------------------------------------------------
# Language-aware prompt loading
# ---------------------------------------------------------------------------


def test_load_agent_prompt_language_specific_file_used(tmp_path: Path) -> None:
    """When a language-specific prompt file exists in the config dir, it is used."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic prompt")
    (config_dir / "agent_prompt_rust.md").write_text("rust-specific prompt")

    result = load_agent_prompt(config_dir=config_dir, language="rust")
    assert result == "rust-specific prompt"


def test_load_agent_prompt_falls_back_to_generic_when_no_language_specific(
    tmp_path: Path,
) -> None:
    """Falls back to generic agent_prompt.md when no language-specific file exists."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic prompt")

    result = load_agent_prompt(config_dir=config_dir, language="go")
    assert result == "generic prompt"


def test_load_agent_prompt_no_language_uses_generic(tmp_path: Path) -> None:
    """When language is not given, uses generic agent_prompt.md."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic prompt")

    result = load_agent_prompt(config_dir=config_dir)
    assert result == "generic prompt"


def test_load_agent_prompt_explicit_path_still_works(tmp_path: Path) -> None:
    """Explicit path= argument still overrides config_dir lookup."""
    prompt_file = tmp_path / "custom.md"
    prompt_file.write_text("custom prompt")
    result = load_agent_prompt(path=prompt_file)
    assert result == "custom prompt"


# ---------------------------------------------------------------------------
# Context-level-aware prompt loading
# ---------------------------------------------------------------------------


def test_load_agent_prompt_context_level_specific_file_used(
    tmp_path: Path,
) -> None:
    """When a context-level-specific prompt file exists it takes priority over generic."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic prompt")
    (config_dir / "agent_prompt_diff+repo.md").write_text("diff+repo prompt")

    result = load_agent_prompt(config_dir=config_dir, context_level="diff+repo")
    assert result == "diff+repo prompt"


def test_load_agent_prompt_context_level_falls_back_to_generic(
    tmp_path: Path,
) -> None:
    """Falls back to generic agent_prompt.md when no context-level file exists."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic prompt")

    result = load_agent_prompt(config_dir=config_dir, context_level="diff+repo")
    assert result == "generic prompt"


def test_load_agent_prompt_context_level_priority_over_language(
    tmp_path: Path,
) -> None:
    """Context-level file takes priority over language-specific file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic")
    (config_dir / "agent_prompt_rust.md").write_text("rust prompt")
    (config_dir / "agent_prompt_diff+repo.md").write_text("diff+repo prompt")

    result = load_agent_prompt(config_dir=config_dir, context_level="diff+repo", language="rust")
    assert result == "diff+repo prompt"


def test_load_agent_prompt_case_type_introducing(tmp_path: Path) -> None:
    """case_type='introducing' loads the introducing-specific prompt."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic")
    (config_dir / "agent_prompt_introducing.md").write_text("introducing prompt")

    result = load_agent_prompt(config_dir=config_dir, case_type="introducing")
    assert result == "introducing prompt"


def test_load_agent_prompt_case_type_fix_uses_generic(tmp_path: Path) -> None:
    """case_type='fix' (default) should NOT load a case-type-specific file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic")
    (config_dir / "agent_prompt_fix.md").write_text("fix prompt")

    result = load_agent_prompt(config_dir=config_dir, case_type="fix")
    assert result == "generic"


def test_load_agent_prompt_case_type_priority_over_context(tmp_path: Path) -> None:
    """case_type-specific file takes priority over context-level file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_prompt.md").write_text("generic")
    (config_dir / "agent_prompt_introducing.md").write_text("introducing")
    (config_dir / "agent_prompt_diff+repo.md").write_text("diff+repo")

    result = load_agent_prompt(
        config_dir=config_dir, case_type="introducing", context_level="diff+repo",
    )
    assert result == "introducing"


def test_build_user_prompt_diff_plus_repo_allows_reasoning(
    tmp_path: Path,
) -> None:
    """diff+repo closing instruction should encourage reasoning and require a code fence."""
    case = _make_case()
    _materialize_test_workspace(
        tmp_path, case, "diff --git a/f.rs b/f.rs\npatch content\n", "diff+repo"
    )
    prompt = build_user_prompt(case, tmp_path, "diff+repo")
    lower = prompt.lower()
    # Should NOT say "only" (i.e. "return ONLY the JSON") in the closing
    assert "return only" not in lower
    # Should encourage reasoning or explanation before the JSON array
    assert any(word in lower for word in ("reasoning", "explain", "analysis", "walk"))
    # Should instruct use of a code block (ensures fence-based extraction)
    assert "code block" in lower or "```" in prompt


def test_build_user_prompt_diff_plus_repo_plus_domain_allows_reasoning(
    tmp_path: Path,
) -> None:
    """diff+repo+domain closing instruction should also encourage reasoning."""
    case = _make_case()
    _materialize_test_workspace(
        tmp_path,
        case,
        "diff --git a/f.rs b/f.rs\npatch content\n",
        "diff+repo+domain",
    )
    prompt = build_user_prompt(case, tmp_path, "diff+repo+domain")
    lower = prompt.lower()
    assert "return only" not in lower
    assert any(word in lower for word in ("reasoning", "explain", "analysis", "walk"))
    assert "code block" in lower or "```" in prompt


def test_introducing_commit_not_in_any_prompt(tmp_path: Path) -> None:
    """introducing_commit must never leak into any agent prompt (analysis-only field)."""
    case = _make_case()
    case = case.model_copy(update={"introducing_commit": "deadbeef1234567890"})
    patch = "diff --git a/foo.rs b/foo.rs\n--- a/foo\n+++ b/foo\n"
    for level in ("diff-only", "diff+repo", "diff+repo+domain"):
        ws = tmp_path / level
        ws.mkdir()
        _materialize_test_workspace(ws, case, patch, level)
        prompt = build_user_prompt(case, ws, level)
        assert "deadbeef" not in prompt, f"introducing_commit leaked into {level} prompt"
        assert "introducing_commit" not in prompt, (
            f"introducing_commit field name leaked into {level} prompt"
        )


# ---------------------------------------------------------------------------
# Patch sanitization
# ---------------------------------------------------------------------------


def test_sanitize_patch_strips_index_lines() -> None:
    """index lines contain blob SHAs that can be used to look up the commit on GitHub."""
    patch = (
        "diff --git a/foo.rs b/foo.rs\n"
        "index 7db1efe3ae..d22cd8683c 100644\n"
        "--- a/foo.rs\n"
        "+++ b/foo.rs\n"
        "@@ -10,3 +10,3 @@\n"
        "- old line\n"
        "+ new line\n"
    )
    result = sanitize_patch(patch)
    assert "7db1efe3ae" not in result
    assert "d22cd8683c" not in result
    assert "diff --git a/foo.rs b/foo.rs" in result
    assert "- old line" in result
    assert "+ new line" in result


def test_sanitize_patch_strips_format_patch_headers() -> None:
    """git format-patch envelope headers must be stripped."""
    patch = (
        "From abc123def456 Mon Sep 17 00:00:00 2001\n"
        "From: Author <author@example.com>\n"
        "Date: Thu, 13 Mar 2026 10:00:00 -0700\n"
        "Subject: [PATCH] Fix the off-by-one error\n"
        "\n"
        "Detailed commit message body.\n"
        "---\n"
        " foo.rs | 2 +-\n"
        " 1 file changed\n"
        "\n"
        "diff --git a/foo.rs b/foo.rs\n"
        "--- a/foo.rs\n"
        "+++ b/foo.rs\n"
        "@@ -1,1 +1,1 @@\n"
        "- old\n"
        "+ new\n"
    )
    result = sanitize_patch(patch)
    assert "abc123def456" not in result
    assert "Author" not in result
    assert "Fix the off-by-one error" not in result
    assert "Detailed commit message body" not in result
    assert "diff --git a/foo.rs b/foo.rs" in result
    assert "- old" in result


def test_sanitize_patch_preserves_diff_content() -> None:
    """Actual diff content (hunks, file paths) must be preserved."""
    patch = (
        "diff --git a/src/lib.rs b/src/lib.rs\n"
        "--- a/src/lib.rs\n"
        "+++ b/src/lib.rs\n"
        "@@ -42,7 +42,7 @@ fn process(data: &[u8]) {\n"
        "     let x = data.len();\n"
        "-    for i in 0..x - 1 {\n"
        "+    for i in 0..x {\n"
        "         process(data[i]);\n"
    )
    result = sanitize_patch(patch)
    assert result == patch  # No index line, nothing to strip


# ---------------------------------------------------------------------------
# Opaque case ID — case.id must not appear in prompts
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PR context in prompts
# ---------------------------------------------------------------------------


def test_pr_title_included_in_all_context_levels(tmp_path: Path) -> None:
    case = _make_case(pr_title="Fix race condition in pool")
    patch = "diff --git a/f.rs b/f.rs\npatch\n"
    for level in ("diff-only", "diff+repo", "diff+repo+domain"):
        ws = tmp_path / level
        ws.mkdir()
        _materialize_test_workspace(ws, case, patch, level)
        prompt = build_user_prompt(case, ws, level)
        assert "Fix race condition in pool" in prompt, f"pr_title missing from {level}"


def test_pr_body_included_in_all_context_levels(tmp_path: Path) -> None:
    case = _make_case(pr_body="This PR fixes the connection pool race condition.")
    patch = "diff --git a/f.rs b/f.rs\npatch\n"
    for level in ("diff-only", "diff+repo", "diff+repo+domain"):
        ws = tmp_path / level
        ws.mkdir()
        _materialize_test_workspace(ws, case, patch, level)
        prompt = build_user_prompt(case, ws, level)
        assert "connection pool race" in prompt, f"pr_body missing from {level}"


def test_pr_body_truncated_when_long(tmp_path: Path) -> None:
    case = _make_case(pr_body="x" * 5000)
    patch = "diff --git a/f.rs b/f.rs\npatch\n"
    _materialize_test_workspace(tmp_path, case, patch, "diff-only")
    prompt = build_user_prompt(case, tmp_path, "diff-only")
    # Body should be truncated — full 5000 chars should not appear
    assert "x" * 5000 not in prompt
    # But some of it should be present
    assert "x" * 100 in prompt


def test_pr_commit_messages_included(tmp_path: Path) -> None:
    case = _make_case(pr_commit_messages=["fix: close pool before drain", "test: add race test"])
    patch = "diff --git a/f.rs b/f.rs\npatch\n"
    _materialize_test_workspace(tmp_path, case, patch, "diff-only")
    prompt = build_user_prompt(case, tmp_path, "diff-only")
    assert "close pool before drain" in prompt
    assert "add race test" in prompt


def test_diff_stat_included_when_stats_present(tmp_path: Path) -> None:
    case = _make_case(
        stats={
            "lines_added": 10,
            "lines_deleted": 5,
            "files_changed": 2,
            "hunks": 3,
        }
    )
    patch = "diff --git a/f.rs b/f.rs\npatch\n"
    _materialize_test_workspace(tmp_path, case, patch, "diff-only")
    prompt = build_user_prompt(case, tmp_path, "diff-only")
    assert "2" in prompt  # files changed count
    assert "10" in prompt  # lines added
    assert "5" in prompt  # lines deleted


def test_empty_pr_context_omitted_gracefully(tmp_path: Path) -> None:
    case = _make_case()  # no pr_title, pr_body, pr_commit_messages
    patch = "diff --git a/f.rs b/f.rs\npatch\n"
    _materialize_test_workspace(tmp_path, case, patch, "diff-only")
    prompt = build_user_prompt(case, tmp_path, "diff-only")
    # Should not have empty sections or "None"
    assert "None" not in prompt
    assert "Commits\n\n###" not in prompt


def test_prompt_framed_as_pr_review(tmp_path: Path) -> None:
    """Prompt should frame the task as PR review, not just bug hunting."""
    case = _make_case()
    patch = "diff --git a/f.rs b/f.rs\npatch\n"
    _materialize_test_workspace(tmp_path, case, patch, "diff-only")
    prompt = build_user_prompt(case, tmp_path, "diff-only")
    lower = prompt.lower()
    assert "pull request" in lower or "pr review" in lower or "code review" in lower


def test_system_prompt_mentions_completeness_and_style() -> None:
    """System prompt should cover more than just bugs."""
    prompt = load_agent_prompt(config_dir=Path("config"))
    lower = prompt.lower()
    assert "security" in lower
    assert "completeness" in lower or "incomplete" in lower or "missing" in lower


# ---------------------------------------------------------------------------
# Opaque case ID — case.id must not appear in prompts
# ---------------------------------------------------------------------------


def test_case_id_not_in_prompt_diff_only(tmp_path: Path) -> None:
    """case.id reveals the repo name — must not appear in any prompt."""
    case = _make_case()
    patch = "diff --git a/f.rs b/f.rs\n- old\n+ new\n"
    _materialize_test_workspace(tmp_path, case, patch, "diff-only")
    prompt = build_user_prompt(case, tmp_path, "diff-only")
    assert case.id not in prompt
    assert "aleo-lang" not in prompt.lower()


def test_case_id_not_in_prompt_diff_plus_repo(tmp_path: Path) -> None:
    case = _make_case()
    patch = "diff --git a/f.rs b/f.rs\npatch content\n"
    _materialize_test_workspace(tmp_path, case, patch, "diff+repo")
    prompt = build_user_prompt(case, tmp_path, "diff+repo")
    assert case.id not in prompt
    assert "aleo-lang" not in prompt.lower()


def test_case_id_not_in_prompt_diff_plus_repo_plus_domain(
    tmp_path: Path,
) -> None:
    case = _make_case()
    patch = "diff --git a/f.rs b/f.rs\npatch content\n"
    _materialize_test_workspace(tmp_path, case, patch, "diff+repo+domain")
    prompt = build_user_prompt(case, tmp_path, "diff+repo+domain")
    assert case.id not in prompt
    assert "aleo-lang" not in prompt.lower()
