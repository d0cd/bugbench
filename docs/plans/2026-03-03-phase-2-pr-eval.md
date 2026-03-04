# Phase 2: PR-Mode Evaluation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement `manage-forks` and `run-pr-eval` CLI commands that orchestrate commercial PR-review tools (CodeRabbit, BugBot, Augment, DeepSource, Graphite Diamond) against the bug-fix test cases built in Phase 1.

**Architecture:** Two commands. `manage-forks` handles GitHub fork lifecycle (create/verify/sync/delete) once per eval cycle. `run-pr-eval` is the evaluation loop: for each (case × PR-tool) pair, it creates a branch at `base_commit`, applies the bug-introducing patch, opens a PR on the fork, polls until the tool reviews, scrapes comments, then closes and cleans up. State is checkpointed to disk so interrupted runs can resume. Tools run concurrently via asyncio; cases run sequentially within each tool to stay inside GitHub rate limits.

**Tech Stack:** Python asyncio, Click, Pydantic v2, PyYAML, subprocess (`gh` CLI + git), existing `git_utils.run_git`, existing `github_scraper.run_gh`.

**Key invariant:** Patches are raw `git diff` output (not `git am` format). Use `git apply` to apply them on the branch.

---

## Task 1: Run State and Config Models

**Files:**
- Create: `src/bugeval/pr_eval_models.py`
- Create: `tests/test_pr_eval_models.py`

### Step 1: Write the failing tests

```python
# tests/test_pr_eval_models.py
"""Unit tests for Phase 2 run state and config models."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from bugeval.pr_eval_models import (
    CaseToolState,
    CaseToolStatus,
    EvalConfig,
    RunState,
    ToolDef,
    load_eval_config,
)


def test_case_tool_state_defaults() -> None:
    state = CaseToolState(case_id="aleo-001", tool="coderabbit")
    assert state.status == CaseToolStatus.pending
    assert state.pr_number is None
    assert state.branch_name is None
    assert state.error is None


def test_run_state_get_set() -> None:
    run = RunState(run_id="run-2026-03-03")
    state = CaseToolState(case_id="aleo-001", tool="coderabbit", status=CaseToolStatus.done)
    run.set_state("aleo-001", "coderabbit", state)
    got = run.get_state("aleo-001", "coderabbit")
    assert got.status == CaseToolStatus.done


def test_run_state_missing_pair_returns_pending() -> None:
    run = RunState(run_id="run-test")
    got = run.get_state("nonexistent", "tool")
    assert got.status == CaseToolStatus.pending


def test_run_state_yaml_round_trip(tmp_path: Path) -> None:
    run = RunState(run_id="run-test")
    run.set_state(
        "c001",
        "bugbot",
        CaseToolState(case_id="c001", tool="bugbot", status=CaseToolStatus.failed, error="timeout"),
    )
    path = tmp_path / "checkpoint.yaml"
    run.save(path)
    loaded = RunState.load(path)
    assert loaded.run_id == "run-test"
    pair = loaded.get_state("c001", "bugbot")
    assert pair.status == CaseToolStatus.failed
    assert pair.error == "timeout"


def test_tool_def_is_pr_tool() -> None:
    pr = ToolDef(name="coderabbit", type="pr", github_app="coderabbit-ai", org="my-org", cooldown_seconds=30)
    api = ToolDef(name="greptile", type="api", github_app="greptile", org="", cooldown_seconds=30)
    assert pr.is_pr_tool
    assert not api.is_pr_tool


def test_eval_config_pr_tools_only() -> None:
    cfg = EvalConfig(
        eval_org="my-org",
        tools=[
            ToolDef(name="coderabbit", type="pr", github_app="coderabbit-ai", org="my-org", cooldown_seconds=30),
            ToolDef(name="greptile", type="api", github_app="greptile", org="", cooldown_seconds=30),
            ToolDef(name="bugbot", type="pr", github_app="linear-bugbot", org="my-org", cooldown_seconds=30),
        ],
        repos={"aleo-lang": "provable-org/aleo-lang"},
    )
    assert len(cfg.pr_tools) == 2
    assert {t.name for t in cfg.pr_tools} == {"coderabbit", "bugbot"}


def test_load_eval_config_from_yaml(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("""\
github:
  eval_org: test-org
tools:
  - name: coderabbit
    type: pr
    github_app: coderabbit-ai
    org: test-org
    cooldown_seconds: 30
  - name: greptile
    type: api
    github_app: greptile
    org: ""
    cooldown_seconds: 30
repos:
  aleo-lang: provable-org/aleo-lang
""")
    cfg = load_eval_config(cfg_path)
    assert cfg.eval_org == "test-org"
    assert len(cfg.tools) == 2
    assert cfg.repos == {"aleo-lang": "provable-org/aleo-lang"}
    assert len(cfg.pr_tools) == 1
    assert cfg.pr_tools[0].name == "coderabbit"
```

### Step 2: Run to verify failure

Run: `uv run pytest tests/test_pr_eval_models.py -v`
Expected: `ModuleNotFoundError: No module named 'bugeval.pr_eval_models'`

### Step 3: Implement `src/bugeval/pr_eval_models.py`

