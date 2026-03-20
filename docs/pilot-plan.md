# Pilot Plan

Progressive pilot runs to validate the pipeline and build toward the full evaluation.
Each step answers a specific question, and results inform whether/how to proceed.

---

## Pricing Reference

Per-1M tokens (input / output):

| Model | Input | Output |
|-------|-------|--------|
| claude-haiku-4-5 | $0.80 | $4.00 |
| claude-sonnet-4-6 | $3.00 | $15.00 |
| claude-opus-4-6 | $15.00 | $75.00 |
| gemini-2.5-flash-lite | free | free |
| gemini-2.5-flash | $0.15 | $0.60 |
| gpt-4.1-mini | $0.40 | $1.60 |
| o4-mini | $1.10 | $4.40 |

Judging cost per case: ~$0.15 (Haiku + Sonnet + Opus ensemble, small prompts).

---

## Prior Pilot (2026-03-16)

**Setup:** 20 cal.com cases × 3 Claude CLI tiers, `diff-only` context.

| Tool | Catch Rate | Avg Score | Cost/Review |
|------|-----------|-----------|-------------|
| claude-cli-haiku | 0% | 0.10 | $0.20 |
| claude-cli-sonnet | 5% | 0.30 | $0.34 |
| claude-cli-opus | 15% | 0.45 | $0.50 |

**Takeaways:**
- Pipeline works end-to-end (run → normalize → judge → analyze).
- Catch rates are low on diff-only TypeScript. Need to test whether (a) Provable/Rust cases behave differently, and (b) repo context improves results.
- Clear Haiku < Sonnet < Opus quality ladder, but even Opus is only 15%.
- Judge ensemble agreement was 98-100% — promising for calibration.

---

## Pilot Steps

### Step 1 — Provable baseline (diff-only)

**Question:** Do Provable/Rust cases behave differently from cal.com/TypeScript? Is the quality ladder consistent?

**Design:**
- **Cases:** 30 Provable cases (10 leo, 10 snarkVM, 10 snarkOS)
- **Tools:** claude-cli-haiku, claude-cli-sonnet, claude-cli-opus
- **Context:** diff-only
- **Evaluations:** 90

**Case selection criteria:**
- Stratify by difficulty: ~3 easy, ~4 medium, ~3 hard per repo
- Mix categories: include logic, memory, concurrency, cryptographic, constraint
- Prefer cases with clear single-file expected findings (easier to judge)

**Cost estimate:** ~$30 eval + ~$14 judging = ~$44

**Commands:**
```bash
# All three repos in separate commands (can run concurrently — no shared state).
# Directory-based resume: completed cases (metadata.json in raw/) are skipped.
# Safe to re-run on failure — only incomplete cases are retried.
uv run bugeval run-agent-eval \
  --cases-dir cases/final/leo \
  --tools claude-cli-haiku,claude-cli-sonnet,claude-cli-opus \
  --context-level diff-only \
  --limit 10 \
  --max-concurrent 5 \
  --run-dir results/run-2026-03-18-step1-v2

uv run bugeval run-agent-eval \
  --cases-dir cases/final/snarkVM \
  --tools claude-cli-haiku,claude-cli-sonnet,claude-cli-opus \
  --context-level diff-only \
  --limit 10 \
  --max-concurrent 5 \
  --run-dir results/run-2026-03-18-step1-v2

uv run bugeval run-agent-eval \
  --cases-dir cases/final/snarkOS \
  --tools claude-cli-haiku,claude-cli-sonnet,claude-cli-opus \
  --context-level diff-only \
  --limit 10 \
  --max-concurrent 5 \
  --run-dir results/run-2026-03-18-step1-v2

# Pipeline: normalize → judge → analyze.
# --via-cli uses the claude CLI binary for judging (no ANTHROPIC_API_KEY needed).
# Without --via-cli, judging uses the Anthropic SDK and requires ANTHROPIC_API_KEY.
uv run bugeval pipeline \
  --run-dir results/run-2026-03-18-step1-v2 \
  --cases-dir cases/final \
  --via-cli \
  --max-concurrent 5
```

