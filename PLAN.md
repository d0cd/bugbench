# Build Plan

Ordered implementation plan for the bug-tools-eval pipeline. Each phase lists scripts, their inputs/outputs, and key design decisions. Phases 0–1 are prerequisites; later phases can be developed in parallel once schemas are defined.

---

## Phase 0: Project Skeleton ✓

**Status:** Complete

- `pyproject.toml` — uv, click, pyyaml, pydantic, anthropic, matplotlib
- Directory structure: `cases/`, `patches/`, `src/bugeval/`, `tests/`, `results/`, `config/`, `docs/`
- `config/config.yaml` — tool definitions, repo mappings, scoring config
- `.env.example` — API key template
- `src/bugeval/cli.py` — Click entry point
- `.github/workflows/ci.yml` — lint + typecheck + test

---

## Phase 1: Dataset Construction

### Script: `mine_candidates`

**Purpose:** Scan git history for bug-fix commits/PRs to use as test cases.

**Input:** Repo path or URL, branch name

**Output:** `candidates/{repo}.yaml` — ranked list of candidates

**Logic:**
- Heuristic signals: commit message keywords (`fix`, `bug`, `regression`, `issue #`), linked issue tags, fix-after-change patterns (file touched within N commits of introduction)
- Per candidate: base commit, head (bug-introducing) commit, fix commit, diff, files changed, PR description
- LLM fallback (Anthropic API) for linking fix commits back to introducing commits
- Confidence score 0.0–1.0 based on signal strength

**Design decisions:**
- Output is a ranked YAML list so humans can review and promote to `cases/`
- LLM is optional fallback, not default — keeps costs low during bulk scanning

---

### Script: `extract_patch`

**Purpose:** Generate `.patch` files for validated test cases.

**Input:** `cases/{case-id}.yaml`

**Output:** `patches/{case-id}.patch`

**Logic:**
- `git format-patch` between base and head commits
- Stored verbatim — never regenerated mid-run

---

### Script: `validate_cases`

**Purpose:** Pydantic schema validation + git integrity checks.

**Input:** `cases/*.yaml`

**Output:** Validation report; auto-populated stats written back to YAML

**Checks:**
- All required fields present
- Commits exist in repo
- Patch applies cleanly to base commit
- Auto-populate: lines added/deleted, files changed, languages, hunk count

**Test case YAML schema:**
```yaml
id: "aleo-lang-001"
repo: "provable-org/aleo-lang"
base_commit: "abc123"           # Clean base (bug not yet introduced)
head_commit: "def456"           # Bug-introducing commit
fix_commit:  "ghi789"           # Fix commit (ground truth)
category: "logic"               # logic | memory | concurrency | api | type | perf
difficulty: "medium"            # easy | medium | hard
severity: "high"                # low | medium | high | critical
language: "rust"
pr_size: "medium"               # tiny (<10L) | small | medium | large | xl (>500L)
description: "Off-by-one in loop bounds causes silent data corruption"
expected_findings:
  - file: "src/compiler/pass.rs"
    line: 142
    summary: "Loop upper bound should be `n` not `n-1`"
# Auto-populated by validate_cases:
stats:
  lines_added: 12
  lines_deleted: 3
  files_changed: 2
  hunks: 1
```

---

## Phase 2: Execution — Commercial Tools (PR Mode)

### Script: `manage_forks`

**Purpose:** GitHub org and fork lifecycle management.

**Input:** `config/config.yaml`

**Logic:**
- Create `{eval_org}/{repo}-{tool}` forks via `gh` CLI
- Verify tool GitHub App is installed (apps install manually; script checks only)
- Health check, sync with upstream, cleanup commands
- `--dry-run` flag: prints actions without executing

---

### Script: `run_pr_eval`

**Purpose:** Orchestrate PR-based reviews for all commercial PR-mode tools.

**Input:** `cases/*.yaml`, `config/config.yaml`

**Output:** Raw review artifacts in `results/run-{date}/raw/{case-id}-{tool}/`

**Per test case × tool:**
1. Create branch on fork
2. Apply patch (`git am`)
3. Open PR via `gh pr create`
4. Poll for review (configurable timeout + cooldown)
5. Scrape review comments via `gh api`
6. Close PR, delete branch
7. Cooldown (per `config.yaml`)

**Design decisions:**
- State machine with checkpoint file (`results/run-{date}/checkpoint.yaml`) — resume on failure
- Async across tools (asyncio), sequential within each fork (avoids GitHub rate limits)
- `--dry-run`: creates branch + PR but immediately closes without polling

---

## Phase 3: Execution — Commercial Tools (API/CLI Mode)

### Script: `run_api_eval`