```python
"""Models for Phase 2 run state, config, and checkpoint management."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class CaseToolStatus(StrEnum):
    pending = "pending"
    branching = "branching"
    applying = "applying"
    pr_open = "pr_open"
    polling = "polling"
    scraping = "scraping"
    closing = "closing"
    done = "done"
    failed = "failed"


class CaseToolState(BaseModel):
    case_id: str
    tool: str
    status: CaseToolStatus = CaseToolStatus.pending
    pr_number: int | None = None
    branch_name: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunState(BaseModel):
    run_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    pairs: dict[str, CaseToolState] = Field(default_factory=dict)

    def _key(self, case_id: str, tool: str) -> str:
        return f"{case_id}::{tool}"

    def get_state(self, case_id: str, tool: str) -> CaseToolState:
        return self.pairs.get(self._key(case_id, tool), CaseToolState(case_id=case_id, tool=tool))

    def set_state(self, case_id: str, tool: str, state: CaseToolState) -> None:
        self.pairs[self._key(case_id, tool)] = state

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(mode="json"), f, sort_keys=False)

    @classmethod
    def load(cls, path: Path) -> "RunState":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)


class ToolDef(BaseModel):
    name: str
    type: str  # pr | api | cli
    github_app: str | None = None
    org: str | None = None
    cooldown_seconds: int = 30

    @property
    def is_pr_tool(self) -> bool:
        return self.type == "pr"


class EvalConfig(BaseModel):
    eval_org: str
    tools: list[ToolDef]
    repos: dict[str, str]  # short_name -> github_path

    @property
    def pr_tools(self) -> list[ToolDef]:
        return [t for t in self.tools if t.is_pr_tool]


def load_eval_config(path: Path) -> EvalConfig:
    """Load eval configuration from config.yaml."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return EvalConfig(
        eval_org=data.get("github", {}).get("eval_org", ""),
        tools=[ToolDef(**t) for t in data.get("tools", [])],
        repos=data.get("repos", {}),
    )
```

### Step 4: Run tests to verify passing

Run: `uv run pytest tests/test_pr_eval_models.py -v`
Expected: 7 PASS

### Step 5: Type check

Run: `uv run pyright src/bugeval/pr_eval_models.py`
Expected: 0 errors

### Step 6: Commit

```bash
git add src/bugeval/pr_eval_models.py tests/test_pr_eval_models.py
git commit -m "feat: add Phase 2 run state models and eval config loader"
```

---

## Task 2: manage-forks Command

**Files:**
- Create: `src/bugeval/manage_forks.py`
- Create: `tests/test_manage_forks.py`

### Step 1: Write the failing tests

```python
# tests/test_manage_forks.py
"""Tests for manage-forks CLI command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

import pytest
from click.testing import CliRunner

from bugeval.manage_forks import fork_name, manage_forks


def _write_config(tmp_path: Path, *, eval_org: str = "test-org") -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""\
github:
  eval_org: {eval_org}
tools:
  - name: coderabbit
    type: pr
    github_app: coderabbit-ai
    org: {eval_org}
    cooldown_seconds: 30
  - name: greptile
    type: api
    github_app: greptile
    org: ""
    cooldown_seconds: 30
repos:
  aleo-lang: provable-org/aleo-lang
""")
    return cfg


def test_fork_name_format() -> None:
    assert fork_name("provable-org/aleo-lang", "coderabbit") == "aleo-lang-coderabbit"


def test_fork_name_with_dotted_repo() -> None:
    assert fork_name("owner/my.repo", "bugbot") == "my.repo-bugbot"


def test_help() -> None:
    runner = CliRunner()
    result = runner.invoke(manage_forks, ["--help"])
    assert result.exit_code == 0
    assert "--action" in result.output


def test_create_dry_run(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(manage_forks, ["--config", str(cfg), "--action", "create", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert "aleo-lang-coderabbit" in result.output


def test_create_calls_gh(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with patch("bugeval.manage_forks.run_gh") as mock_gh:
        mock_gh.return_value = "{}"
        runner = CliRunner()
        result = runner.invoke(manage_forks, ["--config", str(cfg), "--action", "create"])
    assert result.exit_code == 0
    mock_gh.assert_called_once()
    args = mock_gh.call_args[0]
    assert "repo" in args and "fork" in args
    assert "provable-org/aleo-lang" in args
    assert "--org" in args
    assert "test-org" in args


def test_create_skips_api_tools(tmp_path: Path) -> None:
    """Only PR-type tools get forks created."""
    cfg = _write_config(tmp_path)
    with patch("bugeval.manage_forks.run_gh") as mock_gh:
        mock_gh.return_value = "{}"
        runner = CliRunner()
        runner.invoke(manage_forks, ["--config", str(cfg), "--action", "create"])
    # greptile is api type — only one gh call (for coderabbit)
    assert mock_gh.call_count == 1


def test_tool_filter(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with patch("bugeval.manage_forks.run_gh") as mock_gh:
        mock_gh.return_value = "{}"
        runner = CliRunner()
        result = runner.invoke(
            manage_forks, ["--config", str(cfg), "--action", "create", "--tool", "coderabbit"]
        )
    assert result.exit_code == 0
    assert mock_gh.call_count == 1


def test_cleanup_dry_run(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(manage_forks, ["--config", str(cfg), "--action", "cleanup", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert "delete" in result.output.lower()


def test_missing_eval_org_exits(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, eval_org="")
    runner = CliRunner()
    result = runner.invoke(manage_forks, ["--config", str(cfg), "--action", "create"])
    assert result.exit_code != 0


def test_no_repos_configured(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""\
github:
  eval_org: test-org
tools:
  - name: coderabbit
    type: pr
    github_app: coderabbit-ai
    org: test-org
    cooldown_seconds: 30
repos: {}
""")
    runner = CliRunner()
    result = runner.invoke(manage_forks, ["--config", str(cfg), "--action", "create"])
    assert result.exit_code == 0
    assert "No repos" in result.output
```