**Notes:**
- `--cases-dir` accepts a repo subdirectory (e.g. `cases/final/leo`) to filter to one repo.
- `--case-ids` can filter to specific IDs: `--case-ids leo-001,leo-002` or `--case-ids @file.txt`.
- `--max-concurrent 5` parallelizes both eval and judging.
- If a run hits transient errors, re-run the same command — directory-based resume retries only incomplete cases.
- `--via-cli` on the pipeline uses `claude` CLI for LLM judge calls. Without it, `ANTHROPIC_API_KEY` must be set.

**Go/no-go:** If catch rates are similar to cal.com (~0-15%), context levels are critical and we proceed to Step 2. If rates are notably higher or lower, we investigate before continuing.

#### Results (pre-remediation — INVALIDATED)

**Run:** `results/run-2026-03-17-step1` (2026-03-17) — deleted, dataset was misaligned

| Tool | Cases | Catch Rate | Avg Score | Avg SNR | Judge Agreement |
|------|-------|-----------|-----------|---------|----------------|
| claude-cli-haiku | 30 | 0.0% | 0.00 | 0.00 | 100% |
| claude-cli-sonnet | 30 | 0.0% | 0.00 | 0.00 | 100% |
| claude-cli-opus | 30 | 0.0% | 0.00 | 0.00 | 100% |

**Root cause:** Expected findings pointed to files/lines NOT in the patch. Tools found real issues but couldn't match ground truth. Dataset remediation (2026-03-18) fixed alignment to 99%.

#### Results (post-remediation — INVALIDATED)

**Run:** `results/run-2026-03-18-step1` (2026-03-18) — invalidated, prompt reframe + PR context + workspace fixture

| Tool | Cases | Catch Rate | Avg Score | Avg SNR | Judge Agreement |
|------|-------|-----------|-----------|---------|----------------|
| claude-cli-haiku | 30 | 16.7% [3.3–33.3%] | 0.47 | 0.14 | 99% |
| claude-cli-sonnet | 30 | 10.0% [0.0–20.0%] | 0.40 | 0.11 | 99% |
| claude-cli-opus | 29 | 24.1% [10.3–41.4%] | 0.66 | 0.16 | 98% |

**Invalidation reason:** Prompt changed from "find bugs" to "review this PR" (broader scope), PR context fields (title, body, commits) added to dataset and materialized as workspace files, workspace-as-fixture pattern replaces prompt-stuffed context. Results no longer comparable.

#### Results (v2 — PR context + prompt reframe)

**Run:** `results/run-2026-03-18-step1-v2` (2026-03-18)

| Tool | Cases | Catch Rate | Avg Score | Avg SNR | Judge Agreement |
|------|-------|-----------|-----------|---------|----------------|
| claude-cli-haiku | 30 | 3.3% [0.0–10.0%] | 0.13 | 0.03 | 100% |
| claude-cli-sonnet | 30 | 10.0% [0.0–20.0%] | 0.50 | 0.12 | 99% |
| claude-cli-opus | 28 | 25.0% [10.7–42.9%] | 0.75 | 0.19 | 99% |

**Notes:**
- 2/90 normalize errors (Opus returned `confidence: "HIGH"` instead of float). 88/90 judged.
- Clear Haiku < Sonnet < Opus quality ladder, consistent with prior cal.com pilot.
- Opus at 25% catch rate is better than cal.com (15%) — Provable/Rust cases slightly easier to detect.
- Judge ensemble agreement 99-100% — well calibrated.

**Go/no-go:** Catch rates are low on diff-only (3-25%). Proceed to Step 2 to test whether repo context improves results.

---

### Step 2 — Does repo context help?

