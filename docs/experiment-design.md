# Experiment Design

Reference document for the bug-tools-eval study. See `docs/runbook.md` for the step-by-step execution guide and `.claude/CLAUDE.md` for project conventions.

---

## 1. Objective

Evaluate whether commercial AI code review tools (PR-integrated or API-based) can reliably catch real bugs in Provable's codebases at a quality level that justifies their cost, compared to a purpose-built in-house agent using Claude Code CLI and the Anthropic API. The primary output is a build-vs-buy recommendation supported by empirical detection rates, noise ratios, and cost-per-bug-caught across controlled, reproducible test cases.

---

## 2. Tools Under Evaluation

### Commercial (PR-based)

| Tool | Mode | Notes |
|------|------|-------|
| CodeRabbit | PR | Primary PR mode; CLI if available |
| BugBot (Linear) | PR | Requires Linear GitHub App |
| Augment Code | PR | Requires GitHub App install |
| DeepSource | PR | Static + AI hybrid |
| Graphite Diamond | PR | Requires Graphite GitHub App |

### Commercial (API-based)

| Tool | Mode | Notes |
|------|------|-------|
| Greptile | API | Diff submission via REST API |

### In-house agents

| Tool | Mode | Model |
|------|------|-------|
| `claude-cli-haiku` | CLI (Docker) | claude-haiku-4-5 |
| `claude-cli-sonnet` | CLI (Docker) | claude-sonnet-4-6 |
| `claude-cli-opus` | CLI (Docker) | claude-opus-4-6 |
| `anthropic-api-sonnet` | API | claude-sonnet-4-6 |
| `anthropic-api-opus` | API | claude-opus-4-6 |
| `claude-agent-sdk-sonnet` | Agent SDK | claude-sonnet-4-6 |
| `claude-agent-sdk-opus` | Agent SDK | claude-opus-4-6 |
| `gemini-cli-flash-lite` | CLI | gemini-2.5-flash-lite |
| `gemini-cli-flash` | CLI | gemini-2.5-flash |
| `codex-cli-mini` | CLI | gpt-4.1-mini |
| `codex-cli-o4` | CLI | o4-mini |
| `google-api-flash-lite` | SDK | gemini-2.5-flash-lite |
| `google-api-flash` | SDK | gemini-2.5-flash |
| `openai-api-mini` | SDK | gpt-4.1-mini |
| `openai-api-o4` | SDK | o4-mini |

All tools are evaluated against the same test case dataset. Commercial PR tools are isolated to per-tool fork repos in a dedicated GitHub org. Full tool definitions are in `config/config.yaml`.

---

## 3. Dataset

### Source repos

Four Provable repos (Rust, blockchain/ZK) and five public repos (Python, TypeScript, Ruby, Go, Java) for cross-domain validation.

### Current composition

| Repo | Cases | Language | Domain |
|------|-------|----------|--------|
| leo | 253 | Rust | ZK compiler |
| snarkVM | 186 | Rust | ZK virtual machine |
| snarkOS | 187 | Rust | Blockchain node |
| sdk | 56 | Rust | SDK / tooling |
| sentry | 191 | Python | Error monitoring |
| cal.com | 77 | TypeScript | Calendar scheduling |
| discourse | 119 | Ruby | Forum software |
| grafana | 122 | Go | Observability |
| keycloak | 80 | Java | Identity management |
| **Total** | **1,271** | | |

~54% Provable/Rust, ~46% public/mixed-language.

### Selection criteria

- Bug was introduced in an identifiable commit (not a multi-year accumulation)
- Ground truth is the fix commit, which is reviewable
- Bug is non-trivial (not a typo or obvious syntax error)
- Reproducible: patch applies cleanly to base commit

### Test case schema