### Step 2: Run to verify failure

Run: `uv run pytest tests/test_manage_forks.py -v`
Expected: `ModuleNotFoundError: No module named 'bugeval.manage_forks'`

### Step 3: Implement `src/bugeval/manage_forks.py`

```python
"""CLI command: manage-forks — GitHub org and fork lifecycle management."""
from __future__ import annotations

from pathlib import Path

import click

from bugeval.github_scraper import GhError, run_gh
from bugeval.pr_eval_models import EvalConfig, ToolDef, load_eval_config


def fork_name(repo: str, tool: str) -> str:
    """Return the fork repo name for a (repo, tool) pair."""
    return f"{repo.split('/')[-1]}-{tool}"


def _create_fork(eval_org: str, repo: str, tool: ToolDef, dry_run: bool) -> None:
    fname = fork_name(repo, tool.name)
    if dry_run:
        click.echo(f"  DRY RUN: gh repo fork {repo} --org {eval_org} --fork-name {fname}")
        return
    try:
        run_gh("repo", "fork", repo, "--org", eval_org, "--fork-name", fname, "--clone=false")
        click.echo(f"  CREATED {eval_org}/{fname}")
    except GhError as e:
        if "already exists" in str(e).lower():
            click.echo(f"  EXISTS  {eval_org}/{fname}")
        else:
            click.echo(f"  FAIL    {eval_org}/{fname}: {e}", err=True)


def _verify_fork(eval_org: str, repo: str, tool: ToolDef) -> None:
    fname = fork_name(repo, tool.name)
    fork_repo = f"{eval_org}/{fname}"
    try:
        run_gh("api", f"repos/{fork_repo}")
        click.echo(f"  OK      {fork_repo} exists")
    except GhError:
        click.echo(f"  MISSING {fork_repo} — run 'create' first")
        return
    if tool.github_app:
        try:
            run_gh("api", f"repos/{fork_repo}/installation")
            click.echo(f"  OK      {tool.github_app} app installed on {fork_repo}")
        except GhError:
            click.echo(f"  WARN    {tool.github_app} not installed on {fork_repo}")


def _sync_fork(eval_org: str, repo: str, tool: ToolDef, dry_run: bool) -> None:
    fname = fork_name(repo, tool.name)
    fork_repo = f"{eval_org}/{fname}"
    if dry_run:
        click.echo(f"  DRY RUN: gh repo sync {fork_repo}")
        return
    try:
        run_gh("repo", "sync", fork_repo)
        click.echo(f"  SYNCED  {fork_repo}")
    except GhError as e:
        click.echo(f"  FAIL    {fork_repo}: {e}", err=True)


def _delete_fork(eval_org: str, repo: str, tool: ToolDef, dry_run: bool) -> None:
    fname = fork_name(repo, tool.name)
    fork_repo = f"{eval_org}/{fname}"
    if dry_run:
        click.echo(f"  DRY RUN: delete {fork_repo}")
        return
    try:
        run_gh("repo", "delete", fork_repo, "--yes")
        click.echo(f"  DELETED {fork_repo}")
    except GhError as e:
        click.echo(f"  FAIL    {fork_repo}: {e}", err=True)


@click.command("manage-forks")
@click.option(
    "--config",
    "config_path",
    default="config/config.yaml",
    show_default=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to config.yaml.",
)
@click.option(
    "--action",
    type=click.Choice(["create", "verify", "sync", "cleanup"]),
    required=True,
    help="Lifecycle action to perform.",
)
@click.option("--tool", "tool_filter", default=None, help="Limit to a specific tool name.")
@click.option("--dry-run", is_flag=True, help="Print actions without executing.")
def manage_forks(config_path: Path, action: str, tool_filter: str | None, dry_run: bool) -> None:
    """Manage GitHub org forks for PR-mode tool evaluation."""
    cfg = load_eval_config(config_path)

    if not cfg.eval_org:
        click.echo("Error: github.eval_org is not set in config.yaml", err=True)
        raise SystemExit(1)

    pr_tools = cfg.pr_tools
    if tool_filter:
        pr_tools = [t for t in pr_tools if t.name == tool_filter]
        if not pr_tools:
            click.echo(f"Error: tool '{tool_filter}' not found or not PR type", err=True)
            raise SystemExit(1)

    if not cfg.repos:
        click.echo("No repos configured in config.yaml.")
        return

    for repo_short, repo_path in cfg.repos.items():
        click.echo(f"\n{repo_path}:")
        for tool in pr_tools:
            if action == "create":
                _create_fork(cfg.eval_org, repo_path, tool, dry_run)
            elif action == "verify":
                _verify_fork(cfg.eval_org, repo_path, tool)
            elif action == "sync":
                _sync_fork(cfg.eval_org, repo_path, tool, dry_run)
            elif action == "cleanup":
                _delete_fork(cfg.eval_org, repo_path, tool, dry_run)
```