**Question:** Does giving the tool full repo access improve catch rates over diff-only?

Step 1 v2 showed 3-25% catch rate on diff-only. Repo context should help the tool understand what the code is supposed to do and identify whether the patch introduces a bug.

**Design:**
- **Cases:** Same 30 from Step 1 (10 leo, 10 snarkVM, 10 snarkOS)
- **Tools:** claude-cli-sonnet (mid-tier, cost-effective)
- **Context:** diff+repo
- **Evaluations:** 30

We compare these 30 results against the Sonnet diff-only results from Step 1.

**Cost estimate:** ~$15 eval + ~$5 judging = ~$20

**Commands:**
```bash
# Use --repo-cache-dir to avoid re-cloning large repos for each case.
# Directory-based resume: safe to re-run on failure.
uv run bugeval run-agent-eval \
  --cases-dir cases/final/leo \
  --tools claude-cli-sonnet \
  --context-level diff+repo \
  --limit 10 \
  --max-concurrent 5 \
  --repo-cache-dir results/repo-cache \
  --run-dir results/run-2026-03-18-step2-v2

uv run bugeval run-agent-eval \
  --cases-dir cases/final/snarkVM \
  --tools claude-cli-sonnet \
  --context-level diff+repo \
  --limit 10 \
  --max-concurrent 5 \
  --repo-cache-dir results/repo-cache \
  --run-dir results/run-2026-03-18-step2-v2

uv run bugeval run-agent-eval \
  --cases-dir cases/final/snarkOS \
  --tools claude-cli-sonnet \
  --context-level diff+repo \
  --limit 10 \
  --max-concurrent 5 \
  --repo-cache-dir results/repo-cache \
  --run-dir results/run-2026-03-18-step2-v2

uv run bugeval pipeline \
  --run-dir results/run-2026-03-18-step2-v2 \
  --cases-dir cases/final \
  --via-cli \
  --max-concurrent 5
```

**Go/no-go:** If diff+repo significantly beats diff-only, repo context is essential — full runs should use it. If still 0%, the issue is elsewhere (dataset, prompt, or judging).

#### Results (pre-remediation — INVALIDATED)

**Run:** `results/run-2026-03-17-step2-v2` (2026-03-18) — deleted, dataset was misaligned

| Tool | Cases | Catch Rate | Avg Score | Avg SNR | Judge Agreement |
|------|-------|-----------|-----------|---------|----------------|
| claude-cli-sonnet | 30 | 0.0% | 0.00 | 0.00 | 100% |

**Root cause:** Same as Step 1 — expected findings not in patch. Dataset remediation (2026-03-18) fixed alignment to 99%.

#### Results (post-remediation — INVALIDATED)

> _Not run; invalidated along with Step 1: prompt reframe + PR context + workspace fixture._

#### Results (v2 — PR context + prompt reframe)

**Run:** `results/run-2026-03-18-step2-v2` (2026-03-18)

| Tool | Cases | Catch Rate | Avg Score | Avg SNR | Judge Agreement |
|------|-------|-----------|-----------|---------|----------------|
| claude-cli-sonnet | 30 | 23.3% [10.0–36.7%] | 0.63 | 0.17 | 100% |

**Comparison with Step 1 (same 30 cases, same tool):**

| Context | Catch Rate | Avg Score | Improvement |
|---------|-----------|-----------|-------------|
| diff-only | 10.0% | 0.50 | baseline |
| diff+repo | 23.3% | 0.63 | +133% catch rate |

**Takeaways:**
- Repo context more than doubles Sonnet's catch rate (10% → 23.3%).
- diff+repo Sonnet (23.3%) is comparable to diff-only Opus (25.0%) at ~60% of the cost.
- 30/30 normalized successfully (vs 2 errors in Step 1 Opus). Agent tool use is stable.
- 100% judge agreement.

