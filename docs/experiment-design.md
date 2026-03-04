# Experiment Design

Reference document for the bug-tools-eval study. See `PLAN.md` for the implementation build order and `CLAUDE.md` for project conventions.

---

## 1. Objective

We are evaluating whether commercial AI code review tools (PR-integrated or API-based) can reliably catch real bugs in Provable's codebases at a quality level that justifies their cost, compared to a purpose-built in-house agent using Claude Code CLI and the Anthropic API. The primary output is a build-vs-buy recommendation supported by empirical detection rates, noise ratios, and cost-per-bug-caught across controlled, reproducible test cases.

---

## 2. Tools Under Evaluation

| Tool | Mode | Notes |
|------|------|-------|
| Greptile | API | Diff submission via REST API |
| CodeRabbit | PR + CLI | Primary PR mode; CLI if available |
| BugBot (Linear) | PR | Requires Linear GitHub App |
| Augment Code | PR | Requires GitHub App install |
| DeepSource | PR | Static + AI hybrid |
| Graphite Diamond | PR | Requires Graphite GitHub App |
| Claude Code CLI | CLI (Docker) | In-house; structured prompt, stdout capture |
| Anthropic API (agentic) | API (Docker) | In-house; custom tool-use loop |

All tools are evaluated against the same test case dataset. Commercial tools are isolated to per-tool fork repos in a dedicated GitHub org.

---

## 3. Dataset

### Source Repos

Six Provable repos (mix of public and private, Rust-heavy). Bootstrap validation uses 5 public repos: Sentry, Cal.com, Grafana, Keycloak, Discourse.

### Target Size

90–120 test cases across source repos.

### Selection Criteria

- Bug was introduced in an identifiable commit (not a multi-year accumulation)
- Ground truth is the fix commit, which is reviewable
- Bug is non-trivial (not a typo or obvious syntax error)
- Reproducible: patch applies cleanly to base commit

### Test Case Schema