### Step 4: Run tests to verify passing

Run: `uv run pytest tests/test_manage_forks.py -v`
Expected: 9 PASS

### Step 5: Commit

```bash
git add src/bugeval/manage_forks.py tests/test_manage_forks.py
git commit -m "feat: add manage-forks CLI command"
```

---

## Task 3: PR Lifecycle Helpers

**Files:**
- Create: `src/bugeval/pr_lifecycle.py`
- Create: `tests/test_pr_lifecycle.py`

### Step 1: Write the failing tests

```python
# tests/test_pr_lifecycle.py
"""Tests for PR lifecycle helpers."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from bugeval.models import Category, Difficulty, ExpectedFinding, PRSize, Severity, TestCase
from bugeval.pr_lifecycle import (
    PrLifecycleError,
    apply_patch_to_branch,
    close_pr_delete_branch,
    make_branch_name,
    open_pr,
    poll_for_review,
    scrape_review_comments,
)


def _make_case() -> TestCase:
    return TestCase(
        id="aleo-001",
        repo="provable-org/aleo-lang",
        base_commit="abc123",
        head_commit="def456",
        fix_commit="ghi789",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.small,
        description="Off-by-one error in loop bounds",
        expected_findings=[],
    )


def test_make_branch_name_format() -> None:
    name = make_branch_name("aleo-001", "coderabbit")
    assert name == "bugeval/aleo-001-coderabbit"


def test_make_branch_name_sanitizes_chars() -> None:
    name = make_branch_name("aleo/001+x", "code rabbit!")
    assert name.startswith("bugeval/")
    assert " " not in name
    assert "+" not in name
    assert len(name) <= 80


def test_apply_patch_to_branch_calls_git(tmp_path: Path) -> None:
    patch_file = tmp_path / "aleo-001.patch"
    patch_file.write_text("--- a/x.rs\n+++ b/x.rs\n@@ -1 +1 @@\n-old\n+new\n")
    with patch("bugeval.pr_lifecycle.run_git") as mock_git:
        mock_git.return_value = ""
        apply_patch_to_branch(
            branch="bugeval/aleo-001-coderabbit",
            base_commit="abc123",
            patch_path=patch_file,
            fork_url="https://github.com/test-org/aleo-lang-coderabbit.git",
            cwd=tmp_path,
        )
    assert mock_git.call_count >= 3  # checkout, apply, push


def test_open_pr_dry_run_returns_none() -> None:
    case = _make_case()
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        result = open_pr(
            fork_repo="test-org/aleo-lang-coderabbit",
            upstream_repo="provable-org/aleo-lang",
            branch="bugeval/aleo-001-coderabbit",
            case=case,
            dry_run=True,
        )
    mock_gh.assert_not_called()
    assert result is None


def test_open_pr_returns_pr_number() -> None:
    case = _make_case()
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.return_value = "https://github.com/test-org/aleo-lang-coderabbit/pull/42\n"
        pr_num = open_pr(
            fork_repo="test-org/aleo-lang-coderabbit",
            upstream_repo="provable-org/aleo-lang",
            branch="bugeval/aleo-001-coderabbit",
            case=case,
            dry_run=False,
        )
    assert pr_num == 42


def test_poll_for_review_timeout_returns_false() -> None:
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.return_value = "[]"
        result = poll_for_review(
            fork_repo="test-org/aleo-lang-coderabbit",
            pr_number=42,
            timeout_seconds=0,
            poll_interval=0.01,
        )
    assert result is False


def test_poll_for_review_detects_review() -> None:
    reviews = [{"id": 1, "state": "COMMENTED", "body": "Looks buggy here"}]
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.return_value = json.dumps(reviews)
        result = poll_for_review(
            fork_repo="test-org/aleo-lang-coderabbit",
            pr_number=42,
            timeout_seconds=10,
            poll_interval=0.01,
        )
    assert result is True


def test_scrape_review_comments_combines_sources() -> None:
    reviews = [{"id": 1, "state": "COMMENTED", "body": "PR-level comment"}]
    inline = [{"id": 2, "body": "inline comment", "path": "src/lib.rs", "line": 42}]
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        mock_gh.side_effect = [json.dumps(reviews), json.dumps(inline)]
        comments = scrape_review_comments("test-org/aleo-lang-coderabbit", 42)
    assert len(comments) == 2


def test_close_pr_delete_branch_dry_run() -> None:
    with patch("bugeval.pr_lifecycle.run_gh") as mock_gh:
        close_pr_delete_branch(
            fork_repo="test-org/aleo-lang-coderabbit",
            pr_number=42,
            branch="bugeval/aleo-001-coderabbit",
            dry_run=True,
        )
    mock_gh.assert_not_called()
```

