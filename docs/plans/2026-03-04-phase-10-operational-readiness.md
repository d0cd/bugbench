# Phase 10: Operational Readiness — Pipeline Orchestration + Status + Docker Guard

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the evaluation pipeline operationally runnable — chain the post-run steps into one command, surface progress during a run, guard against misconfiguration before wasting API credits, and enforce Docker isolation for agent mode as specified in the experiment design.

**Architecture:** Four independent additions, all backward compatible. No new dependencies. `pipeline` wraps existing CLI commands as library calls. `status` reads checkpoint and scores dirs. `validate-env` introspects os.environ and config. Docker guard is a pre-flight check in `run_agent_eval.py`.

**Tech Stack:** Python 3.11+, click, pydantic, PyYAML (all already present), `subprocess` for Docker check.

---

## Background: What's missing

The pipeline today requires 3 manual commands after each eval run:
```bash
uv run bugeval normalize --run-dir results/run-xxx
uv run bugeval judge --run-dir results/run-xxx --cases-dir cases/
uv run bugeval analyze --run-dir results/run-xxx --cases-dir cases/
```
There is no way to know how far along a run is without opening checkpoint YAMLs manually.
The experiment design (Section 5) specifies Docker containers for agent isolation; `run-agent-eval` currently clones to a local temp dir with no Docker involvement.
No validation of environment before submitting 100+ API calls.

---

## Task 1: `validate-env` command

Check API keys, GitHub auth, and config completeness before starting a run. Exits non-zero if any required item is missing.

**Files:**
- Create: `src/bugeval/validate_env.py`
- Modify: `src/bugeval/cli.py` — add `cli.add_command(validate_env)`
- Create: `tests/test_validate_env.py`

**What it checks:**

| Check | How | Required? |
|-------|-----|-----------|
| `ANTHROPIC_API_KEY` in env | `os.environ.get` | Always |
| `GITHUB_TOKEN` in env | `os.environ.get` | Always |
| Tool API keys (e.g. `GREPTILE_API_KEY`) | From config `api_key_env` fields | Per-tool |
| `config.github.eval_org` non-empty | Read config | If PR tools present |
| `config.repos` non-empty | Read config | Always |
| At least one case YAML in `cases_dir` | Glob `*.yaml` | If `--cases-dir` provided |

**Implementation — `validate_env.py`:**

```python
"""CLI command: validate-env — pre-flight check of env vars and config."""

from __future__ import annotations

import os
from pathlib import Path

import click

from bugeval.pr_eval_models import EvalConfig, ToolType, load_eval_config


class EnvCheckResult:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.ok: list[str] = []

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


def check_env(config: EvalConfig, cases_dir: Path | None = None) -> EnvCheckResult:
    """Run all pre-flight checks. Returns an EnvCheckResult."""
    result = EnvCheckResult()

    # Always-required keys
    for key in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN"):
        if os.environ.get(key):
            result.ok.append(f"{key} is set")
        else:
            result.errors.append(f"{key} is not set")

    # Tool-specific API keys
    for tool in config.tools:
        if tool.api_key_env:
            if os.environ.get(tool.api_key_env):
                result.ok.append(f"{tool.api_key_env} is set (for {tool.name})")
            else:
                result.errors.append(f"{tool.api_key_env} not set (required for {tool.name})")

    # eval_org required if any PR tools configured
    pr_tools = [t for t in config.tools if t.type == ToolType.pr]
    if pr_tools:
        if config.github.eval_org:
            result.ok.append(f"eval_org = {config.github.eval_org!r}")
        else:
            result.errors.append("github.eval_org is empty but PR tools are configured")

    # repos must be non-empty
    if config.repos:
        result.ok.append(f"{len(config.repos)} repo(s) configured")
    else:
        result.warnings.append("config.repos is empty — no repos to evaluate")

    # optional: cases dir
    if cases_dir is not None and cases_dir.exists():
        yamls = list(cases_dir.glob("*.yaml"))
        if yamls:
            result.ok.append(f"{len(yamls)} case(s) found in {cases_dir}")
        else:
            result.warnings.append(f"No case YAML files in {cases_dir}")

    return result


@click.command("validate-env")
@click.option(
    "--config",
    "config_path",
    default="config/config.yaml",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to config.yaml",
)
@click.option(
    "--cases-dir",
    default=None,
    type=click.Path(dir_okay=True, file_okay=False, path_type=Path),
    help="Optional: directory to check for case YAML files",
)
def validate_env(config_path: str, cases_dir: Path | None) -> None:
    """Pre-flight check: verify env vars and config before starting a run."""
    config = load_eval_config(Path(config_path))
    result = check_env(config, cases_dir)

    for msg in result.ok:
        click.echo(f"  [ok]   {msg}")
    for msg in result.warnings:
        click.echo(f"  [warn] {msg}", err=True)
    for msg in result.errors:
        click.echo(f"  [fail] {msg}", err=True)

    if result.passed:
        click.echo("\nAll checks passed.")
    else:
        click.echo(f"\n{len(result.errors)} check(s) failed.", err=True)
        raise SystemExit(1)
```

