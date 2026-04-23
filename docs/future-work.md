# Future Work

Directions for extending the evaluation framework, ordered by expected impact.

---

## 1. Ensemble Detection (Copilot + Agent)

**Hypothesis:** Copilot and the in-house agent catch mostly *different* bugs. If overlap is low, the union could reach 40-50% detection.

**Experiment:**
1. Run both Copilot and Opus agent on the same 67 cases (already done in pilot)
2. Compute per-case caught/missed for each tool
3. Measure overlap: `|caught_by_both| / |caught_by_either|`
4. If overlap < 50%, the ensemble is worth deploying

**Implementation:** Add `bugbench analyze --ensemble` flag that computes union/intersection metrics across tool pairs. No new evaluation runs needed — reuse existing results.

**Expected outcome:** Based on pilot data, the tools appear complementary (Copilot catches mechanical issues, agent catches reasoning-heavy bugs). Ensemble detection of 40-50% would justify running both in production.

---

## 2. Two-Pass Architecture Tuning

**Background:** The two-pass runner (`agent-sdk-2pass`) was designed to solve the exploration-vs-analysis problem (see [architectural-decisions.md](architectural-decisions.md)). Initial results show promise but the architecture has tunable parameters.

**Experiments:**
- **Turn budget allocation:** Test 20/10, 30/15, 40/20 splits between explorer and reviewer
- **Explorer output format:** Structured JSON context vs free-form notes
- **Reviewer prompt variants:** Strict "only report what you're confident about" vs "flag anything suspicious"
- **Model mixing:** Haiku explorer (cheap context gathering) + Opus reviewer (deep analysis)

**Implementation:** Each variant is a new tool config in `config.yaml`. Run via `bugbench evaluate --tool agent-sdk-2pass --model <variant>`.

---

## 3. Scale to 250 Cases Across Multiple Repos

**Current state:** 67 curated Leo cases. 924 pre-mined cases from snarkOS (434) and snarkVM (482) await processing.

**Steps:**
1. Run `bugbench blame` + `ground-truth` + `curate` on snarkOS and snarkVM cases
2. Generate ~20 clean (non-bug) control cases per repo for false alarm rate
3. Re-evaluate all tools on the expanded dataset
4. Test whether findings generalize across repos (Leo-specific patterns vs universal)

**Cost estimate:** ~$800 for 250 cases x 10 configs with prompt caching (see [presentation cost analysis](presentation.html)).

---

## 4. SWE-bench Style Patch Generation

The current experiment evaluates **bug detection** (did the tool find the bug?). A natural extension is **bug fixing** (can the tool write a correct patch?), following the SWE-bench evaluation paradigm:

| | Current (detection) | Extension (patch generation) |
|---|---|---|
| **Task** | Review introducing PR, find bugs | Given bug report at introducing commit, write a fix |
| **Input** | Introducing PR diff + repo context | Issue body + repo at introducing commit |
| **Output** | Comments (file, line, description) | Patch (code diff) |
| **Metric** | Catch rate (file+line match) | Resolved rate (fix tests pass) |
| **Ground truth** | Buggy lines from diff intersection | Fix commit diff + test suite |

The dataset construction pipeline already provides everything needed:
- `base_commit` (the buggy state) as the starting point
- `fix_commit` as the gold-standard solution
- `bug_description` (from issue body or fix PR) as the task prompt
- Issue bodies, PR discussions, and review comments as optional context

Implementation would add a new evaluation mode (`bugbench evaluate --mode patch`) that:
1. Checks out the repo at `base_commit` (the buggy state)
2. Presents the agent with the bug description and asks it to write a fix
3. Applies the agent's patch and runs the repo's test suite
4. Compares against the fix commit: exact match, semantic equivalence (tests pass), or failure

This reuses the same cases, blame, and ground truth pipeline — only the evaluation runner and scoring change.

---

## 5. Cost Optimization

Three optimizations could reduce per-evaluation cost by ~50%:

**Prompt caching:** Add `cache_control` breakpoints to the system prompt in agent runners. The system prompt (~2K tokens) is identical across cases — caching avoids re-processing. Expected savings: 30% on agent API costs.

**Batch API for judge scoring:** Switch from synchronous `client.messages.create()` to Anthropic's batch API for judge calls. Scores don't need real-time results. Expected savings: 50% on judge costs (8% of total).

**Early termination:** When the agent produces a structured JSON findings block mid-turn, stop the agent loop instead of consuming remaining turns. Many agents produce output by turn 15-20 of a 30-turn budget. Expected savings: 20% on agent costs.

Combined: ~$400 instead of ~$800 for a 250-case evaluation.

---

## 6. Human Judge Calibration Interface

**Problem:** The LLM judge is a black box. We can't verify it scores fairly or detect systematic biases.

**Solution:** A web interface (extending the existing dashboard) where a human reviewer:
1. Scores a sample of 20-30 cases independently
2. Compares their scores against the LLM judge
3. Measures inter-rater agreement (Cohen's kappa)
4. Identifies and overrides systematic biases

See [audit-2026-03-23.md §10](audit-2026-03-23.md) for the detailed design, data model, and UI mockup.

---

## 7. Cross-Model Judge Comparison

**Question:** Does the judge model affect tool rankings?

**Experiment:** Score the same results with 3 judge models (Haiku, Sonnet, Opus) and compare:
- Do tool rankings change?
- Where do judges disagree? (specific case types, bug categories)
- Is a cheaper judge (Haiku) sufficient, or does Opus find nuance Haiku misses?

**Implementation:** `bugbench score --judge-models haiku,sonnet,opus` already supports multi-judge. Run 3x and compare `judge_agreement` fields.

---

## 8. Domain-Specific Prompt Engineering

**Background:** The `diff+repo+domain` context level adds ZK/cryptography-specific context to the agent prompt, but this hasn't been systematically evaluated.

**Experiments:**
- Compare `diff+repo` vs `diff+repo+domain` on cases classified as `codegen`, `type`, or `security`
- Test domain prompts from `config/domain/compiler.md` vs generic prompts
- Measure whether domain context helps on ZK-specific bugs (constraint satisfaction, circuit correctness) without hurting general bug detection

---

## Priority Order

| # | Experiment | Effort | Impact | Ready? | What's Needed |
|---|-----------|--------|--------|--------|---------------|
| 1 | Ensemble detection | Small | High | Needs `--ensemble` flag | Add flag to `analyze` command, compute union/intersection |
| 2 | Two-pass tuning | Medium | High | Base works | Add variant configs to config.yaml, run with `--tool agent-sdk-2pass` |
| 3 | Scale to 250 cases | Medium | High | Ready now | `bugbench blame/ground-truth/curate` on snarkOS + snarkVM cases |
| 4 | SWE-bench patch gen | Large | High | Needs new mode | Add `--mode patch` to evaluate, new scoring logic |
| 5 | Cost optimization | Small | Medium | Code changes | Add cache_control, batch API, early termination |
| 6 | Judge calibration | Medium | Medium | Needs new UI | Dashboard extension (see audit-2026-03-23.md §10) |
| 7 | Cross-model judge | Small | Medium | Ready now | `bugbench score --judge-models claude-haiku-4-5,claude-sonnet-4-6,claude-opus-4-6` |
| 8 | Domain prompts | Small | Low-Medium | Ready now | `bugbench evaluate --tool agent --context diff+repo+domain` |