Add `from pathlib import Path` at the top of the test file.

### Step 2: Run to verify failure

Run: `uv run pytest tests/test_pr_lifecycle.py -v`
Expected: `ModuleNotFoundError: No module named 'bugeval.pr_lifecycle'`

### Step 3: Implement `src/bugeval/pr_lifecycle.py`

```python
"""PR lifecycle helpers for PR-mode evaluation."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from bugeval.git_utils import GitError, run_git
from bugeval.github_scraper import GhError, run_gh
from bugeval.models import TestCase


class PrLifecycleError(Exception):
    """Raised when a PR lifecycle operation fails."""


def make_branch_name(case_id: str, tool: str) -> str:
    """Create a safe git branch name for a case×tool pair (max 80 chars)."""
    slug = re.sub(r"[^a-z0-9\-]", "-", f"{case_id}-{tool}".lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"bugeval/{slug}"[:80]


def apply_patch_to_branch(
    branch: str,
    base_commit: str,
    patch_path: Path,
    fork_url: str,
    cwd: Path,
) -> None:
    """Checkout a new branch at base_commit, apply patch, push to fork_url."""
    try:
        run_git("checkout", "-b", branch, base_commit, cwd=cwd)
        run_git("apply", str(patch_path), cwd=cwd)
        run_git("add", "-A", cwd=cwd)
        run_git("commit", "-m", f"[bugeval] apply bug patch", cwd=cwd)
        run_git("push", fork_url, f"HEAD:{branch}", cwd=cwd)
    except GitError as e:
        raise PrLifecycleError(f"Branch/apply/push failed: {e}") from e


def open_pr(
    fork_repo: str,
    upstream_repo: str,
    branch: str,
    case: TestCase,
    dry_run: bool,
) -> int | None:
    """Open a PR on fork_repo. Returns PR number or None if dry_run."""
    if dry_run:
        return None

    owner, _name = upstream_repo.split("/", 1)
    output = run_gh(
        "pr", "create",
        "--repo", fork_repo,
        "--head", branch,
        "--base", "main",
        "--title", f"[bugeval] {case.id}: {case.description[:60]}",
        "--body", (
            f"Automated evaluation PR.\n\n"
            f"**Case:** `{case.id}`  \n"
            f"**Category:** {case.category}  \n"
            f"**Severity:** {case.severity}  \n"
        ),
    )
    match = re.search(r"/pull/(\d+)", output)
    if not match:
        raise PrLifecycleError(f"Could not parse PR number from: {output!r}")
    return int(match.group(1))


def poll_for_review(
    fork_repo: str,
    pr_number: int,
    timeout_seconds: float,
    poll_interval: float = 30.0,
) -> bool:
    """Poll until a review appears or timeout expires. Returns True if found."""
    owner, name = fork_repo.split("/", 1)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            output = run_gh("api", f"repos/{owner}/{name}/pulls/{pr_number}/reviews")
            reviews: list[dict[str, Any]] = json.loads(output)
            if reviews:
                return True
        except (GhError, json.JSONDecodeError):
            pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))
    return False


def scrape_review_comments(fork_repo: str, pr_number: int) -> list[dict[str, Any]]:
    """Fetch PR-level reviews and inline review comments."""
    owner, name = fork_repo.split("/", 1)
    base = f"repos/{owner}/{name}/pulls/{pr_number}"
    all_comments: list[dict[str, Any]] = []
    for endpoint in (f"{base}/reviews", f"{base}/comments"):
        try:
            all_comments.extend(json.loads(run_gh("api", endpoint)))
        except (GhError, json.JSONDecodeError):
            pass
    return all_comments


def close_pr_delete_branch(
    fork_repo: str,
    pr_number: int,
    branch: str,
    dry_run: bool,
) -> None:
    """Close PR and delete remote branch (best-effort; ignores errors)."""
    if dry_run:
        return
    try:
        run_gh("pr", "close", str(pr_number), "--repo", fork_repo, "--delete-branch")
    except GhError:
        pass  # best-effort cleanup
```

### Step 4: Run tests to verify passing

Run: `uv run pytest tests/test_pr_lifecycle.py -v`
Expected: 9 PASS

### Step 5: Commit

```bash
git add src/bugeval/pr_lifecycle.py tests/test_pr_lifecycle.py
git commit -m "feat: add PR lifecycle helpers"
```

---

## Task 4: run-pr-eval Orchestrator

**Files:**
- Create: `src/bugeval/run_pr_eval.py`
- Create: `tests/test_run_pr_eval.py`

### Step 1: Write the failing tests