```yaml
id: "leo-001"
repo: "ProvableHQ/leo"
base_commit: "abc123"           # Clean state (bug not yet present)
head_commit: "def456"           # Bug-introducing commit
fix_commit:  "ghi789"           # Ground truth fix
category: "logic"               # See Category enum below
difficulty: "medium"            # easy | medium | hard
severity: "high"                # low | medium | high | critical
language: "rust"
pr_size: "medium"               # tiny (<10L) | small | medium | large | xl (>500L)
description: "Off-by-one in loop bounds causes silent data corruption"
expected_findings:
  - file: "src/compiler/pass.rs"
    line: 142
    summary: "Loop upper bound should be `n` not `n-1`"
    line_side: "pre_fix"        # "pre_fix" (- side) or "post_fix" (+ side)
# Auto-populated by validate_cases:
stats:
  lines_added: 12
  lines_deleted: 3
  files_changed: 2
  hunks: 1
# Data quality fields:
visibility: "public"            # public | private
needs_manual_review: false
verified: false
verified_by: null
valid_for_code_review: true
introducing_commit: null        # SHA of bug-introducing commit (analysis-only)
pr_number: null                 # GitHub PR number (for scraped cases)
reviewer_notes: []
reviewer_findings: []           # Additional expected findings
quality_flags: []               # e.g. ["groundedness-failed"]
```

### Category enum

`logic` | `memory` | `concurrency` | `api-misuse` | `type` | `cryptographic` | `constraint` | `code-smell` | `security` | `performance` | `style` | `incomplete`

### Dataset quality verification

The `groundedness-check` command (`bugeval groundedness-check`) verifies that `expected_findings` actually exist in the pre-fix diff. It uses an LLM (Haiku by default) to check whether the described bug is visible at the cited file and line in the patch. Cases that fail are flagged with `quality_flags: ["groundedness-failed"]` and `needs_manual_review: true`. This is a post-curation QA step, not part of the evaluation pipeline.

---

## 4. Context Levels

Each test case is run at three context levels to isolate what information drives detection:

| Level | What the tool receives | What it tests |
|-------|----------------------|---------------|
| `diff-only` | The patch alone | Can the tool catch bugs from the diff with no repo context? |
| `diff+repo` | Patch + full repo at base commit | Does repo context improve detection? |
| `diff+repo+domain` | Patch + repo + domain-specific prompt | Does domain knowledge (e.g., ZK proof semantics) unlock harder bugs? |

Context levels apply to in-house agents. Commercial tools receive context according to their native flow (PR tools see the full PR; API tools receive the diff).

---

