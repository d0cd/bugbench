# Pilot Plan: Incremental Validation

Validate the full pipeline cheaply before scaling up. Each step builds on the
last. Early steps use the Anthropic Agent SDK runner (no API key needed — uses
your Claude Code subscription).

## Prerequisites

```bash
uv sync
export GITHUB_TOKEN=...        # for mining + PR tools (gh CLI)
# ANTHROPIC_API_KEY only needed from Step 4 onward
```

Clone the first target repo:
```bash
mkdir -p repos
git clone https://github.com/ProvableHQ/leo.git repos/leo
```

One-time GitHub org setup (needed for Step 3):
1. Create org `bug-tools-eval` at github.com/organizations/new
2. Install GitHub Copilot, Greptile, and CodeRabbit Apps on the org
3. Activate each `*-greptile` repo on the Greptile dashboard (see `docs/runbook.md`)

---

## Step 0: Mine + Curate Dataset (no cost) --- DONE

Mine fix PRs, build ground truth, auto-curate.

```bash
uv run bugbench mine --repo ProvableHQ/leo --limit 500 --output-dir cases --since 2023-01-01
uv run bugbench blame --cases-dir cases/leo --repo-dir repos/leo
uv run bugbench ground-truth --cases-dir cases/leo --repo-dir repos/leo
uv run bugbench curate --cases-dir cases/leo
```

**Results:** 232 mined -> 58 active cases after curation.
Exclusions: 93 dependency bumps, 25 duplicate introducing PRs, 20 no buggy
lines, 11 all-test-expectation, 9 CI fixes, 7 self-referential, 6 features,
3 doc fixes.

All 58 active cases have: source-code buggy lines, valid introducing commits,
bug descriptions, classification metadata, language=rust.

**Cost: $0. Time: ~10 min.**

---

## Step 1: Agent SDK — diff-only

Validate evaluate -> score -> analyze on the curated dataset.

```bash
uv run bugbench evaluate \
  --tool agent-sdk \
  --cases-dir cases/leo \
  --run-dir results/run-01-sdk-diffonly \
  --repo-dir repos/leo \
  --context diff-only \
  --concurrency 3

# Mechanical scoring only (no LLM, no API key)
uv run bugbench score --run-dir results/run-01-sdk-diffonly --cases-dir cases/leo --dry-run
uv run bugbench analyze --run-dir results/run-01-sdk-diffonly --cases-dir cases/leo
```

**Check:**
- `run_metadata.json` exists with tool, context, model
- `results/` subdir has YAML result files
- `scores/` subdir has YAML score files with `caught` field
- `transcripts/` has SDK transcript JSON files
- `comparison.csv` generated
- Catch rate shown in stdout

**Cost: ~$0.50-1.00. Time: ~10 min.**

---

## Step 2: Agent SDK — diff+repo

Validate workspace-as-fixture pattern and tool use.

```bash
uv run bugbench evaluate \
  --tool agent-sdk \
  --cases-dir cases/leo \
  --run-dir results/run-02-sdk-repo \
  --repo-dir repos/leo \
  --context diff+repo \
  --concurrency 3

uv run bugbench score --run-dir results/run-02-sdk-repo --cases-dir cases/leo --dry-run
uv run bugbench analyze --run-dir results/run-02-sdk-repo --cases-dir cases/leo
```

**Check:**
- Transcripts show the agent using `Read`, `Glob`, `Grep`, `WebSearch` tools
- `.pr/description.md` and `diff.patch` exist in workspace
- Catch rate hopefully higher than diff-only

**Cost: ~$2-5. Time: ~15 min.**

---

## Step 3: PR Tools on 3 Cases

Validate the two-phase PR lifecycle: open PRs, wait for reviews, scrape.
Per-tool repos and local clones are created automatically.