```python
# tests/test_run_pr_eval.py
"""Tests for run-pr-eval async orchestrator."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from bugeval.models import Category, Difficulty, PRSize, Severity, TestCase
from bugeval.pr_eval_models import CaseToolState, CaseToolStatus, RunState, ToolDef
from bugeval.run_pr_eval import load_cases_for_run, make_run_id, process_case_tool, run_pr_eval


def _make_config(tmp_path: Path, *, eval_org: str = "test-org") -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""\
github:
  eval_org: {eval_org}
tools:
  - name: coderabbit
    type: pr
    github_app: coderabbit-ai
    org: {eval_org}
    cooldown_seconds: 0
repos:
  aleo-lang: provable-org/aleo-lang
""")
    return cfg


def _make_case() -> TestCase:
    return TestCase(
        id="aleo-001",
        repo="provable-org/aleo-lang",
        base_commit="abc",
        head_commit="def",
        fix_commit="ghi",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.small,
        description="test",
        expected_findings=[],
    )


def test_make_run_id_format() -> None:
    run_id = make_run_id()
    assert run_id.startswith("run-")
    assert len(run_id) == len("run-2026-03-03")


def test_load_cases_for_run_missing_dir(tmp_path: Path) -> None:
    cases = load_cases_for_run(tmp_path / "missing")
    assert cases == []


def test_load_cases_for_run_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    cases = load_cases_for_run(tmp_path / "cases")
    assert cases == []


def test_help() -> None:
    runner = CliRunner()
    result = runner.invoke(run_pr_eval, ["--help"])
    assert result.exit_code == 0
    assert "--cases-dir" in result.output
    assert "--run-dir" in result.output
    assert "--dry-run" in result.output


def test_dry_run_no_cases(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        run_pr_eval,
        ["--config", str(cfg), "--cases-dir", str(cases_dir), "--run-dir", str(tmp_path / "run"), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "No cases" in result.output


def test_checkpoint_created(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    run_dir = tmp_path / "run"
    runner = CliRunner()
    runner.invoke(
        run_pr_eval,
        ["--config", str(cfg), "--cases-dir", str(cases_dir), "--run-dir", str(run_dir), "--dry-run"],
    )
    # Even with no cases, run dir and checkpoint should be created
    assert run_dir.exists()


def test_process_case_tool_dry_run(tmp_path: Path) -> None:
    """Dry run should mark done without calling gh or git."""
    case = _make_case()
    tool = ToolDef(name="coderabbit", type="pr", github_app="coderabbit-ai", org="test-org", cooldown_seconds=0)
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()

    with patch("bugeval.run_pr_eval.apply_patch_to_branch") as mock_branch, \
         patch("bugeval.run_pr_eval.open_pr") as mock_pr:
        state = asyncio.run(
            process_case_tool(
                case=case,
                tool=tool,
                eval_org="test-org",
                patches_dir=patches_dir,
                run_dir=tmp_path / "run",
                repo_dir=None,
                dry_run=True,
            )
        )

    mock_branch.assert_not_called()
    mock_pr.assert_not_called()
    assert state.status == CaseToolStatus.done


def test_process_case_tool_missing_patch(tmp_path: Path) -> None:
    """Missing patch file → status=failed."""
    case = _make_case()
    tool = ToolDef(name="coderabbit", type="pr", github_app="coderabbit-ai", org="test-org", cooldown_seconds=0)
    state = asyncio.run(
        process_case_tool(
            case=case,
            tool=tool,
            eval_org="test-org",
            patches_dir=tmp_path / "patches",
            run_dir=tmp_path / "run",
            repo_dir=None,
            dry_run=False,
        )
    )
    assert state.status == CaseToolStatus.failed
    assert "patch" in (state.error or "").lower()


def test_done_cases_skipped_in_checkpoint(tmp_path: Path) -> None:
    """Cases already done in checkpoint are skipped."""
    cfg = _make_config(tmp_path)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    # Pre-populate checkpoint with a done pair
    from bugeval.pr_eval_models import CaseToolState, RunState, CaseToolStatus
    run_state = RunState(run_id="run-test")
    run_state.set_state(
        "aleo-001", "coderabbit",
        CaseToolState(case_id="aleo-001", tool="coderabbit", status=CaseToolStatus.done),
    )
    run_state.save(run_dir / "checkpoint.yaml")

    runner = CliRunner()
    result = runner.invoke(
        run_pr_eval,
        ["--config", str(cfg), "--cases-dir", str(cases_dir), "--run-dir", str(run_dir), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "Resuming" in result.output
```

### Step 2: Run to verify failure

Run: `uv run pytest tests/test_run_pr_eval.py -v`
Expected: `ModuleNotFoundError: No module named 'bugeval.run_pr_eval'`

### Step 3: Implement `src/bugeval/run_pr_eval.py`

