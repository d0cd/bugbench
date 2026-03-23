# Runbook

## Quick Start

Full pipeline from dataset construction through analysis:

```bash
# 1. Dataset construction
bugeval mine --repo ProvableHQ/leo --output-dir cases/leo
bugeval blame --cases-dir cases/leo --repo-dir ./repos/leo
bugeval ground-truth --cases-dir cases/leo --repo-dir ./repos/leo
bugeval validate --cases-dir cases/leo --repo-dir ./repos/leo
bugeval clean-cases --repo ProvableHQ/leo --cases-dir cases/leo

# 2. Evaluation (PR tools need --org for forks)
bugeval evaluate --tool copilot --cases-dir cases/leo --run-dir results/run-001 --repo-dir ./repos/leo --org bug-tools-eval
bugeval evaluate --tool greptile --cases-dir cases/leo --run-dir results/run-001 --repo-dir ./repos/leo --org bug-tools-eval
bugeval evaluate --tool coderabbit --cases-dir cases/leo --run-dir results/run-001 --repo-dir ./repos/leo --org bug-tools-eval
bugeval evaluate --tool agent --cases-dir cases/leo --run-dir results/run-001 --repo-dir ./repos/leo --context diff+repo

# 3. Scoring and analysis
bugeval score --run-dir results/run-001 --cases-dir cases/leo
bugeval analyze --run-dir results/run-001 --cases-dir cases/leo
```

## GitHub Org Setup

The evaluation org is `bug-tools-eval`. Each tool gets its own isolated repo per source repo (e.g., `bug-tools-eval/leo-copilot`).

- `ensure_tool_repo()` creates per-tool repos lazily (e.g., `leo-copilot`, `leo-greptile`, `leo-coderabbit`)
- Repos must be **public** for free-tier tool access
- Each PR tool needs its GitHub App installed on the org
- `--org bug-tools-eval` must be passed to `bugeval evaluate` for all PR-based tools

### App installation

| Tool | GitHub App | Trigger |
|------|-----------|---------|
| Copilot | GitHub Copilot (native) | Automatic on PR open (can take 10+ min) |
| CodeRabbit | `coderabbitai` | Automatic on PR open |
| Greptile | `greptile-apps` | Comment `@greptile` on PR (auto-triggered by runner) |

### Greptile setup

Greptile requires **manual dashboard configuration** for each new repo before it
will review PRs. There is no public API to automate this.

