# bugbench

**Evaluation framework for AI code review tools.** Measures how well commercial services and in-house agents detect real bugs — on your codebase, with your code.

Mines historical bug-fix PRs, reconstructs the introducing changes, presents them to each tool as if reviewing the original PR, and scores detection accuracy against ground truth.

> **Pilot (67 cases from [ProvableHQ/leo](https://github.com/ProvableHQ/leo)):** In-house Opus agent detects 31% of bugs vs Copilot at 24%, with higher review quality (2.25 vs 1.94) and a third fewer false positives.

**[View the presentation](https://d0cd.github.io/bugbench/presentation.html)** | **[Launch the dashboard](#dashboard)** for interactive case exploration

## Architecture

```
                     ┌──────────────────────────────────┐
                     │       Dataset Construction       │
                     │                                  │
  Fix PRs ──→ mine ──→ blame ──→ ground-truth ──→ curate
                     │                                  │
                     └──────────────┬───────────────────┘
                                    │
                              cases/*.yaml
                                    │
                     ┌──────────────▼───────────────────┐
                     │         Evaluation Layer          │
                     │                                  │
                     │  ┌──────────┐  ┌──────────────┐  │
                     │  │ PR Tools │  │ Agent Runners │  │
                     │  │──────────│  │──────────────│  │
                     │  │ Copilot  │  │ Claude API   │  │
                     │  │ Greptile │  │ Gemini API   │  │
                     │  │CodeRabbit│  │ OpenAI API   │  │
                     │  │          │  │ Claude CLI   │  │
                     │  │          │  │ Gemini CLI   │  │
                     │  │          │  │ Codex CLI    │  │
                     │  │          │  │ Claude SDK   │  │
                     │  └──────────┘  └──────────────┘  │
                     └──────────────┬───────────────────┘
                                    │
                              results/run-*/
                                    │
                     ┌──────────────▼───────────────────┐
                     │     Scoring & Analysis Layer      │
                     │                                  │
                     │  score (mechanical + LLM judge)  │
                     │  analyze (stats, charts, tables) │
                     │  dashboard (web UI)              │
                     └──────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [GitHub CLI](https://cli.github.com/) (`gh`) — authenticated
- Docker (optional, for isolated agent runs)

### Setup

```bash
# Install dependencies
uv sync

# Install optional providers
uv sync --extra sdk      # Claude Agent SDK
uv sync --extra google   # Gemini support
uv sync --extra openai   # OpenAI support

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Verify setup
uv run bugbench --help
```

### Run the Pipeline

```bash
# 1. Mine bug-fix PRs from a repository
uv run bugbench mine --repo ProvableHQ/leo --limit 50

# 2. Find introducing commits
uv run bugbench blame --cases-dir cases --repo-dir repos/leo

# 3. Compute ground truth (buggy lines)
uv run bugbench ground-truth --cases-dir cases --repo-dir repos/leo

# 4. Curate dataset (exclude bad cases)
uv run bugbench curate --cases-dir cases

# 5. Evaluate a tool
uv run bugbench evaluate --tool agent --context-level diff-only

# 6. Score results
uv run bugbench score --run-dir results/run-2026-03-22

# 7. Analyze and generate reports
uv run bugbench analyze --run-dir results/run-2026-03-22
```

## Tools Evaluated

### Commercial (PR-based)

These tools review code through GitHub's PR interface. The framework manages the full lifecycle: fork repo, create branch, open PR, wait for review, scrape comments, close PR.

| Tool | Runner | Command |
|------|--------|---------|
| GitHub Copilot | `copilot_runner.py` | `bugbench evaluate --tool copilot` |
| Greptile | `greptile_runner.py` | `bugbench evaluate --tool greptile` |
| CodeRabbit | `coderabbit_runner.py` | `bugbench evaluate --tool coderabbit` |

### In-House Agents (API)

Same tools, prompts, and execution engine — only the model differs. Measures **model capability**.

| Tool | Model | Command |
|------|-------|---------|
| Claude | `claude-sonnet-4-6` | `bugbench evaluate --tool agent` |
| Gemini | `gemini-2.5-flash` | `bugbench evaluate --tool agent-gemini` |
| OpenAI | `o4-mini` | `bugbench evaluate --tool agent-openai` |

### In-House Agents (CLI)

Each vendor's CLI with its own system prompt and agent loop. Measures **product capability**.

| Tool | Binary | Command |
|------|--------|---------|
| Claude Code | `claude` | `bugbench evaluate --tool agent-cli-claude` |
| Gemini CLI | `gemini` | `bugbench evaluate --tool agent-cli-gemini` |
| Codex CLI | `codex` | `bugbench evaluate --tool agent-cli-codex` |

### In-House Agent (SDK)

| Tool | Command |
|------|---------|
| Claude Agent SDK | `bugbench evaluate --tool agent-sdk` |
| Claude Agent SDK (2-pass) | `bugbench evaluate --tool agent-sdk-2pass` |
| Claude Agent SDK (v3) | `bugbench evaluate --tool agent-sdk-v3` |

## CLI Reference

```
bugbench mine           Scrape fix PRs, build test cases
bugbench blame          Find introducing commits via git blame
bugbench ground-truth   Compute buggy lines from diff intersection
bugbench curate         Auto-exclude bad cases (LLM-powered)
bugbench clean-cases    Generate negative control cases
bugbench validate       Cross-model ground truth validation
bugbench add-case       Manually add a case from a PR URL
bugbench evaluate       Run a tool against test cases
bugbench open-prs       Phase 1: Open PRs for PR-based tools
bugbench scrape-prs     Phase 2: Scrape reviews from open PRs
bugbench cleanup-prs    Close orphaned PRs and stale branches
bugbench score          Mechanical + LLM judge scoring
bugbench analyze        Generate stats, tables, and charts
bugbench dashboard      Launch local web review dashboard
```

All commands support `--help` for detailed options. Most support `--dry-run` for safe testing.

## Scoring

**Bug Detection (0-3):** How accurately the tool identified the known bug.

| Score | Meaning |
|-------|---------|
| 0 | Missed — no relevant comment |
| 1 | Wrong area — commented on the right file but wrong location |
| 2 | Correct identification — found the bug |
| 3 | Correct identification + suggested fix |

**Review Quality (0-4):** Overall usefulness of the review.

| Score | Meaning |
|-------|---------|
| 0 | Useless |
| 1 | Shallow — surface-level comments only |
| 2 | Adequate — some useful observations |
| 3 | Strong — thorough, actionable review |
| 4 | Exceptional — catches subtle issues with clear reasoning |

**Comment Classification:** Each comment is tagged as TP-expected, TP-novel, FP, or low-value.

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
    # Call your tool's API with the diff
    findings = call_my_tool_api(diff)

    comments = [
        Comment(file=f["file"], line=f["line"], body=f["message"])
        for f in findings
    ]

    return ToolResult(
        case_id=case.id,
        tool="my-tool",
        context_level=context_level,
        comments=comments,
        time_seconds=elapsed,
        cost_usd=cost,
    )
```

### 2. Register in config

Add your tool to `config/config.yaml`:

```yaml
tools:
  my-tool:
    type: api
    display_name: My Tool
    timeout_seconds: 300
```

### 3. Add dispatch

In `src/bugeval/evaluate.py`, add your tool to the runner dispatch:

```python
elif tool == "my-tool":
    from bugeval.my_tool_runner import run_my_tool
    result = run_my_tool(case, diff, workspace, context_level, repo_dir, timeout)
```

### 4. Run

```bash
bugbench evaluate --tool my-tool --cases-dir cases --run-dir results/run-mytool
bugbench score --run-dir results/run-mytool --cases-dir cases
bugbench analyze --run-dir results/run-mytool --cases-dir cases
```

The scoring, judging, and analysis pipeline is tool-agnostic — it works on any `ToolResult`.

## Project Structure

```
cases/              Test case YAML definitions (immutable during runs)
patches/            git format-patch outputs
src/bugeval/        Python package — all source code
tests/              pytest test suite (960+ tests)
results/            Run outputs (gitignored)
config/             config.yaml, prompt templates
docs/               Experiment design, analysis, reports
  analysis.md       Detailed case-by-case findings
  experiment-design.md   Full methodology
  pilot-report-*.md      Results from pilot runs
  presentation.html      Stakeholder presentation
  runbook.md             Operational procedures
  future-work.md         Extension proposals
```

## Key Documents

| Document | Purpose |
|----------|---------|
| [Experiment Design](docs/experiment-design.md) | Full methodology (11 sections) |
| [Pilot Report](docs/pilot-report-2026-03-22.md) | Pilot results and scoring methodology |
| [Analysis](docs/analysis.md) | Case-by-case findings and tool comparison |
| [Runbook](docs/runbook.md) | Operational procedures and troubleshooting |
| [Future Work](docs/future-work.md) | SWE-bench style patch generation extension |
| [Presentation](https://d0cd.github.io/bugbench/presentation.html) | Stakeholder presentation |
| [Architectural Decisions](docs/architectural-decisions.md) | Design rationale and key findings |
| [Audit](docs/audit.md) | Codebase audit and handoff checklist |

## Dashboard

Local web UI for exploring cases, reviewing ground truth, and comparing tool results.

```bash
uv run bugbench dashboard --port 5050
```

Features: case browser with filtering, case detail with code-level buggy line viewer, golden set review workflow (confirm/dispute + notes), run metrics with tool comparison tables, and experiment grouping.

## Development

```bash
# Run all checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest

# Launch dashboard
uv run bugbench dashboard
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