```python
"""CLI command: run-pr-eval — async orchestrator for PR-mode tool evaluation."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import click

from bugeval.io import load_all_cases
from bugeval.models import TestCase
from bugeval.pr_eval_models import (
    CaseToolState,
    CaseToolStatus,
    RunState,
    ToolDef,
    load_eval_config,
)
from bugeval.pr_lifecycle import (
    PrLifecycleError,
    apply_patch_to_branch,
    close_pr_delete_branch,
    make_branch_name,
    open_pr,
    poll_for_review,
    scrape_review_comments,
)


def make_run_id() -> str:
    """Generate a run ID from the current date."""
    return f"run-{datetime.now().strftime('%Y-%m-%d')}"


def load_cases_for_run(cases_dir: Path) -> list[TestCase]:
    """Load all test cases from cases_dir, returning [] if dir missing."""
    if not cases_dir.exists():
        return []
    return load_all_cases(cases_dir)


async def process_case_tool(
    case: TestCase,
    tool: ToolDef,
    eval_org: str,
    patches_dir: Path,
    run_dir: Path,
    repo_dir: Path | None,
    dry_run: bool,
) -> CaseToolState:
    """Run the full lifecycle for one (case × tool) pair. Returns final state."""
    state = CaseToolState(case_id=case.id, tool=tool.name, started_at=datetime.now())

    fork_repo = f"{eval_org}/{case.repo.split('/')[-1]}-{tool.name}"
    fork_url = f"https://github.com/{fork_repo}.git"
    branch = make_branch_name(case.id, tool.name)
    patch_path = patches_dir / f"{case.id}.patch"
    out_dir = run_dir / "raw" / f"{case.id}-{tool.name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        click.echo(f"  DRY RUN {case.id} × {tool.name}")
        state.status = CaseToolStatus.done
        state.finished_at = datetime.now()
        return state

    if not patch_path.exists():
        state.status = CaseToolStatus.failed
        state.error = f"patch not found: {patch_path}"
        state.finished_at = datetime.now()
        return state

    try:
        state.status = CaseToolStatus.branching
        state.branch_name = branch
        if repo_dir is not None:
            apply_patch_to_branch(
                branch=branch,
                base_commit=case.base_commit,
                patch_path=patch_path,
                fork_url=fork_url,
                cwd=repo_dir,
            )

        state.status = CaseToolStatus.pr_open
        pr_number = open_pr(
            fork_repo=fork_repo,
            upstream_repo=case.repo,
            branch=branch,
            case=case,
            dry_run=False,
        )
        state.pr_number = pr_number

        state.status = CaseToolStatus.polling
        poll_for_review(
            fork_repo=fork_repo,
            pr_number=pr_number,  # type: ignore[arg-type]
            timeout_seconds=600,
            poll_interval=30,
        )

        state.status = CaseToolStatus.scraping
        comments = scrape_review_comments(fork_repo, pr_number)  # type: ignore[arg-type]
        (out_dir / "comments.json").write_text(json.dumps(comments, indent=2))

        state.status = CaseToolStatus.closing
        close_pr_delete_branch(
            fork_repo=fork_repo,
            pr_number=pr_number,  # type: ignore[arg-type]
            branch=branch,
            dry_run=False,
        )

        state.status = CaseToolStatus.done
        state.finished_at = datetime.now()
        click.echo(f"  DONE  {case.id} × {tool.name} (PR #{pr_number}, {len(comments)} comments)")

    except (PrLifecycleError, Exception) as e:
        state.status = CaseToolStatus.failed
        state.error = str(e)
        state.finished_at = datetime.now()
        click.echo(f"  FAIL  {case.id} × {tool.name}: {e}", err=True)

    return state


async def _eval_tool(
    tool: ToolDef,
    cases: list[TestCase],
    eval_org: str,
    patches_dir: Path,
    run_dir: Path,
    repo_dir: Path | None,
    run_state: RunState,
    checkpoint_path: Path,
    dry_run: bool,
) -> None:
    """Process all cases sequentially for one tool, checkpointing after each."""
    for case in cases:
        existing = run_state.get_state(case.id, tool.name)
        if existing.status == CaseToolStatus.done:
            click.echo(f"  SKIP  {case.id} × {tool.name} (already done)")
            continue

        state = await process_case_tool(
            case=case,
            tool=tool,
            eval_org=eval_org,
            patches_dir=patches_dir,
            run_dir=run_dir,
            repo_dir=repo_dir,
            dry_run=dry_run,
        )
        run_state.set_state(case.id, tool.name, state)
        run_state.save(checkpoint_path)

        if tool.cooldown_seconds > 0 and not dry_run:
            await asyncio.sleep(tool.cooldown_seconds)


@click.command("run-pr-eval")
@click.option(
    "--config",
    "config_path",
    default="config/config.yaml",
    show_default=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option("--cases-dir", default="cases", show_default=True, type=click.Path(path_type=Path))
@click.option("--patches-dir", default="patches", show_default=True, type=click.Path(path_type=Path))
@click.option(
    "--repo-dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Local git checkout used for branch/apply/push operations.",
)
@click.option(
    "--run-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Output directory (default: results/run-YYYY-MM-DD/).",
)
@click.option("--tools", "tool_filter", default=None, help="Comma-separated tool names (default: all PR tools).")
@click.option("--dry-run", is_flag=True, help="Skip polling; close PRs immediately.")
def run_pr_eval(
    config_path: Path,
    cases_dir: Path,
    patches_dir: Path,
    repo_dir: Path | None,
    run_dir: Path | None,
    tool_filter: str | None,
    dry_run: bool,
) -> None:
    """Async orchestrator for PR-mode commercial tool evaluation."""
    cfg = load_eval_config(config_path)

    pr_tools = cfg.pr_tools
    if tool_filter:
        names = {n.strip() for n in tool_filter.split(",")}
        pr_tools = [t for t in pr_tools if t.name in names]

    cases = load_cases_for_run(cases_dir)
    if not cases:
        click.echo("No cases found.")
        return

    effective_run_dir = run_dir or (Path("results") / make_run_id())
    effective_run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = effective_run_dir / "checkpoint.yaml"

    if checkpoint_path.exists():
        try:
            run_state = RunState.load(checkpoint_path)
            click.echo(f"Resuming run {run_state.run_id} from checkpoint.")
        except Exception:
            run_state = RunState(run_id=make_run_id())
    else:
        run_state = RunState(run_id=make_run_id())

    click.echo(
        f"Running {len(cases)} cases × {len(pr_tools)} PR tools → {effective_run_dir}"
    )

    async def _main() -> None:
        await asyncio.gather(*[
            _eval_tool(
                tool=tool,
                cases=cases,
                eval_org=cfg.eval_org,
                patches_dir=patches_dir,
                run_dir=effective_run_dir,
                repo_dir=repo_dir,
                run_state=run_state,
                checkpoint_path=checkpoint_path,
                dry_run=dry_run,
            )
            for tool in pr_tools
        ])

    asyncio.run(_main())
    click.echo(f"\nRun complete. Results in {effective_run_dir}/")
```

