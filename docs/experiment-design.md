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
| GitHub Copilot | PR | Requires GitHub Copilot App |
| BugBot (Linear) | PR | Requires Linear GitHub App |
| Augment Code | PR | Requires GitHub App install |
| DeepSource | PR | Static + AI hybrid |
| Graphite Diamond | PR | Requires Graphite GitHub App |

### Commercial (API-based)

| Tool | Mode | Notes |
|------|------|-------|
| Greptile | API | Diff submission via REST API |

### In-house agents (30 tool definitions)

Three runner architectures are used, each answering a different question (see §5a):

| Provider | CLI tools | API tools | Agent SDK tools |
|----------|-----------|-----------|-----------------|
| Anthropic | `claude-cli-{haiku,sonnet,opus}` | `anthropic-api-{haiku,sonnet,opus}` | `claude-agent-sdk-{haiku,sonnet,opus}` |
| Google | `gemini-cli-{flash-lite,flash,pro}` | `google-api-{flash-lite,flash,pro}` | — |
| OpenAI | `codex-cli-{mini,5.4,codex}` | `openai-api-{mini,o4,5.4-mini}` | — |

All tools are evaluated against the same test case dataset. Commercial PR tools are isolated to per-tool fork repos in a dedicated GitHub org. The authoritative list of all tool definitions (names, models, timeouts, pricing) is in `config/config.yaml`.

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
base_commit: "abc123"           # PR merge base — clean state before the fix
head_commit: "def456"           # Tip of the fix PR (same as fix_commit for single-commit PRs)
fix_commit:  "def456"           # Ground truth fix commit
category: "logic"               # See Category enum below
difficulty: "medium"            # easy | medium | hard
severity: "high"                # low | medium | high | critical
language: "rust"
pr_size: "medium"               # tiny (<10L) | small (10-50L) | medium (50-200L) | large (200-500L) | xl (>500L)
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
case_type: "fix"                # "fix" (bug-fix PR) or "introducing" (bug-introducing commit)
pr_number: null                 # GitHub PR number (for scraped cases)
pr_title: ""                    # PR title text (from GitHub)
pr_body: ""                     # PR body/description text (from GitHub)
pr_commit_messages: []          # Commit message headlines (from GitHub)
reviewer_notes: []
reviewer_findings: []           # Additional expected findings
quality_flags: []               # e.g. ["patch-too-large"]
```

### Category enum

`logic` | `memory` | `concurrency` | `api-misuse` | `type` | `cryptographic` | `constraint` | `code-smell` | `security` | `performance` | `style` | `incomplete`

### Current dataset composition

All 1,271 cases are `case_type: fix` — the model reviews the fix PR and should identify the bug being fixed. `case_type: introducing` (where the model reviews the PR that introduced the bug) is defined in the schema but not yet used.

Cases with `valid_for_code_review: false` or empty `expected_findings` are automatically excluded by the evaluation pipeline (`load_cases`). Use `load_all_cases` to access the unfiltered dataset for data quality work.

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

**Agent mode:** Repo cloned at base commit (optionally in Docker). PR context is materialized as workspace files before the agent runs:

```
workspace/
├── .pr/
│   ├── description.md    # PR title + body + stats
│   ├── commits.txt       # Commit message headlines (one per line)
│   └── domain.md         # Domain context hints (diff+repo+domain only)
└── diff.patch            # Sanitized unified diff (SHAs and envelope headers stripped)
```

The agent prompt references these files and they are also inlined in the user message for API agents that cannot read files. PR context comes from `pr_title`, `pr_body`, and `pr_commit_messages` fields in the test case YAML. The patch is sanitized to remove identifying metadata (blob SHAs, git format-patch envelope) that could be used for web lookups. The `pr_number` is never exposed to the agent.

**Domain context** (`diff+repo+domain` only): The `domain.md` file is sourced from `config/agent_prompt_domain.md` and contains domain-specific hints relevant to the repository's technology (e.g., ZK proof semantics for Provable repos). This file is only materialized when context level is `diff+repo+domain`. The prompt templates are in `config/agent_prompt.md` (system prompt), `config/agent_prompt_diff+repo.md` (context-specific), and related files. Prompts are loaded by `agent_prompts.py:load_agent_prompt()` and `build_user_prompt()`.

---

## 5a. Runner Architecture and Experimental Control

In-house agents use three runner architectures. Understanding which one to use is critical for interpreting results.

### API runners (model capability comparison)

Each provider's SDK is wrapped in a manual multi-turn loop. All three runners share:

- **Same tools**: 5 tools (`read_file`, `list_directory`, `search_code`, `read_file_range`, `git_blame`) defined in `AGENT_TOOLS`
- **Same tool execution**: single `execute_tool()` function with path traversal guards
- **Same prompt**: identical `system_prompt` and `user_prompt` from `agent_prompts.py`
- **Same parameters**: `max_turns=20`, `temperature=0`, `max_tokens=16384`
- **Same context gating**: `diff-only` disables all tools

Differences in API runner scores reflect **model capability**, not tooling differences. This is the scientifically rigorous comparison.

### CLI runners (product capability comparison)

Each vendor's CLI tool (`claude`, `gemini`, `codex`) is invoked as a subprocess. The CLI brings its own system prompt, tool set, sandbox policy, and agent loop — none of which we control. This means:

- The model sees a different "wrapper" depending on which CLI runs it
- Each CLI has different built-in tools and different agent loop behavior
- `max_turns` is only controllable for Claude (`--max-turns`), not Gemini or Codex
- System prompts cannot be injected (the CLI uses its own)

CLI runner scores measure **product capability** — how well does this product work out of the box? Valuable for the build-vs-buy question, but not a fair model-to-model comparison.

### Agent SDK runner (Anthropic only)

Uses the Anthropic Agent SDK to spawn a Claude Code session programmatically. Provides `max_budget_usd` for cost control. Uses Claude Code's built-in tools (`Read`, `Glob`, `Grep`), which differ from the 5 API tools.

### Interpretation guide

| Question | Use these runners |
|----------|------------------|
| Which **model** is best at finding bugs? | API runners (controlled experiment) |
| Which **product** catches the most bugs out of the box? | CLI runners |
| Is it worth building in-house vs buying? | Compare CLI runners + API runners against commercial PR tools |
| Does repo context help? | Compare `diff-only` vs `diff+repo` within a single runner type |

---

## 5b. Dataset Alignment (Data Quality)

"Alignment" is a data quality check on the test cases, not a tool evaluation metric. It answers: **are the expected bugs actually in the patches we give to the tools?**

### Alignment statuses

| Status | Meaning |
|--------|---------|
| `aligned` | Finding's file AND line are in a changed diff hunk |
| `file-only` | Finding's file is in the diff but the specific line is not in any hunk |
| `misaligned` | Finding's file is not in the diff at all |

### How to interpret alignment percentages

- **High aligned %** — dataset is sound; tools have a fair shot at detecting bugs
- **file-only cases** — valid for `diff+repo` context (tool can explore beyond the diff), unfair for `diff-only`
- **misaligned cases** — should be excluded. A tool scored 0 on a misaligned case is not a real failure — the bug was not in the input.

Alignment is checked via `validate-cases --check-alignment`. The current dataset is 99% aligned after remediation.

---

## 6. Execution Protocol

### Resume via directory detection

Each eval command uses directory-based resume: a (case, tool, context_level) triple is considered done if its `run_dir/raw/{case_id}-{tool}-{context_level}/metadata.json` (agent/API) or `comments.json` (PR) exists. Failed cases write an `error.json` marker and are retried on next run. No shared checkpoint file is needed, enabling safe concurrent invocations against the same run directory.

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

Evaluation uses a **two-layer** scoring system: bug detection (backward-compatible) and holistic review quality.

### Bug Detection Score (0–3)

| Score | Label | Meaning | Example |
|-------|-------|---------|---------|
| 0 | missed | Known bug not identified at all | Tool reviews the PR, comments on style, misses the logic error |
| 1 | wrong-area | Flagged in the right file but wrong line/issue | Tool flags a nearby variable but not the off-by-one |
| 2 | correct-id | Identifies correct file + approximate line | "This loop bound looks off" on the right line |
| 3 | correct-id-and-fix | Correct ID + actionable fix suggestion | "Change `n-1` to `n` here — off-by-one error" |

### Review Quality Score (0–4)

| Score | Label | Meaning |
|-------|-------|---------|
| 0 | useless | No useful feedback, or all noise |
| 1 | shallow | Some relevant observations but misses the main issue and adds little value |
| 2 | adequate | Identifies the main issue OR finds multiple real secondary issues |
| 3 | strong | Identifies the main issue AND provides additional useful findings |
| 4 | exceptional | Comprehensive: main issue + secondary findings + verifies correctness + actionable suggestions |

### Per-Comment Classification

Each tool comment is classified by the judge:
- **TP-expected**: Matches a known expected finding from ground truth
- **TP-novel**: Genuine issue NOT in ground truth, independently confirmed by the judge using the diff
- **FP**: Incorrect, wrong, or describes a non-issue
- **low-value**: Generic advice, obvious, or not actionable

Scoring is per test case, not per comment. A tool that makes 20 comments but gets the bug right scores a detection 2 or 3. A tool that misses the bug but finds real secondary issues can still score review quality 2.

---

## 8. Judging

### LLM-as-judge

- **Ensemble:** 3 models vote independently — majority wins. Default configuration (from `config/config.yaml`): Haiku 4.5, Sonnet 4.6, and Gemini 2.5 Flash. The third vote is cross-provider (Google, not Anthropic) to mitigate self-evaluation bias when judging Claude agent outputs.
- **Cross-provider:** ensemble dispatches each vote to the right SDK automatically based on model name prefix (`claude-*` → Anthropic, `gemini-*` → Google, `gpt-*`/`o4-*` → OpenAI).
- **Fallback:** single-model mode using Opus 4.6 (when ensemble is not configured in `config.judging.models`)
- **Prompt:** `config/judge_prompt.md` — includes rubric, ground truth, tool output (blinded)
- **Output per vote:** score (0–3), reasoning, per-comment classification (TP-expected / TP-novel / FP / low-value / uncertain) with severity and actionability annotations for TPs
- **Diff context:** the judge receives the actual patch to verify TP-novel claims
- **Noise metrics:** total comments, TP-expected, TP-novel, FP, low-value counts, SNR, precision

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

### Review Quality (per-comment derived)

| Metric | Definition |
|--------|-----------|
| Quality-adjusted precision (QAP) | Σ(severity_weight × actionability_weight) / total_comments |
| Weighted signal | Σ(severity_weight × actionability_weight) for all TPs |
| Actionability rate | count(actionable TPs) / count(all TPs) |
| Novel finding rate | TP-novel count / total cases |

### Noise & Precision

| Metric | Definition |
|--------|-----------|
| Total comments | All comments produced per review |
| TP-expected | Comments matching known ground truth findings |
| TP-novel | Comments identifying genuine issues not in ground truth |
| FP | Incorrect or wrong comments |
| Low-value | Generic or non-actionable comments |
| SNR | (TP-expected + TP-novel) / total_comments |
| Precision | (TP-expected + TP-novel) / total_comments |
| Noise ratio | (FP + low-value) / total_comments |

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
| Tool | 30+ tool definitions (see §2 and `config/config.yaml`) |
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
| **Config drift** | Tool configs versioned in `config/config.yaml`; pinned at run start via `run_metadata.json` (written by all three eval commands with config hash, code commit SHA, tool list, and timestamp) |
| **Self-eval bias** | LLM judge evaluates all tools including Claude agents — same judge for all, blinded. Cross-provider judges (Gemini, GPT) supported: add `gemini-*` or `gpt-*` models to `config.judging.models` for cross-validation |
| **Tool caching** | Run on fresh PRs; delete branches after close; use forks not original repos |
| **Time decay** | Complete all tool runs within a single dataset version; tag `dataset-v1`, `dataset-v2` before runs |
| **Context asymmetry** | Commercial PR tools see PR metadata; agents get configurable context. Report results per-context-level; add a `diff-only` agent baseline for fair comparison |
| **Domain knowledge advantage** | `diff+repo+domain` context gives agents ZK-specific hints. Always compare against `diff-only` as a level playing field |
| **Multiple comparisons** | Apply FDR correction (Benjamini-Hochberg) to pairwise p-values |
| **Misaligned test cases** | Expected findings not in the patch produce unfair 0 scores. Run `validate-cases --check-alignment` before every eval; exclude misaligned cases (see §5b) |
| **CLI vs API conflation** | CLI and API runners measure different things (product vs model). Never average CLI and API scores together; report them in separate tables |
| **Model-generated PR content** | Some PRs contain AI-authored descriptions (e.g., "Generated with Claude Code"). These are flagged with `quality_flags: ["model-generated-pr-body"]` to prevent data leakage. Exclude or control for these in analysis |
| **PR metadata leakage** | Agent workspace includes PR title/body which could theoretically be used to look up the original PR. Agent prompts instruct "Do NOT use web search to look up the specific commit, PR, issue, or repository." The `pr_number` field is never exposed to agents |

---

## 12. Dashboard

The `bugeval dashboard` command launches a local Flask web UI for experiment management, dataset review, and human calibration. Key pages:

| Page | URL | Purpose |
|------|-----|---------|
| Home | `/` | Dataset stats grid (by repo, category), experiment overview |
| Runs | `/runs` | Run list with pipeline progress, experiment grouping, notes |
| Run Detail | `/runs/<id>` | Per-run metadata, tools, notes editor |
| Cases | `/cases` | Filterable/sortable/paginated case browser |
| Case Detail | `/cases/<id>` | Metadata editor, diff viewer, alignment status |
| Dataset | `/dataset` | Distribution charts, alignment stats, findings table |
| Golden Set | `/golden` | Case confirmation workflow with coverage stats by repo |
| Human Scoring | `/score/<run>` | Tool-blinded two-axis scoring (detection 0-3, quality 0-4) |
| Human Judge | `/human-judge` | Legacy calibration interface with Cohen's kappa |
| DX Assessment | `/dx` | Per-tool developer experience sliders (1-5 scale) |
| Metrics | `/metrics/<run>` | Aggregate stats, per-tool tables, cost analysis |
| Compare | `/compare` | Side-by-side run comparison with delta indicators |

State is stored in sidecar JSON/YAML files (no database): experiment groups in `results/experiments.yaml`, run notes in `run_dir/.notes.json`, golden set in `cases/.golden_set.json`, human scores in `run_dir/human_scores/`.

---

## 13. Future Work

- **OpenRouter integration**: Add an OpenRouter API runner to evaluate models not available via direct provider APIs (Llama, Mistral, Command R, etc.). Would reuse the existing API runner pattern with a different base URL and model names.

---

## 14. External Benchmarking

The `export-predictions` / `import-predictions` commands allow external tools to benchmark against this dataset without running inside the framework. Export produces a JSONL file with a standardized `Prediction` schema (instance_id, tool, context_level, findings); import converts predictions back to `NormalizedResult` YAMLs for scoring through the judge → analyze pipeline.