**Tests — `tests/test_validate_env.py`:**

```python
"""Tests for validate-env command."""

from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from bugeval.cli import cli
from bugeval.pr_eval_models import EvalConfig, load_eval_config
from bugeval.validate_env import check_env


def _make_config(tmp_path: Path, tools=None, eval_org="", repos=None) -> Path:
    config_data = {
        "github": {"eval_org": eval_org},
        "tools": tools or [],
        "repos": repos or {},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(config_data))
    return p


def test_check_env_missing_api_key(tmp_path):
    config = load_eval_config(_make_config(tmp_path))
    with patch.dict("os.environ", {}, clear=True):
        result = check_env(config)
    assert not result.passed
    assert any("ANTHROPIC_API_KEY" in e for e in result.errors)
    assert any("GITHUB_TOKEN" in e for e in result.errors)


def test_check_env_all_present(tmp_path):
    config = load_eval_config(_make_config(tmp_path, repos={"repo": "org/repo"}))
    env = {"ANTHROPIC_API_KEY": "sk-ant-x", "GITHUB_TOKEN": "ghp_x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config)
    assert result.passed
    assert any("ANTHROPIC_API_KEY" in m for m in result.ok)


def test_check_env_pr_tool_missing_org(tmp_path):
    tools = [{"name": "coderabbit", "type": "pr", "cooldown_seconds": 0}]
    config = load_eval_config(_make_config(tmp_path, tools=tools, eval_org=""))
    env = {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config)
    assert not result.passed
    assert any("eval_org" in e for e in result.errors)


def test_check_env_tool_api_key_missing(tmp_path):
    tools = [{"name": "greptile", "type": "api",
              "api_endpoint": "https://api.greptile.com",
              "api_key_env": "GREPTILE_API_KEY", "cooldown_seconds": 0}]
    config = load_eval_config(_make_config(tmp_path, tools=tools))
    env = {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config)
    assert not result.passed
    assert any("GREPTILE_API_KEY" in e for e in result.errors)


def test_check_env_warns_empty_repos(tmp_path):
    config = load_eval_config(_make_config(tmp_path, repos={}))
    env = {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config)
    assert result.passed  # warnings don't fail
    assert any("repos" in w for w in result.warnings)


def test_check_env_cases_dir_empty(tmp_path):
    config = load_eval_config(_make_config(tmp_path))
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    env = {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=True):
        result = check_env(config, cases_dir=cases_dir)
    assert any("No case" in w for w in result.warnings)


def test_validate_env_cli_exits_nonzero_on_failure(tmp_path):
    config_path = _make_config(tmp_path)
    runner = CliRunner()
    with patch.dict("os.environ", {}, clear=True):
        result = runner.invoke(cli, ["validate-env", "--config", str(config_path)])
    assert result.exit_code != 0
```

---

## Task 2: `pipeline` command

Chain `normalize → judge → analyze` in one shot after an eval run completes. Each stage only runs if the previous one produced output. Supports `--dry-run`.

**Files:**
- Create: `src/bugeval/pipeline.py`
- Modify: `src/bugeval/cli.py` — add `cli.add_command(pipeline)`
- Create: `tests/test_pipeline.py`

**Implementation — `pipeline.py`:**

```python
"""CLI command: pipeline — normalize + judge + analyze in one shot."""

from __future__ import annotations

from pathlib import Path

import click

from bugeval.analyze import (
    aggregate_scores,
    generate_csv,
    generate_markdown,
    load_cases_lookup,
    load_normalized_lookup,
)
from bugeval.judge import judge_all  # (already exists — see below)
from bugeval.normalize import discover_raw_dirs, _parse_raw_dir_name, normalize_api_result, ...
```

**Important:** Rather than re-implementing each stage, `pipeline` imports and calls the existing functions directly (not via subprocess). This keeps it testable.

**Functions needed (all already exist or are trivially extractable):**