### Step 4: Run tests to verify passing

Run: `uv run pytest tests/test_run_pr_eval.py -v`
Expected: 10 PASS

### Step 5: Commit

```bash
git add src/bugeval/run_pr_eval.py tests/test_run_pr_eval.py
git commit -m "feat: add run-pr-eval async orchestrator"
```

---

## Task 5: CLI Wiring and Smoke Tests

**Files:**
- Modify: `src/bugeval/cli.py`
- Modify: `tests/test_cli.py`

### Step 1: Write the failing tests

Add to `tests/test_cli.py`:

```python
def test_manage_forks_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["manage-forks", "--help"])
    assert result.exit_code == 0
    assert "--action" in result.output


def test_run_pr_eval_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["run-pr-eval", "--help"])
    assert result.exit_code == 0
    assert "--cases-dir" in result.output
```

### Step 2: Run to verify failure

Run: `uv run pytest tests/test_cli.py::test_manage_forks_help tests/test_cli.py::test_run_pr_eval_help -v`
Expected: FAIL (commands not registered)

### Step 3: Update `src/bugeval/cli.py`

```python
"""Main CLI entry point."""

import click

from bugeval.curate import curate
from bugeval.extract_patch import extract_patch
from bugeval.manage_forks import manage_forks
from bugeval.run_pr_eval import run_pr_eval
from bugeval.scrape_github_cmd import scrape_github
from bugeval.validate_cases import validate_cases


@click.group()
def cli() -> None:
    """bugeval — AI code review tools evaluation framework."""


cli.add_command(scrape_github)
cli.add_command(validate_cases)
cli.add_command(extract_patch)
cli.add_command(curate)
cli.add_command(manage_forks)
cli.add_command(run_pr_eval)
```

### Step 4: Run all tests

Run: `uv run pytest -v`
Expected: all tests PASS (126 previous + ~35 new = ~161 total)

### Step 5: Run linting and type checking

Run: `uv run ruff check src/ tests/`
Expected: no issues

Run: `uv run pyright src/`
Expected: 0 errors

### Step 6: Commit

```bash
git add src/bugeval/cli.py tests/test_cli.py
git commit -m "feat: wire manage-forks and run-pr-eval into CLI"
```

---

## Build Order

```
Task 1: pr_eval_models.py     ← pure data, zero deps
Task 2: manage_forks.py       ← depends on pr_eval_models
Task 3: pr_lifecycle.py       ← depends on git_utils + github_scraper
Task 4: run_pr_eval.py        ← depends on 1, 3, io
Task 5: cli wiring            ← depends on all
```

Tasks 2 and 3 are independent once Task 1 is done — can be parallelized.

---

## Verification Checklist

- [ ] `uv run pytest` — all tests pass
- [ ] `uv run ruff check src/ tests/` — clean
- [ ] `uv run pyright src/` — 0 errors
- [ ] `uv run bugeval manage-forks --help` — shows options
- [ ] `uv run bugeval run-pr-eval --help` — shows options
- [ ] Dry run: `uv run bugeval manage-forks --config config/config.yaml --action create --dry-run` (needs repos in config)
- [ ] Dry run: `uv run bugeval run-pr-eval --cases-dir cases/ --dry-run`

---

## Notes for Executor

**Patch format:** `extract_patch` generates `git diff` output (unified diff), not `git am`-format. Use `git apply` (not `git am`) in `apply_patch_to_branch`.

**Git push auth:** `git push https://github.com/{fork}.git HEAD:{branch}` relies on `gh auth` credential helper being configured. Ensure `gh auth status` is valid before a real run.

**`--repo-dir` is optional:** If not provided, `process_case_tool` skips the branch/apply/push step and jumps straight to PR polling. This allows testing the polling/scraping path against pre-existing PRs.

**asyncio + Click:** `asyncio.run()` is called inside the Click command (synchronous entry point). This is intentional — Click does not support async commands natively.

**No new dependencies** were added. All imports are from stdlib or packages already in `pyproject.toml`.