```bash
# Copy 3 cases to a test dir
mkdir -p cases/leo-pilot
cp cases/leo/leo-002.yaml cases/leo/leo-020.yaml cases/leo/leo-022.yaml cases/leo-pilot/

# Phase 1: Open PRs (fast)
for tool in copilot greptile coderabbit; do
  uv run bugbench open-prs \
    --tool $tool \
    --cases-dir cases/leo-pilot \
    --run-dir results/run-03-pr-pilot \
    --repo-dir repos/leo \
    --org bug-tools-eval &
done
wait

# Wait 10-15 min for tools to review, then scrape
uv run bugbench scrape-prs \
  --run-dir results/run-03-pr-pilot \
  --cases-dir cases/leo-pilot \
  --org bug-tools-eval \
  --no-close

# Re-run scrape until all reviewed, then close
uv run bugbench scrape-prs \
  --run-dir results/run-03-pr-pilot \
  --cases-dir cases/leo-pilot \
  --org bug-tools-eval \
  --close
```

**Check for each tool:**
- Per-tool repo created at `bug-tools-eval/leo-{tool}`
- PR opened with scrubbed title (no fix/bug keywords)
- `pr_state=pending-review` in result YAML after open
- `pr_state=reviewed` or `closed` after scrape
- Comments scraped and filtered correctly
- `pr_number` field set on ToolResult

**Cost: $0 (free on public repos). Time: ~15 min.**

---

## Step 4: LLM Judge + Validation (needs ANTHROPIC_API_KEY)

```bash
export ANTHROPIC_API_KEY=...

# Cross-validate ground truth
uv run bugbench validate --cases-dir cases/leo --repo-dir repos/leo

# Score Step 1+2 results WITH LLM judge
uv run bugbench score --run-dir results/run-01-sdk-diffonly --cases-dir cases/leo
uv run bugbench score --run-dir results/run-02-sdk-repo --cases-dir cases/leo
uv run bugbench analyze --run-dir results/run-01-sdk-diffonly --cases-dir cases/leo
uv run bugbench analyze --run-dir results/run-02-sdk-repo --cases-dir cases/leo
```

**Check:**
- Validation verdicts in case YAMLs (claude_verdict = confirmed/disputed)
- Scores now have `detection_score` (0-3), `review_quality` (0-4)
- Comment verdicts: TP, FP, low-value, TP-novel
- `reasoning` field has judge explanation
- `judge_cost_usd` tracked per case

**Cost: ~$1. Time: ~10 min.**

---

## Step 5: Generate Clean Cases + Full Evaluation

Add negative controls for false alarm testing.

```bash
uv run bugbench clean-cases --repo ProvableHQ/leo --count 10 --cases-dir cases --since 2023-01-01

# Run SDK on all cases (bug + clean)
uv run bugbench evaluate \
  --tool agent-sdk \
  --cases-dir cases/leo \
  --run-dir results/run-05-sdk-full \
  --repo-dir repos/leo \
  --context diff+repo \
  --concurrency 3

uv run bugbench score --run-dir results/run-05-sdk-full --cases-dir cases/leo
uv run bugbench analyze --run-dir results/run-05-sdk-full --cases-dir cases/leo
```

**Check via dashboard:**
```bash
uv run bugbench dashboard --cases-dir cases --results-dir results --debug
# Visit http://localhost:5000
# Check: blame confidence distribution, clean cases present, catch rate >10%
```

**Cost: ~$5. Time: ~20 min.**

---

## Step 6: PR Tools at Scale

Run all 3 PR tools on the full leo dataset using two-phase approach.

```bash
# Phase 1: Open all PRs (~2 min)
for tool in copilot greptile coderabbit; do
  uv run bugbench open-prs \
    --tool $tool \
    --cases-dir cases/leo \
    --run-dir results/run-06-pr-tools \
    --repo-dir repos/leo \
    --org bug-tools-eval \
    --concurrency 1 &
done
wait

# Wait for tools to review (Copilot ~10 min, Greptile ~15 min, CodeRabbit ~30 min)
# Scrape periodically until all reviewed
uv run bugbench scrape-prs \
  --run-dir results/run-06-pr-tools \
  --cases-dir cases/leo \
  --org bug-tools-eval \
  --no-close

# When all reviewed, close and score
uv run bugbench scrape-prs \
  --run-dir results/run-06-pr-tools \
  --cases-dir cases/leo \
  --org bug-tools-eval \
  --close

uv run bugbench score --run-dir results/run-06-pr-tools --cases-dir cases/leo
uv run bugbench analyze --run-dir results/run-06-pr-tools --cases-dir cases/leo
```