## 5. Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  Dataset Layer                                                 │
│  cases/final/*.yaml  →  patches/*.patch                        │
│  (ground truth, immutable during runs)                         │
└───────────────────────────┬───────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
      ┌───────────┐  ┌───────────┐  ┌───────────┐
      │ PR Mode   │  │ API Mode  │  │ Agent Mode│
      │ run-pr-   │  │ run-api-  │  │ run-agent-│
      │ eval      │  │ eval      │  │ eval      │
      └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
            └───────────────┴───────────────┘
                            │ raw tool output
                            ▼
                 ┌─────────────────────┐
                 │  normalize          │
                 │  raw → NormalizedResult YAML │
                 └──────────┬──────────┘
                            ▼
                 ┌─────────────────────┐
                 │  judge              │
                 │  LLM-as-judge (3×)  │
                 │  → JudgeScore YAML  │
                 └──────────┬──────────┘
                            ▼
                 ┌─────────────────────┐
                 │  analyze            │
                 │  → report.md, CSV,  │
                 │    charts           │
                 └─────────────────────┘
```

The `pipeline` command runs normalize → judge → analyze in sequence. Each stage can also be run independently.

**PR mode:** Each commercial PR-based tool gets its own fork of each source repo in a dedicated eval GitHub org. A branch is created, the patch applied, and a PR opened. The tool reviews naturally. Comments are scraped via `gh api`.

**API mode:** Diff sent directly to the tool's API; response captured.

**Agent mode:** Repo cloned at base commit (optionally in Docker). Patch applied. Agent runs with structured prompt. Output captured as structured findings.

---

## 6. Execution Protocol

### Checkpoint and resume

Each eval command writes a `checkpoint.yaml` tracking per-(case, tool) status. Interrupted runs resume automatically from the last completed pair.

### Isolation guarantees

- Each tool's fork is independent — no cross-tool contamination
- Docker containers destroyed after each agent run (when `--use-docker` is set)
- Branches deleted after PR closed
- No run mutates `cases/` or `patches/`

### Parallelism

- Async across tools (`asyncio.gather`): all tools process the same case concurrently
- Sequential within each fork: one PR at a time per fork repo
- Configurable `--max-concurrent` and per-tool `cooldown_seconds` in `config.yaml`

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

### LLM-as-judge

- **Ensemble:** 3 models vote independently (Haiku 4.5, Sonnet 4.6, Opus 4.6) — majority wins
- **Fallback:** single-model mode using Opus (when ensemble is not configured)
- **Prompt:** `config/judge_prompt.md` — includes rubric, ground truth, tool output (blinded)
- **Output per vote:** score (0–3), reasoning, per-comment classification (TP / FP / low-value)
- **Noise metrics:** total comments, true positives, SNR (TP / total)

### Human-as-judge (calibration)

- 25% random sample, stratified by tool and difficulty
- Blinded: tool identity redacted
- Randomized: cases shuffled to prevent order effects
- Agreement metric: Cohen's kappa between LLM judge and human judges
- **Calibration threshold:** kappa ≥ 0.85 required before LLM scores are accepted at scale
- Infrastructure: `bugeval human-judge export/import-scores/kappa`

### Calibration process

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
| Catch rate by slice | Catch rate broken down by category, difficulty, severity, PR size, language, repo, visibility, context level |

### Noise

| Metric | Definition |
|--------|-----------|
| Total comments | All comments produced per review |
| True positives | Comments classified as TP by judge |
| SNR | true_positives / total_comments |

### Cost

| Metric | Definition |
|--------|-----------|
| Cost per review | API/token cost per test case (from `metadata.cost_usd`) |
| Cost per bug caught | Cost per review / catch rate |

### Statistical measures

| Metric | Definition |
|--------|-----------|
| Bootstrap 95% CI | Confidence interval on catch rate (2,000 resamples) |
| Permutation p-value | Two-sided test for pairwise tool differences (5,000 permutations) |

### Developer Experience (DX)

Qualitative assessment (1–5 scale per dimension):
- Comment actionability
- False positive burden
- Integration friction
- Response latency

---

## 10. Analysis Dimensions

All metrics are sliced along these dimensions:

| Dimension | Values |
|-----------|--------|
| Tool | 23 tool definitions (see §2) |
| Category | 12 categories (see §3) |
| Difficulty | easy, medium, hard |
| Severity | low, medium, high, critical |
| Context level | diff-only, diff+repo, diff+repo+domain |
| PR size | tiny, small, medium, large, xl |
| Visibility | public, private |
| Language | rust, python, typescript, ruby, go, java |
| Verified | true, false |

Primary comparison: tool × catch rate. Secondary: tool × cost-per-bug. Tertiary: context level × catch rate for in-house agents.

---

## 11. Pitfalls and Mitigations

| Pitfall | Mitigation |
|---------|-----------|
| **Dataset contamination** | Use private Provable repos alongside public repos; public repos provide cross-domain validation |
| **Survivorship bias** | Include bugs across all severity levels, not just obvious ones |
| **Context prompt quality** | Standardize prompts across context levels; version them in `config/` |
| **Config drift** | Tool configs versioned in `config/config.yaml`; pinned at run start via `run_metadata.json` |
| **Self-eval bias** | LLM judge evaluates all tools including Claude agents — same judge for all, blinded. Plan: add non-Claude judges (Gemini, GPT) for cross-validation |
| **Tool caching** | Run on fresh PRs; delete branches after close; use forks not original repos |
| **Time decay** | Complete all tool runs within a single dataset version; tag `dataset-v1`, `dataset-v2` before runs |
| **Context asymmetry** | Commercial PR tools see PR metadata; agents get configurable context. Report results per-context-level; add a `diff-only` agent baseline for fair comparison |
| **Domain knowledge advantage** | `diff+repo+domain` context gives agents ZK-specific hints. Always compare against `diff-only` as a level playing field |
| **Multiple comparisons** | Apply FDR correction (Benjamini-Hochberg) to pairwise p-values |

---

## 12. External Benchmarking

The `export-predictions` / `import-predictions` commands allow external tools to benchmark against this dataset without running inside the framework. Export produces a JSONL file with a standardized `Prediction` schema (instance_id, tool, context_level, findings); import converts predictions back to `NormalizedResult` YAMLs for scoring through the judge → analyze pipeline.
