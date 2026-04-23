# bugbench

> **⚠️ Maintenance mode** (as of 2026-04-23). No new features or experiments planned. Bug fixes and dependency updates only. See [`docs/audit-2026-03-23.md`](docs/audit-2026-03-23.md) for the last full audit and [`docs/BACKLOG.md`](docs/BACKLOG.md) for out-of-scope work.

**Evaluation framework for AI code review tools.** Measures how well commercial services and in-house agents detect real bugs — on your codebase, with your code.

Mines historical bug-fix PRs, reconstructs the introducing changes, presents them to each tool as if reviewing the original PR, and scores detection accuracy against ground truth.

> **Pilot (67 cases from [ProvableHQ/leo](https://github.com/ProvableHQ/leo)):** In-house Opus agent detects 31% of bugs vs Copilot at 24%, with higher review quality (2.25 vs 1.94) and a third fewer false positives.

**[View the presentation](https://d0cd.github.io/bugbench/presentation.html)** | **[Launch the dashboard](#dashboard)** for interactive case exploration

**Pipeline:** `mine` → `blame` → `ground-truth` → `curate` → `evaluate` → `score` → `analyze` (see [experiment design](docs/experiment-design.md) for full architecture)

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [GitHub CLI](https://cli.github.com/) (`gh`) — authenticated
- Docker (optional, for isolated agent runs)

### Setup

```bash
uv sync                          # core dependencies
uv sync --extra sdk              # + Claude Agent SDK
uv sync --extra google           # + Gemini
uv sync --extra openai           # + OpenAI
uv sync --extra dashboard        # + Flask dashboard

cp .env.example .env             # configure API keys
uv run bugbench --help           # verify
```

### Run the Pipeline

```bash
# Build dataset
uv run bugbench mine --repo ProvableHQ/leo --limit 50
uv run bugbench blame --cases-dir cases --repo-dir repos/leo
uv run bugbench ground-truth --cases-dir cases --repo-dir repos/leo
uv run bugbench curate --cases-dir cases

# Evaluate
uv run bugbench evaluate --tool agent --context diff-only \
    --cases-dir cases --run-dir results/my-run

# Score and analyze
uv run bugbench score --run-dir results/my-run --cases-dir cases
uv run bugbench analyze --run-dir results/my-run --cases-dir cases
```

## Tools Evaluated

### Commercial (PR-based)

Review through GitHub's PR interface. The framework manages the full lifecycle: fork, branch, open PR, wait for review, scrape comments, close PR.

| Tool | Command |
|------|---------|
| GitHub Copilot | `bugbench evaluate --tool copilot` |
| Greptile | `bugbench evaluate --tool greptile` |
| CodeRabbit | `bugbench evaluate --tool coderabbit` |

### In-House Agents (API)

Same tools, prompts, and execution engine — only the model differs. Measures **model capability**.

| Tool | Default Model | Command |
|------|---------------|---------|
| Claude | `claude-sonnet-4-6` | `bugbench evaluate --tool agent` |
| Gemini | `gemini-2.5-flash` | `bugbench evaluate --tool agent-gemini` |
| OpenAI | `o4-mini` | `bugbench evaluate --tool agent-openai` |

### In-House Agents (CLI)

Each vendor's CLI with its own system prompt and agent loop. Measures **product capability**.

| Tool | Command |
|------|---------|
| Claude Code | `bugbench evaluate --tool agent-cli-claude` |
| Gemini CLI | `bugbench evaluate --tool agent-cli-gemini` |
| Codex CLI | `bugbench evaluate --tool agent-cli-codex` |

### In-House Agents (SDK)

| Tool | Strategy | Command |
|------|----------|---------|
| Claude Agent SDK | Single-pass | `bugbench evaluate --tool agent-sdk` |
| Claude Agent SDK (2-pass) | Explorer + reviewer | `bugbench evaluate --tool agent-sdk-2pass` |
| Claude Agent SDK (v3) | Survey + investigate + report | `bugbench evaluate --tool agent-sdk-v3` |

## CLI Reference

| Command | Purpose |
|---------|---------|
| `bugbench mine` | Scrape fix PRs, build test cases |
| `bugbench blame` | Find introducing commits via git blame |
| `bugbench ground-truth` | Compute buggy lines from diff intersection |
| `bugbench curate` | Auto-exclude bad cases (LLM-powered) |
| `bugbench clean-cases` | Generate negative control cases |
| `bugbench validate` | Cross-model ground truth validation |
| `bugbench add-case` | Manually add a case from a PR URL |
| `bugbench evaluate` | Run a tool against test cases |
| `bugbench open-prs` | Phase 1: Open PRs for PR-based tools |
| `bugbench scrape-prs` | Phase 2: Scrape reviews from open PRs |
| `bugbench cleanup-prs` | Close orphaned PRs and stale branches |
| `bugbench score` | Mechanical + LLM judge scoring |
| `bugbench analyze` | Stats, comparison tables, and charts |
| `bugbench dashboard` | Local web UI for experiment review |

All commands support `--help`. Most support `--dry-run`.

## Scoring

**Bug Detection (0–3)**

| Score | Meaning |
|-------|---------|
| 0 | Missed — no relevant comment |
| 1 | Wrong area — right file, wrong location |
| 2 | Correct identification — found the bug |
| 3 | Correct identification + suggested fix |

**Review Quality (0–4)**

| Score | Meaning |
|-------|---------|
| 0 | Useless |
| 1 | Shallow — surface-level only |
| 2 | Adequate — some useful observations |
| 3 | Strong — thorough, actionable |
| 4 | Exceptional — catches subtle issues with clear reasoning |

**Comment Classification:** TP-expected, TP-novel, FP, or low-value.

## Adding a New Tool

To evaluate a tool not already supported (e.g., Amazon CodeGuru, DeepSource):

### 1. Create a runner

Create `src/bugeval/my_tool_runner.py`. Your runner receives a test case and returns a `ToolResult`:

```python
from pathlib import Path
from bugeval.models import TestCase
from bugeval.result_models import Comment, ToolResult

def run_my_tool(
    case: TestCase,
    diff: str,
    workspace: Path | None,
    context_level: str,
    repo_dir: Path,
    timeout: int,
) -> ToolResult:
    findings = call_my_tool_api(diff)

    return ToolResult(
        case_id=case.id,
        tool="my-tool",
        context_level=context_level,
        comments=[
            Comment(file=f["file"], line=f["line"], body=f["message"])
            for f in findings
        ],
        time_seconds=elapsed,
        cost_usd=cost,
    )
```

### 2. Add dispatch

In `src/bugeval/evaluate.py`, add your tool to the runner dispatch:

```python
elif tool == "my-tool":
    from bugeval.my_tool_runner import run_my_tool
    result = run_my_tool(case, diff, workspace, context_level, repo_dir, timeout)
```

### 3. Run

```bash
bugbench evaluate --tool my-tool --cases-dir cases --run-dir results/run-mytool
bugbench score --run-dir results/run-mytool --cases-dir cases
bugbench analyze --run-dir results/run-mytool --cases-dir cases
```

The scoring, judging, and analysis pipeline is tool-agnostic — it works on any `ToolResult`.

## Dashboard

```bash
uv run bugbench dashboard --port 5050
```

Case browser with filtering, code-level buggy line viewer, golden set review workflow (confirm/dispute + notes), run metrics with tool comparison tables, and experiment grouping.

## Project Structure

```
src/bugeval/
  agent_runner.py       Shared agent utilities (prompts, tools, workspace)
  _anthropic_runner.py  Claude API multi-turn runner
  _gemini_runner.py     Gemini API runner
  _openai_runner.py     OpenAI API runner
  _cli_runners.py       CLI subprocess runners (claude, gemini, codex)
  _sdk_runner.py        Claude Agent SDK runner
  _two_pass.py          Two-pass and three-phase review architectures
  copilot_runner.py     GitHub Copilot PR lifecycle
  greptile_runner.py    Greptile PR lifecycle
  coderabbit_runner.py  CodeRabbit PR lifecycle
  pr_utils.py           Shared PR tool utilities
  evaluate.py           Evaluation orchestrator and dispatch
  score.py              Mechanical + LLM judge scoring
  analyze.py            Statistics, charts, comparison tables
  models.py             Core Pydantic schemas (TestCase, GroundTruth)
  result_models.py      ToolResult and Comment schemas
  score_models.py       CaseScore schema
  io.py                 YAML I/O with atomic writes
  mine.py               GitHub PR scraping and case construction
  blame.py              Git blame for introducing commits
  ground_truth.py       Diff intersection for buggy lines
  curate.py             LLM-powered case quality filtering
  validate.py           Cross-model ground truth validation
  dashboard.py          Flask web UI
  cli.py                Click CLI entry point

tests/                  963 tests, 82% coverage
cases/                  Test case YAML definitions (immutable during runs)
results/                Run outputs (gitignored)
config/                 config.yaml, domain prompts
docs/                   Experiment design, analysis, reports, presentation
```

## Key Documents

| Document | Purpose |
|----------|---------|
| [Experiment Design](docs/experiment-design.md) | Full methodology (11 sections) |
| [Pilot Report](docs/pilot-report-2026-03-22.md) | Pilot results and scoring methodology |
| [Analysis](docs/analysis.md) | Case-by-case findings and tool comparison |
| [Architectural Decisions](docs/architectural-decisions.md) | Design rationale and key findings |
| [Runbook](docs/runbook.md) | Operational procedures and troubleshooting |
| [Future Work](docs/future-work.md) | 8 experiment directions with priority order |
| [Presentation](https://d0cd.github.io/bugbench/presentation.html) | Stakeholder presentation |

## Development

```bash
uv run ruff check src/ tests/       # lint
uv run ruff format --check src/     # format
uv run pyright src/                  # type check
uv run pytest                        # test (963 tests)
```

## Environment Variables

See `.env.example` for the full list. Required:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `GITHUB_TOKEN` | GitHub API (or use `gh auth login`) |

Optional (for specific tools):

| Variable | Purpose |
|----------|---------|
| `GREPTILE_API_KEY` | Greptile API access |
| `OPENAI_API_KEY` | OpenAI API access |
| `GOOGLE_API_KEY` | Gemini API access |
| `EVAL_ORG` | GitHub org for fork management |