```yaml
id: "aleo-lang-001"
repo: "provable-org/aleo-lang"
base_commit: "abc123"           # Clean state (bug not yet present)
head_commit: "def456"           # Bug-introducing commit
fix_commit:  "ghi789"           # Ground truth fix
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

## 4. Context Levels

Each test case is run at three context levels to isolate what information drives detection:

| Level | What the tool receives | What it tests |
|-------|----------------------|---------------|
| `diff-only` | The patch alone | Can the tool catch bugs from the diff with no repo context? |
| `diff+repo` | Patch + full repo at base commit | Does repo context improve detection? |
| `diff+repo+domain` | Patch + repo + domain-specific prompt | Does domain knowledge (e.g., ZK proof semantics) unlock harder bugs? |

Context levels apply to in-house agents. Commercial tools receive context according to their native PR review flow.

---

## 5. Architecture

```
┌─────────────────────────────────────────────────────┐
│  Dataset Layer                                       │
│  cases/*.yaml  ─►  patches/*.patch                   │
│  (ground truth, immutable during runs)               │
└───────────────────────┬─────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
  ┌───────────┐  ┌───────────┐  ┌───────────┐
  │ PR Mode   │  │ API Mode  │  │ Agent Mode│
  │ Worker    │  │ Direct    │  │ Docker    │
  │ Fork Repos│  │ API calls │  │ Container │
  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
        └───────────────┴───────────────┘
                        │
                        ▼
            ┌───────────────────┐
            │  Results Layer     │
            │  scrape → judge    │
            │  → analyze         │
            └───────────────────┘
```

**PR mode:** Each commercial PR-based tool gets its own fork of each source repo in a dedicated eval GitHub org. A branch is created, the patch applied, and a PR opened. The tool reviews naturally. Comments are scraped via `gh api`.

**API mode:** Diff sent directly to the tool's API; response captured.

**Agent mode:** Repo cloned at base commit into a Docker container. Patch applied. Agent runs with structured prompt. Container destroyed after capture.

---

## 6. Execution Protocol

### State Machine (per test case × tool)

```
PENDING → BRANCH_CREATED → PATCH_APPLIED → PR_OPENED
       → REVIEW_POLLED → SCRAPED → CLOSED → COOLDOWN → DONE
```

Checkpoint file (`results/run-{date}/checkpoint.yaml`) persists state. Failed runs resume from last completed state.

### Isolation Guarantees

- Each tool's fork is independent — no cross-tool contamination
- Docker containers destroyed after each agent run
- Branches deleted after PR closed
- No run mutates `cases/` or `patches/`

### Cooldowns

Per-tool configurable (`cooldown_seconds` in `config.yaml`). Default 30s between PRs on the same fork to avoid GitHub rate limits.

### Parallel Strategy

- Async across tools (asyncio): all tools process the same test case concurrently
- Sequential within each fork: one PR at a time per fork repo
- Agent runs: one container per case, parallelizable across cases

---

## 7. Scoring Rubric

| Score | Label | Meaning | Example |
|-------|-------|---------|---------|
| 0 | missed | Bug not identified at all | Tool reviews the PR, comments on style, misses the logic error |
| 1 | wrong-area | Flagged in the right file but wrong line/issue | Tool flags a nearby variable but not the off-by-one |
| 2 | correct-id | Identifies correct file + approximate line | "This loop bound looks off" on the right line |
| 3 | correct-id-and-fix | Correct ID + actionable fix suggestion | "Change `n-1` to `n` here — off-by-one error" |

Scoring is per test case, not per comment. A tool that makes 20 comments but gets the bug right scores a 2 or 3.

---

## 8. Judging

### LLM-as-Judge

- **Model:** Claude claude-opus-4-6
- **Calls:** 3 independent, majority vote
- **Prompt:** `config/judge_prompt.md` — includes rubric, ground truth, tool output (blinded)
- **Output per call:** score (0–3), reasoning, per-comment classification (TP / FP / low-value)

### Human-as-Judge

- 25% random sample, stratified by tool and difficulty
- Blinded: tool identity redacted
- Randomized: cases shuffled to prevent order effects
- Agreement metric: Cohen's kappa between LLM judge and human judges
- **Calibration threshold:** 85% agreement required before LLM scores are accepted for the full dataset

### Calibration Process

1. Run human judging on initial 25% sample
2. Compute kappa per score level
3. If kappa < 0.85: adjust judge prompt, re-calibrate
4. Once calibrated: LLM judge runs at scale

---

## 9. Metrics

### Detection

| Metric | Definition |
|--------|-----------|
| Catch rate | % of cases scoring ≥ 2 |
| Score distribution | % at each 0–3 level |
| Catch rate by PR size | Catch rate sliced by tiny/small/medium/large/xl |

### Noise

| Metric | Definition |
|--------|-----------|
| Total comments | All comments produced per review |
| True positives | Comments classified as TP by judge |
| SNR | true_positives / total_comments |

### Cost

| Metric | Definition |
|--------|-----------|
| Cost per review | API/subscription cost per test case |
| Cost per bug caught | Cost per review / catch rate |

### Developer Experience (DX)

Qualitative assessment (1–5 scale):
- Comment actionability
- False positive burden
- Integration friction
- Response latency

---

## 10. Analysis Dimensions

All metrics are sliced along these dimensions:

| Dimension | Values |
|-----------|--------|
| Tool | 8 tools |
| Category | logic, memory, concurrency, api, type, perf |
| Difficulty | easy, medium, hard |
| Severity | low, medium, high, critical |
| Context level | diff-only, diff+repo, diff+repo+domain |
| PR size | tiny, small, medium, large, xl |
| Visibility | public, private |
| Language | rust, python, typescript, … |

Primary comparison: tool × catch rate. Secondary: tool × cost-per-bug. Tertiary: context level × catch rate for in-house agent.

---

## 11. Pitfalls and Mitigations

| Pitfall | Mitigation |
|---------|-----------|
| **Dataset contamination** | Use private Provable repos for primary dataset; public repos only for bootstrap validation |
| **Survivorship bias** | Include bugs across all severity levels, not just obvious ones |
| **Context prompt quality** | Standardize prompts across context levels; version them in `config/` |
| **Config drift** | Tool configs versioned in `config/config.yaml`; pinned at run start |
| **Self-eval bias** | Anthropic API judge evaluates all tools including Claude — use same judge for all, blinded |
| **Tool caching** | Run on fresh PRs; delete branches after close; use forks not original repos |
| **Time decay** | Complete all tool runs within a single dataset version; tag `dataset-v1` before any runs |
| **Scope creep** | Evaluate against PLAN.md phases — each phase has defined inputs/outputs |