1. Log in to the [Greptile dashboard](https://app.greptile.com/)
2. Go to **Repositories** and click **Add Repository**
3. Enter the full repo path (e.g., `bug-tools-eval/leo-greptile`)
4. Wait for Greptile to finish indexing (check the status badge)
5. Verify by opening a test PR and commenting `@greptile` — it should respond
   within 2-5 minutes

Repeat for each `{repo}-greptile` repo in the org. Indexing typically takes
5-15 minutes per repo depending on size.

### Parallel PR tool execution

PR tools share a local repo clone for `git checkout` and `git push`, which causes lock contention if run in parallel. **Create per-tool clones:**

```bash
# One-time setup per source repo
git clone https://github.com/ProvableHQ/leo.git repos/leo
for tool in copilot greptile coderabbit; do
  git clone --local repos/leo repos/leo-$tool
done

# Run all tools in parallel (each uses its own clone)
for tool in copilot greptile coderabbit; do
  uv run bugeval evaluate \
    --tool $tool \
    --cases-dir cases/leo \
    --run-dir results/run-pr-tools \
    --repo-dir repos/leo-$tool \
    --org bug-tools-eval \
    --timeout 900 \
    --concurrency 1 &
done
wait
```

## Dashboard

`bugeval dashboard` launches a local Flask web UI for experiment management and dataset review.

| Page | URL | Purpose |
|------|-----|---------|
| Home | `/` | Dataset stats: by repo, by kind (bug/clean), by blame confidence tier, by validation status |
| Cases | `/cases` | Filterable/sortable case browser with v2 fields (kind, blame_confidence, validation) |
| Case Detail | `/cases/<id>` | Ground truth (buggy_lines), validation verdicts, PR relations, introducing PR metadata |
| Runs | `/runs` | Run list with result/score counts; experiment grouping |
| Run Detail | `/runs/<id>` | Per-run notes, links to metrics/scores |
| Golden Set | `/golden` | Case confirmation workflow: confirm/dispute with coverage stats |
| Metrics | `/metrics/<run>` | Catch rate, false alarm rate, SNR, contamination warnings, tool comparison table, charts |
| Compare | `/compare` | Run comparison |

State is stored in sidecar files (no database): experiments in `results/experiments.yaml`, run notes in `.notes.json`, golden set in `cases/.golden_set.json`, human scores in `run_dir/human_scores/`.

## Docker Agent Evaluation

The Agent SDK runner can execute inside Docker, giving the agent access to Bash, `rg`, `cargo`, and other tools safely sandboxed. Docker is a transparent wrapper — the same SDK code runs inside the container as would run locally.

### Setup

```bash
# Build the Docker image (Rust toolchain + Claude Code CLI + Agent SDK)
docker build -t bugeval-agent-v2 .

# Authenticate Claude Code inside Docker (interactive, one-time)
docker run -it \
  -v bugeval-claude-auth:/home/agent/.claude \
  -e CLAUDE_CONFIG_DIR=/home/agent/.claude \
  bugeval-agent-v2 claude /login
```

**Requirements:** Docker (Orbstack recommended on macOS). The image needs ~2.8GB. Each container uses ~300-500MB RAM at runtime.

### Running

```bash
# Sonnet with Docker (Bash/rg access)
uv run bugeval evaluate \
  --tool agent-sdk \
  --cases-dir cases/leo \
  --run-dir results/run-docker-sonnet \
  --context diff+repo \
  --repo-dir repos/leo \
  --concurrency 5 \
  --timeout 300 \
  --max-turns 20 \
  --docker \
  --docker-image bugeval-agent-v2

# Opus with Docker
uv run bugeval evaluate \
  --tool agent-sdk \
  --cases-dir cases/leo \
  --run-dir results/run-docker-opus \
  --context diff+repo \
  --repo-dir repos/leo \
  --concurrency 5 \
  --timeout 300 \
  --max-turns 20 \
  --model claude-opus-4-6 \
  --docker \
  --docker-image bugeval-agent-v2
```

### Architecture

- **`--docker` flag** on `bugeval evaluate` routes SDK tools through Docker at the orchestrator level (`evaluate.py`). Runners are Docker-unaware.
- Inside Docker, `_docker_runner.py` runs the Agent SDK with `ClaudeSDKClient`, capturing turn-by-turn transcripts (tool calls, text, cost).
- **Synthesis continuation:** If the agent exhausts its turns without producing JSON findings, a synthesis prompt is automatically sent in the same session (90s reserved budget). This recovered 7/9 failures in testing.
- **Prompt:** Docker agents get an enhanced prompt (`bash_enabled=True`) describing Bash, `rg`, `cargo` tools. Non-Docker agents get read-only tools (Read, Glob, Grep, WebSearch).
- **Workspace:** Cloned repos are mounted at `/work` inside the container. Workspaces must be under the project directory (not `/tmp`) for Docker volume mounts to work.

### Concurrency and resources

| Orbstack memory | Max concurrency | Notes |
|-----------------|-----------------|-------|
| 2 GB | 1 | Containers OOM at concurrency 2+ |
| 8 GB | 3 | Safe for most workloads |
| 16 GB | 5-7 | Recommended |

### Outputs

```
results/run-*/
├── results/*.yaml         # ToolResult: comments, cost, time
├── transcripts/*.json     # Turn-by-turn SDK messages (tool_use, text, cost)
├── scores/*.yaml          # LLM judge scores (after `bugeval score`)
└── workspaces/            # Repo clones (can be cleaned up after run)
```

### Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| Exit code -9 (SIGKILL) | OOM — too many concurrent containers | Reduce `--concurrency` or increase Orbstack memory |
| Empty workspace inside container | Mount from `/tmp` fails on some Docker runtimes | Workspaces must be under project dir (evaluate does this automatically) |
| `docker: Error response from daemon: pull access denied` | Image not built on current Docker runtime | Rebuild: `docker build -t bugeval-agent-v2 .` |
| Auth failure inside container | Claude Code not logged in | Re-run: `docker run -it -v bugeval-claude-auth:/home/agent/.claude ... claude /login` |
| Agent explores forever (100+ messages) | Large PR, agent doesn't self-terminate | Synthesis continuation handles this; also consider `--max-turns 15` |
| Docker + diff-only produces empty results | `workspace=None` mounts `/dev/null` as `/work` | Not yet supported. Use `--context diff+repo` with Docker, or SDK without `--docker` for diff-only |

## Two-Phase PR Evaluation (Recommended)

The preferred approach for PR tools (copilot, greptile, coderabbit) is two-phase:
open all PRs first, let tools review asynchronously, then scrape.

```bash
# Phase 1: Open PRs (fast, ~2 min for 58 cases × 3 tools)
for tool in copilot greptile coderabbit; do
  uv run bugeval open-prs \
    --tool $tool \
    --cases-dir cases/leo \
    --run-dir results/run-pr-tools \
    --repo-dir repos/leo \
    --org bug-tools-eval \
    --concurrency 1 &
done
wait

# Wait for tools to review (5-30 min depending on tool)

# Phase 2: Scrape reviews (re-run until all reviewed)
uv run bugeval scrape-prs \
  --run-dir results/run-pr-tools \
  --cases-dir cases/leo \
  --org bug-tools-eval \
  --no-close    # keep PRs open until all tools have reviewed

# When all reviewed, close PRs
uv run bugeval scrape-prs \
  --run-dir results/run-pr-tools \
  --cases-dir cases/leo \
  --org bug-tools-eval \
  --close

# Clean up orphaned PRs/branches from failed runs
uv run bugeval cleanup-prs --org bug-tools-eval --dry-run
uv run bugeval cleanup-prs --org bug-tools-eval
```

**Preflight checks:** `open-prs` validates cases exist, repo is valid, GitHub
auth works, and sample commits are reachable — before touching GitHub.

**Re-run safety:** `open-prs` never overwrites results with `pr_state=pending-review`.
Error results are automatically retried on re-run.

**Retry:** All `gh` CLI calls retry 3× with exponential backoff on transient
errors (timeouts, 500s, rate limits).

## CLI Reference

| Command | Purpose |
|---------|---------|
| `bugeval mine` | Scrape fix PRs from GitHub, build initial TestCase YAMLs |
| `bugeval blame` | Find introducing commits via git blame |
| `bugeval ground-truth` | Build ground truth via diff intersection |
| `bugeval curate` | Auto-exclude bad cases (bumps, CI fixes, features, etc.) |
| `bugeval validate` | Cross-model validation (Claude + Gemini) |
| `bugeval clean-cases` | Generate negative control cases (clean PRs) |
| `bugeval evaluate` | Run a tool against test cases (agents + single-phase PR) |
| `bugeval open-prs` | Phase 1: Open PRs for PR tools (no waiting) |
| `bugeval scrape-prs` | Phase 2: Scrape reviews from open PRs |
| `bugeval cleanup-prs` | Close orphaned PRs and delete stale branches |
| `bugeval score` | Mechanical catch rate + LLM quality judge |
| `bugeval analyze` | Statistics, comparison tables, charts |
| `bugeval dashboard` | Local Flask web UI for experiment management |
| `bugeval add-case` | Manually add a case from a fix PR URL |