| Existing CLI | Core function to call |
|-------------|----------------------|
| `normalize` | `normalize_pr_result`, `normalize_api_result`, `normalize_agent_result`, `discover_raw_dirs` |
| `judge` | `run_judge_on_result` (extracted from judge.py — see below) |
| `analyze` | `aggregate_scores`, `generate_markdown`, `generate_csv`, `generate_charts` |

**Extracting a public function from `judge.py`:**

Currently `judge.py`'s CLI mixes loading and calling the LLM. We need to extract a `judge_normalized_results(run_dir, cases_dir, config_path, dry_run)` function. This is a **pure extraction** — no behavior change, just moving code the CLI already calls into a named function.

```python
# In judge.py — add this function above the @click.command:
def judge_normalized_results(
    run_dir: Path,
    cases_dir: Path,
    config_path: Path,
    dry_run: bool = False,
    model: str | None = None,
) -> int:
    """Run LLM judge on all normalized results in run_dir. Returns count of scored pairs."""
    # Move the body of the `judge` click command here.
    # The CLI command becomes a thin wrapper that calls this.
    ...
```

**Pipeline CLI:**

```python
@click.command("pipeline")
@click.option("--run-dir", required=True,
              type=click.Path(exists=True, dir_okay=True, file_okay=False),
              help="Path to completed run directory")
@click.option("--config", "config_path", default="config/config.yaml", show_default=True,
              type=click.Path(exists=True, dir_okay=False))
@click.option("--cases-dir", default="cases/", show_default=True,
              type=click.Path(dir_okay=True, file_okay=False))
@click.option("--context-level", default="diff-only", show_default=True,
              type=click.Choice(["diff-only", "diff+repo", "diff+repo+domain"]))
@click.option("--no-charts", is_flag=True, default=False, help="Skip chart generation")
@click.option("--dry-run", is_flag=True, default=False)
def pipeline(run_dir, config_path, cases_dir, context_level, no_charts, dry_run):
    """Normalize → judge → analyze a completed eval run in one shot."""
    resolved = Path(run_dir)
    click.echo("=== Stage 1: normalize ===")
    _run_normalize(resolved, Path(config_path), context_level, dry_run)
    click.echo("=== Stage 2: judge ===")
    _run_judge(resolved, Path(cases_dir), Path(config_path), dry_run)
    click.echo("=== Stage 3: analyze ===")
    _run_analyze(resolved, Path(cases_dir), no_charts)
    click.echo("Pipeline complete.")
```

**Tests — `tests/test_pipeline.py`:**

```python
def test_pipeline_help():  # --help exits 0, shows all options

def test_pipeline_dry_run_no_output_files():
    # Setup: raw dir with findings.json + config
    # Run: pipeline --dry-run
    # Assert: no *.yaml files written in run_dir, no scores/ dir created

def test_pipeline_normalize_stage():
    # Setup: raw dir with findings.json
    # Run: pipeline (not dry-run) with mock judge
    # Assert: normalized YAML created

def test_pipeline_skips_judge_if_no_normalized():
    # Setup: empty run dir (no raw/)
    # Run: pipeline
    # Assert: "No raw output directories" printed, exits 0

def test_pipeline_full_chain(tmp_path):
    # Setup: synthetic findings.json + config + cases
    # Mock judge LLM call
    # Assert: report.md + scores.csv created

def test_pipeline_no_charts_flag():
    # Assert: --no-charts passes through to analyze stage
```

---

## Task 3: `status` command