**Check:** Comparison table shows copilot vs greptile vs coderabbit catch rates.

**Cost: $0 (free) + ~$0.50 judge. Time: ~45 min.**

---

## Step 7: Multi-Repo Dataset

Mine additional repos and curate.

```bash
for repo in ProvableHQ/snarkOS AleoNet/sdk; do
  slug=$(echo $repo | cut -d/ -f2)
  git clone https://github.com/$repo.git repos/$slug
  uv run bugbench mine --repo $repo --limit 500 --output-dir cases --since 2023-01-01
  uv run bugbench blame --cases-dir cases/$slug --repo-dir repos/$slug
  uv run bugbench ground-truth --cases-dir cases/$slug --repo-dir repos/$slug
  uv run bugbench curate --cases-dir cases/$slug
  uv run bugbench clean-cases --repo $repo --count 10 --cases-dir cases --since 2023-01-01
done
```

**Cost: $0 (mining only). Time: ~30 min.**

---

## Step 8: Full Model Comparison (needs all API keys)

Compare Anthropic, Google, and OpenAI API runners across all repos.

```bash
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
export OPENAI_API_KEY=...

for slug in leo snarkOS sdk; do
  for tool_model in "agent claude-sonnet-4-6" "agent-gemini gemini-2.5-flash" "agent-openai o4-mini"; do
    tool=$(echo $tool_model | cut -d' ' -f1)
    model=$(echo $tool_model | cut -d' ' -f2)
    uv run bugbench evaluate \
      --tool $tool --model $model \
      --cases-dir cases/$slug \
      --run-dir results/run-08-models \
      --repo-dir repos/$slug \
      --context diff+repo \
      --concurrency 3
  done
done

uv run bugbench score --run-dir results/run-08-models --cases-dir cases
uv run bugbench analyze --run-dir results/run-08-models --cases-dir cases
```

**Check:** Model quality ladder visible. Compare in dashboard.

**Cost: ~$15-30. Time: ~1-2 hours.**

---

## Decision Points

| Step | Gate | If fails |
|------|------|----------|
| 0 | 50+ curated cases with buggy_lines | Widen date range or add repos |
| 1 | Results + scores generated | Debug evaluate/score pipeline |
| 2 | Agent uses Read/Glob/Grep tools | Fix workspace materialization |
| 3 | PR lifecycle completes for all 3 tools | Fix app install / Greptile dashboard |
| 4 | Judge scores are reasonable | Tune judge prompt |
| 5 | >10% catch rate, clean cases have 0 catches | Tune agent prompt |
| 6 | PR tools produce results at scale | Fix rate limits / timeouts |
| 7 | Multi-repo mining + curation works | Fix repo-specific blame issues |
| 8 | Model quality ladder visible | Experiment design validated |

---

## Cost Summary

| Step | What | API Key? | Cases | Cost |
|------|------|----------|-------|------|
| 0 | Mine + blame + ground truth + curate | No | 58 active | $0 |
| 1 | Agent SDK diff-only | No | 58 | ~$0.75 |
| 2 | Agent SDK diff+repo | No | 58 | ~$3 |
| 3 | PR tools x 3 cases each | No | 9 | $0 |
| 4 | LLM judge + validation | ANTHROPIC | 58 | ~$1 |
| 5 | Clean cases + SDK full dataset | ANTHROPIC | ~68 | ~$5 |
| 6 | PR tools x full dataset + judge | ANTHROPIC | ~200 | ~$0.50 |
| 7 | Mine 2 more repos | No | ~100 | $0 |
| 8 | API runners x all repos | ALL | ~300 | ~$20 |
| **Total pilot** | | | | **~$30** |

---

## After the Pilot

Once Step 8 validates the full experiment design:

1. **Golden set curation** — Use the dashboard to confirm/dispute cases
2. **Full-scale evaluation** — Run all 10 tools x all repos x all context levels
3. **Analysis report** — Comparison tables, charts, statistical tests
4. **Build-vs-buy recommendation** — Catch rate, cost per bug, false alarm rate