**Go/no-go:** Repo context is essential. Step 3 (cross-vendor) should use diff+repo as the standard context level.

---

### Step 3 — Cross-vendor comparison

**Question:** How do non-Anthropic models compare on Provable code? Are cheap models viable?

**Design:**
- **Cases:** Same 30 from Step 1
- **Tools:** google-api-flash, openai-api-o4, gemini-cli-flash-lite
- **Context:** diff-only (or best context from Step 2 if it's a clear winner)
- **Evaluations:** 90

**Why these tools:**
- `google-api-flash` — strong mid-tier, very cheap ($0.15/$0.60 per 1M)
- `openai-api-o4` — reasoning model, moderate cost ($1.10/$4.40)
- `gemini-cli-flash-lite` — free, establishes the floor

**Cost estimate:** ~$5 eval + ~$14 judging = ~$19

**Commands:**
```bash
uv run bugeval run-agent-eval \
  --cases-dir cases/final/leo \
  --tools google-api-flash,openai-api-o4,gemini-cli-flash-lite \
  --context-level diff-only \
  --limit 10 \
  --max-concurrent 5 \
  --run-dir results/run-2026-XX-XX-step3

# Repeat for snarkVM and snarkOS

uv run bugeval pipeline \
  --run-dir results/run-2026-XX-XX-step3 \
  --cases-dir cases/final \
  --via-cli \
  --max-concurrent 5
```

**Go/no-go:** If a cheap model matches Sonnet, the cost-quality tradeoff story changes. If Opus dominates everything, that's the build recommendation.

#### Results

> _Not yet run._

---

### Step 4 — Agent architecture comparison

**Question:** Does the agent architecture matter? Three approaches use the same underlying model but differ in how the agent gathers context and reasons:

| Approach | Runner | What it does |
|----------|--------|-------------|
| **CLI single-shot** | `claude-cli-sonnet` | One prompt in, one response out. No tool use. |
| **API multi-turn** | `anthropic-api-sonnet` | Custom agent loop with our tool definitions (read_file, search_code, etc.). We control the loop, context, and tools. |
| **Agent SDK** | `claude-agent-sdk-sonnet` | Claude Code's full runtime — autonomous multi-turn with built-in Read, Grep, Bash, etc. Agent decides what to explore. |

These test fundamentally different architectures, not just wrappers:
- CLI tests raw model quality on the prompt alone
- API tests whether a hand-crafted agent loop with targeted tools improves results
- SDK tests whether Claude Code's built-in agent runtime (with auto context-gathering, compaction, and richer tooling) outperforms both

**Design:**
- **Cases:** Same 30 from Step 1
- **Tools:** claude-cli-sonnet, anthropic-api-sonnet, claude-agent-sdk-sonnet
- **Context:** Best context level from Step 2 (for CLI); diff+repo for API and SDK (they gather their own context via tools)
- **Evaluations:** 90

**Cost estimate:** ~$35 eval + ~$14 judging = ~$49

**Go/no-go:**
- If SDK >> CLI: Claude Code's agent loop adds real value — invest in agent tooling rather than prompt engineering.
- If API >> CLI but API ≈ SDK: A custom agent loop is sufficient — no need for the SDK's complexity.
- If all ≈ equal: Model quality dominates architecture. Focus spend on better models, not agent scaffolding.

**Note:** `anthropic-api-sonnet` and `claude-agent-sdk-sonnet` require `ANTHROPIC_API_KEY` to be set.

#### Results

> _Not yet run._

---

### Step 5 — Scale to full dataset

**Question:** Do pilot findings hold at scale? Statistical power for pairwise comparisons.

**Design:**
- **Cases:** All 1,271 cases
- **Tools:** Top 3-4 tools from Steps 1-4 + best context level
- **Evaluations:** ~4,000-5,000

Run only after Steps 1-4 establish which tools and context levels are worth scaling. Cost depends on which tools make the cut.

**Rough cost estimates (full dataset, single context level):**

| Tool | Est. cost (1,271 cases) |
|------|------------------------|
| claude-cli-haiku | ~$250 |
| claude-cli-sonnet | ~$430 |
| claude-cli-opus | ~$640 |
| google-api-flash | ~$15 |
| openai-api-o4 | ~$70 |
| Judging (per tool) | ~$190 |

#### Results

> _Not yet run._

---

### Step 6 — Commercial PR tools

**Question:** How do commercial tools compare to best in-house agent?

**Design:**
- **Cases:** Subset of public repos only (commercial tools can't access private Provable repos)
- **Tools:** coderabbit, bugbot, augment-code, deepsource, graphite-diamond, greptile
- **Mode:** PR-based (requires GitHub org setup from runbook Phase 0)

Deferred until in-house baseline is established. Commercial tools are slower to iterate (PR webhooks, app installs) and more expensive to retry.

#### Results

> _Not yet run._

---

## Decision Log

Track key decisions and pivots as pilots run.

| Date | Step | Decision | Rationale |
|------|------|----------|-----------|
| 2026-03-17 | Step 1 | Proceed to Step 2 (context levels) | diff-only scores 0% on all Provable repos — tools find issues but not the expected bugs. Context is essential. |
| 2026-03-18 | Step 2 | Investigate dataset alignment | Still 0% with repo context. Root cause: expected findings not in patch hunks. |
| 2026-03-18 | All | Invalidate Steps 1-2, re-run | Dataset remediation fixed alignment to 99%. Old results meaningless. Deleted old run dirs. |
| 2026-03-18 | All | Invalidate post-remediation results, re-run as v2 | Prompt reframed from "find bugs" to "review this PR" (broader scope). PR context (title, body, commits) backfilled and materialized as workspace files. Old results not comparable. |
| 2026-03-18 | Step 1 v2 | Proceed to Step 2 (diff+repo context) | diff-only: Haiku 3%, Sonnet 10%, Opus 25%. Quality ladder confirmed. Rates low enough that repo context is worth testing. |
| 2026-03-18 | Step 2 v2 | Repo context is essential; use diff+repo for all future steps | diff+repo Sonnet (23.3%) ≈ diff-only Opus (25.0%) at lower cost. Context more than doubles catch rate. |
| 2026-03-18 | All | Root cause: rust-specific prompt override silently loaded | `config/agent_prompt_rust.md` had old "find bugs introduced" framing. All Rust runs used wrong prompt. Deleted override. |
| 2026-03-18 | All | Prompt reframe: "review both sides of diff" | New prompt instructs model to analyze removed lines (pre-fix bugs) AND added lines (new issues). Mini-pilot: 0% → 60-80% catch rate on 5 previously-missed cases. |
| 2026-03-18 | All | Scale to 60 high-signal cases with corrected prompt | Selected from 477 filtered Provable cases (excluded style, low code-smell, XL PRs). Prioritized logic/constraint/security, high/critical severity, medium difficulty. Case list: `config/pilot-60.txt`. |

---

## Running Cost Tracker

| Run | Step | Cases × Tools | Eval Cost | Judge Cost | Total |
|-----|------|--------------|-----------|------------|-------|
| 2026-03-16 prior pilot | — | 20 × 3 | $20.78 | ~$3 | ~$24 |
| 2026-03-17 | Step 1 | 30 × 3 | $7.14 | ~$10 | ~$17 |
| 2026-03-18 | Step 1 v2 | 30 × 3 | ~$30 | ~$14 | ~$44 |
| 2026-03-18 | Step 2 v2 | 30 × 1 | ~$15 | ~$5 | ~$20 |
| | Step 3 | 30 × 3 | | | |
| | Step 4 | 30 × 2 | | | |
| | Step 5 | TBD | | | |
| | Step 6 | TBD | | | |
| **Running total** | | | | | ~$55 |