Show a per-run progress table: how many (case × tool) pairs are done, how many scored, whether analysis exists. Reads checkpoint.yaml and counts scores/*.yaml and analysis/report.md.

**Files:**
- Create: `src/bugeval/status_cmd.py`
- Modify: `src/bugeval/cli.py` — add `cli.add_command(status)`
- Create: `tests/test_status_cmd.py`

**Output format:**

```
Run: results/run-2026-03-04
  Checkpoint:   12/20 done (8 failed, 0 pending)
  Normalized:   11 results
  Scored:        9 results
  Analysis:     yes (results/run-2026-03-04/analysis/report.md)
```

**Implementation — `status_cmd.py`:**

```python
"""CLI command: status — show pipeline progress for a run directory."""

from __future__ import annotations

from pathlib import Path

import click
import yaml

from bugeval.pr_eval_models import CaseToolStatus, RunState


def get_run_status(run_dir: Path) -> dict:
    """Collect progress stats from a run directory. Returns a dict."""
    checkpoint_path = run_dir / "checkpoint.yaml"
    run_state = RunState.load(checkpoint_path) if checkpoint_path.exists() else RunState()

    states = list(run_state._states.values()) if run_state._states else []  # noqa: SLF001
    # Note: RunState stores states internally; read via public interface
    total = len(states)
    done = sum(1 for s in states if s.status == CaseToolStatus.done)
    failed = sum(1 for s in states if s.status == CaseToolStatus.failed)
    pending = total - done - failed

    normalized_count = len(list(run_dir.glob("*.yaml"))) - (
        1 if (run_dir / "checkpoint.yaml").exists() else 0
    )
    scores_dir = run_dir / "scores"
    scored_count = len(list(scores_dir.glob("*.yaml"))) if scores_dir.exists() else 0
    report_path = run_dir / "analysis" / "report.md"

    return {
        "run_dir": str(run_dir),
        "total": total,
        "done": done,
        "failed": failed,
        "pending": pending,
        "normalized": normalized_count,
        "scored": scored_count,
        "has_analysis": report_path.exists(),
        "report_path": str(report_path) if report_path.exists() else None,
    }


@click.command("status")
@click.option("--run-dir", required=True,
              type=click.Path(exists=True, dir_okay=True, file_okay=False),
              help="Path to a run directory")
def status(run_dir: str) -> None:
    """Show pipeline progress for a run directory."""
    info = get_run_status(Path(run_dir))
    click.echo(f"Run: {info['run_dir']}")
    click.echo(f"  Checkpoint:  {info['done']}/{info['total']} done "
               f"({info['failed']} failed, {info['pending']} pending)")
    click.echo(f"  Normalized:  {info['normalized']} results")
    click.echo(f"  Scored:      {info['scored']} results")
    analysis = f"yes ({info['report_path']})" if info["has_analysis"] else "no"
    click.echo(f"  Analysis:    {analysis}")
```

**Note on `RunState` internal access:** Check whether `RunState` exposes a public way to iterate states. If not, add a `states()` method that returns `list[CaseToolState]`. This is a 2-line addition to `pr_eval_models.py`.

**Tests — `tests/test_status_cmd.py`:**

```python
def test_status_help():  # exits 0, shows --run-dir

def test_status_empty_run_dir(tmp_path):
    # No checkpoint, no *.yaml files
    # Assert: shows 0/0 done, 0 normalized

def test_status_with_checkpoint(tmp_path):
    # Pre-seed checkpoint with 2 done, 1 failed
    # Assert: "2/3 done (1 failed, 0 pending)"

def test_status_counts_normalized_yaml(tmp_path):
    # Write 3 *.yaml + checkpoint.yaml
    # Assert: "Normalized: 3 results" (checkpoint excluded)

def test_status_counts_scores(tmp_path):
    # Write scores/case-001-tool.yaml + scores/case-002-tool.yaml
    # Assert: "Scored: 2 results"

def test_status_shows_analysis_when_present(tmp_path):
    # Write analysis/report.md
    # Assert: "Analysis: yes"

def test_get_run_status_returns_dict(tmp_path):
    # Unit test of the function directly
    info = get_run_status(tmp_path)
    assert info["total"] == 0
    assert not info["has_analysis"]
```

---

## Task 4: Docker guard in `run-agent-eval`

Add a pre-flight check before running agent mode: if `claude-code-cli` or `anthropic-api` tools are active and Docker is not running, print a clear warning. Add `--require-docker` flag that exits non-zero if Docker is unavailable.

**Files:**
- Modify: `src/bugeval/run_agent_eval.py` — add `is_docker_available()`, call before run
- Modify: `tests/test_run_agent_eval.py` — 3 new tests

**Implementation:**

```python
# In run_agent_eval.py:

import subprocess


def is_docker_available() -> bool:
    """Return True if Docker daemon is reachable via `docker info`."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
```

Wire into the CLI:

```python
@click.option(
    "--require-docker",
    is_flag=True,
    default=False,
    help="Exit with error if Docker daemon is not reachable.",
)
def run_agent_eval(..., require_docker: bool) -> None:
    if not is_docker_available():
        msg = "Docker daemon not reachable. Agent runs clone to a local temp dir (no container isolation)."
        if require_docker:
            click.echo(f"Error: {msg}", err=True)
            raise SystemExit(1)
        click.echo(f"Warning: {msg}", err=True)
    ...
```

**Tests:**

```python
def test_is_docker_available_returns_bool():
    # Just verify the return type — don't depend on Docker being present
    from bugeval.run_agent_eval import is_docker_available
    result = is_docker_available()
    assert isinstance(result, bool)


def test_run_agent_eval_warns_without_docker(tmp_path):
    # Mock is_docker_available to return False
    # Run with a case + dry-run
    # Assert: "Warning: Docker" in stderr output


def test_run_agent_eval_exits_with_require_docker(tmp_path):
    # Mock is_docker_available to return False
    # Run with --require-docker
    # Assert: exit_code != 0 and "Error" in stderr
```

---

## Task 5: `RunState.states()` public method

The `status` command needs to iterate all `CaseToolState` objects from a `RunState`. Currently the internal dict is accessed privately. Add a public method.

**Files:**
- Modify: `src/bugeval/pr_eval_models.py` — add `states()` method to `RunState`
- Modify: `tests/test_pr_eval_models.py` — 1 new test

```python
# In RunState class:
def states(self) -> list[CaseToolState]:
    """Return all stored states."""
    return list(self._states.values())
```

Then update `status_cmd.py` to use `run_state.states()` instead of `run_state._states.values()`.

**Test:**
```python
def test_run_state_states_method():
    rs = RunState()
    rs.set(CaseToolState(case_id="c1", tool="t1"))
    rs.set(CaseToolState(case_id="c2", tool="t1"))
    assert len(rs.states()) == 2
    assert all(isinstance(s, CaseToolState) for s in rs.states())
```

---

## Task 6: `judge_normalized_results` extraction from `judge.py`

The `pipeline` command needs to call judge logic without re-parsing CLI args. Extract a callable function from the judge CLI handler.

**Files:**
- Modify: `src/bugeval/judge.py` — extract `judge_normalized_results()`, make CLI call it
- Modify: `tests/test_judge.py` — 2 new tests for the extracted function

**Before (conceptual):**
```python
@click.command("judge")
def judge(run_dir, cases_dir, config_path, dry_run, model):
    # all the logic here
    ...
```

**After:**
```python
def judge_normalized_results(
    run_dir: Path,
    cases_dir: Path,
    config_path: Path,
    dry_run: bool = False,
    model: str | None = None,
) -> int:
    """Judge all normalized results in run_dir. Returns count of results scored."""
    # all the logic moved here
    ...

@click.command("judge")
def judge(run_dir, cases_dir, config_path, dry_run, model):
    count = judge_normalized_results(
        Path(run_dir), Path(cases_dir), Path(config_path), dry_run, model
    )
    click.echo(f"Judged {count} result(s).")
```

**Tests:**
```python
def test_judge_normalized_results_dry_run(tmp_path):
    # Write a normalized YAML + config + cases
    # Call judge_normalized_results(dry_run=True)
    # Assert: returns 0 or count, no score YAMLs written

def test_judge_normalized_results_returns_count(tmp_path):
    # Write normalized YAML + mock LLM
    # Assert: return value equals number of (case x tool) pairs scored
```

---

## Build Order

Tasks 1, 3, 4, 5 are fully independent — do them in parallel.
Task 5 must complete before Task 3 (status uses `RunState.states()`).
Task 6 must complete before Task 2 (pipeline calls `judge_normalized_results`).

```
Task 5 (RunState.states)  ──► Task 3 (status)
Task 6 (judge extract)    ──► Task 2 (pipeline)
Task 1 (validate-env)     ──► (no deps)
Task 4 (docker guard)     ──► (no deps)
```

**Batch 1:** Tasks 1, 4, 5 (all independent, small)
**Batch 2:** Tasks 3, 6 (depend on 5 and 4 respectively)
**Batch 3:** Task 2 (pipeline — depends on 6)

---

## Verification

```bash
# Run all tests
uv run pytest -q                               # ~360+ tests pass

# Linting and types
uv run ruff check src/ tests/                  # clean
uv run ruff format --check src/ tests/         # clean
uv run pyright src/                            # 0 errors

# New commands appear in help
uv run bugeval --help                          # shows validate-env, pipeline, status
uv run bugeval validate-env --help             # shows --config, --cases-dir
uv run bugeval pipeline --help                 # shows --run-dir, --dry-run, --no-charts
uv run bugeval status --help                   # shows --run-dir
uv run bugeval run-agent-eval --help           # shows --require-docker

# Smoke test validate-env (will warn, not fail, on missing keys)
uv run bugeval validate-env --config config/config.yaml 2>&1 || true
```

---

## Test Count Estimate

| Task | New tests |
|------|-----------|
| Task 1: validate-env | 7 |
| Task 2: pipeline | 6 |
| Task 3: status | 7 |
| Task 4: docker guard | 3 |
| Task 5: RunState.states() | 1 |
| Task 6: judge extraction | 2 |
| **Total** | **~26** |

Target: ~362+ tests passing.