**Purpose:** Direct API/CLI orchestrator for non-PR tools.

**Adapters:**
- `greptile`: POST diff to API, GET review response
- `coderabbit`: Run CLI locally against diff (if available)
- Others: add adapters as APIs become available

**Output:** Same normalized schema as PR mode (`results/run-{date}/raw/`)

---

## Phase 4: Execution — In-House Agent

### Script: `run_agent`

**Purpose:** Docker-based runner for Claude Code CLI and Anthropic API agentic loop.

**Input:** `cases/*.yaml`, context level, mode (`cli` | `api`)

**Output:** `results/run-{date}/raw/{case-id}-{mode}-{context}/`

**Per test case:**
1. Clone repo at base commit (Docker volume)
2. Apply diff
3. Run agent with context-level prompt
4. Capture: stdout, conversation log, token count, cost, wall time
5. Destroy container

**Modes:**
- `cli`: Mount repo, run `claude` with structured prompt, capture stdout + conversation log
- `api`: Custom agentic loop (tool use: file read, grep, etc.), capture full transcript

**Dockerfile:** In repo root.

---

## Phase 5: Results Collection

### Script: `scrape_results`

**Purpose:** Normalize all tool outputs to a common schema.

**Input:** Raw artifacts from Phases 2–4

**Output:** `results/run-{date}/{case-id}-{tool}.yaml`

**Output schema:**
```yaml
test_case_id: "aleo-lang-001"
tool: "coderabbit"
context_level: "diff+repo"
comments:
  - file: "src/compiler/pass.rs"
    line: 142
    body: "Loop bound looks off — should this be `n` instead of `n-1`?"
    type: "inline"   # inline | pr-level | summary
metadata:
  tokens: 1842
  cost_usd: 0.012
  time_seconds: 8.4
```

**Notes:**
- Strip tool identity from output for blinded judging
- PR mode: fetch via `gh api` (PR comments, review comments, inline comments)
- Agent mode: parse conversation log

---

## Phase 6: Judging

### Script: `judge`

**Purpose:** LLM-as-judge, 3× majority vote, produces 0–3 score.

**Input:** `cases/{case-id}.yaml` (ground truth) + `results/run-{date}/{case-id}-{tool}.yaml`

**Output:** `results/run-{date}/scores/{case-id}-{tool}.yaml`

**Prompt template:** `config/judge_prompt.md`

**Output schema:**
```yaml
test_case_id: "aleo-lang-001"
tool: "coderabbit"
score: 2
votes: [2, 2, 3]
reasoning: "Tool correctly identified the file and line but did not suggest a fix."
comments:
  - id: 0
    classification: "TP"    # TP | FP | low-value
    relevance: "direct"
noise:
  total_comments: 4
  true_positives: 1
  snr: 0.25
```

---

### Script: `human_judge`

**Purpose:** Export/import human scoring for 25% blinded sample.

**Export:** Blinded, randomized markdown or CSV (tool name redacted)

**Import:** Merge human scores, compute Cohen's kappa vs. LLM judge

---

## Phase 7: Analysis

### Script: `analyze`

**Purpose:** Aggregate scores into comparison tables and charts.

**Input:** `results/run-{date}/scores/*.yaml`

**Output:** `results/run-{date}/analysis/` — markdown tables, CSV, matplotlib PNGs

**Metrics:**
- Detection: catch rate, score distribution, by-PR-size breakdown
- Noise: total comments, SNR
- Cost: per-review, per-bug-caught
- DX: qualitative summary

**Slice dimensions:** tool × category × difficulty × severity × context × PR size × public/private × language

---

## Phase 8: Bootstrap Validation

**Purpose:** End-to-end pipeline test on 5 public repos before running on Provable repos.

**Repos:** Sentry, Cal.com, Grafana, Keycloak, Discourse

**Goal:**
- Validate pipeline works
- Compare results vs. published Greptile benchmark numbers as sanity check
- Memorization check: public repos should show if tools have "seen" the bugs before

---

## Build Order Rationale

```
Phase 0 (skeleton)
    └── Phase 1 (dataset)
            ├── Phase 2 (PR eval)      ──┐
            ├── Phase 3 (API eval)     ──┤── Phase 5 (scrape)
            └── Phase 4 (agent)        ──┘       │
                                             Phase 6 (judge)
                                                  │
                                             Phase 7 (analyze)
```

Phase 8 (bootstrap) can begin as soon as Phase 5 schema is defined.

---

## Verification Checklist

- [ ] pytest for all non-trivial logic
- [ ] Integration test: 1 case through full pipeline (mine → extract → validate → run → scrape → judge → analyze)
- [ ] `--dry-run` flag on all execution commands
- [ ] All commands have `--help`
