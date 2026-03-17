# Results Schema

Reference documentation for run output files produced by `run-*-eval`, `normalize`, `judge`, and `analyze`.

---

## Run directory structure

```
results/run-YYYY-MM-DD/
├── run_metadata.json          # Reproducibility metadata (git SHA, tools, dataset commit)
├── checkpoint.yaml            # Resumable progress state (case x tool status)
├── raw/
│   └── <case-id>-<tool>/      # One directory per (case, tool) pair
│       ├── findings.json      # Raw tool output (agent: structured findings list)
│       ├── conversation.json  # Full message history (agent tools only)
│       ├── metadata.json      # Timing, cost, token counts
│       ├── prompt.txt         # Prompt sent to the agent (agent tools only)
│       └── stdout.txt         # CLI stdout (CLI tools only)
├── <case-id>-<tool>.yaml      # Normalized result (NormalizedResult schema)
├── judge/
│   └── <case-id>-<tool>.yaml  # Judge score (JudgeScore schema)
└── analysis/
    ├── report.md              # Human-readable summary tables
    ├── scores.csv             # One row per (case, tool) pair
    ├── catch_rate.png         # Catch rate (score >= 2) per tool, bar chart
    └── score_dist.png         # Score distribution per tool, stacked bar chart
```

---

## `run_metadata.json`

Written at run start by all three `run-*-eval` commands.

| Field | Type | Description |
|-------|------|-------------|
| `created_at` | ISO 8601 string | UTC timestamp when the run started |
| `git_sha` | 40-char hex or `""` | HEAD commit of the eval framework repo |
| `config_hash` | `sha256:<hex>` or `""` | SHA-256 of `config/config.yaml` |
| `context_level` | string | Context given to tools: `diff-only`, `diff+repo`, `diff+repo+domain`, or `pr` |
| `tools` | list of strings | Tool names included in this run |
| `cases_dir` | path string | Absolute path to the cases directory used |
| `limit` | int | Max cases per tool (0 = no limit) |
| `patches_dir` | path string or null | Absolute path to patches directory |
| `dataset_commit` | 40-char hex or `""` | Last git commit that touched `cases_dir` |
| `total_cases` | int | Number of `.yaml` files found in `cases_dir` at run start |
| `agent_prompt_hash` | `sha256:<hex>` or `""` | SHA-256 of `config/agent_prompt.md` (agent runs only) |
| `python_version` | string | Python interpreter version (e.g. `3.13.3`) |

---

## `metadata.json` (per raw result)

Written alongside each raw tool output in `raw/<case-id>-<tool>/`.

| Field | Type | Description |
|-------|------|-------------|
| `time_seconds` | float | Wall-clock time for the tool call |
| `cost_usd` | float | Estimated cost based on token usage and model pricing |
| `tokens` | int | Total tokens consumed (input + output) |

Cost is calculated using per-model pricing rates defined in `config/config.yaml` under the `pricing` section.

---

## Normalized result YAML (`NormalizedResult`)

One file per `(case_id, tool)` pair, named `<case-id>-<tool>.yaml`.

| Field | Type | Description |
|-------|------|-------------|
| `test_case_id` | string | Case identifier (matches filename in `cases/`) |
| `tool` | string | Tool name (matches config) |
| `context_level` | string | Context level used for this run |
| `comments` | list | Extracted findings (see `Comment` below) |
| `metadata.tokens` | int | Total tokens used (0 if unavailable) |
| `metadata.cost_usd` | float | Estimated cost in USD (0.0 if unavailable) |
| `metadata.time_seconds` | float | Wall-clock time for the tool call |
| `dx` | object or null | Developer experience assessment (optional, see below) |

### `Comment` fields

| Field | Type | Description |
|-------|------|-------------|
| `file` | string | File path mentioned in the finding (empty if not file-specific) |
| `line` | int | Line number (0 if not applicable) |
| `body` | string | Full text of the comment or finding |
| `type` | string | `inline`, `pr-level`, or `summary` |
| `confidence` | float or null | Tool-reported confidence (0-1) |
| `severity` | string or null | Tool-reported severity label |
| `category` | string or null | Tool-reported category (e.g. `logic`, `security`) |
| `suggested_fix` | string or null | Suggested code fix, if any |
| `reasoning` | string or null | Tool's reasoning for the finding |

### `DxAssessment` fields (optional)

| Field | Type | Description |
|-------|------|-------------|
| `actionability` | int (1-5) | How actionable are the tool's comments? |
| `false_positive_burden` | int (1-5) | How much noise/false positives? (5 = low burden) |
| `integration_friction` | int (1-5) | Ease of integration into workflow (5 = frictionless) |
| `response_latency` | int (1-5) | Speed of response (5 = fast) |
| `notes` | string | Free-text notes |

---

## Judge score YAML (`JudgeScore`)

One file per `(case_id, tool)` pair under `judge/`.

| Field | Type | Description |
|-------|------|-------------|
| `test_case_id` | string | Case identifier |
| `tool` | string | Tool name |
| `score` | int (0-3) | Majority-vote score from LLM judges |
| `votes` | list of int | Individual judge votes (typically 3) |
| `reasoning` | string | Judge's explanation for the final score |
| `comment_judgments` | list | Per-comment TP/FP/low-value classifications |
| `noise.total_comments` | int | Total comments in the normalized result |
| `noise.true_positives` | int | Comments classified as TP |
| `noise.snr` | float | Signal-to-noise ratio (TP / total) |
| `vote_agreement` | float | Agreement ratio among judge votes |

### Scoring scale

| Score | Label | Meaning |
|-------|-------|---------|
| 0 | missed | Bug not identified |
| 1 | wrong-area | Flagged nearby but wrong location |
| 2 | correct-id | Correct file + line, no actionable fix |
| 3 | correct-id-and-fix | Correct identification + actionable fix suggestion |

A score >= 2 counts as a "catch" for catch-rate metrics.

---

## `analysis/report.md`

Human-readable Markdown with sections:

1. **Catch rate by tool** — percentage of cases where score >= 2, sorted descending
2. **Mean score by tool** — average score (0-3), sorted descending
3. **Score distribution** — counts of 0/1/2/3 per tool
4. **Pairwise comparisons** — permutation p-values between tools
5. **Cost analysis** — cost per review, cost per detection (when cost data is present)
6. **DX assessment** — developer experience scores (when DX data is present)

Slices are broken down by: context level, category, difficulty, severity, repo, language, visibility, PR size, and verified status.

---

## `analysis/scores.csv`

One row per `(case_id, tool)` pair.

| Column | Description |
|--------|-------------|
| `case_id` | Test case identifier |
| `tool` | Tool name |
| `score` | Judge score (0-3) |
| `votes` | Pipe-separated individual votes (e.g. `2\|2\|3`) |
| `catch_rate_ci_lo` | Bootstrap 95% CI lower bound |
| `catch_rate_ci_hi` | Bootstrap 95% CI upper bound |
| `category` | Bug category from the test case |
| `difficulty` | Difficulty label |
| `severity` | Severity label |
| `repo` | Repository (e.g. `ProvableHQ/leo`) |
| `language` | Primary language |
| `context_level` | Context level used |
| `cost_usd` | Estimated API cost for this run |
| `time_seconds` | Wall-clock time |

---

## Charts

**`catch_rate.png`** — Bar chart showing the fraction of cases each tool scored >= 2.
Higher is better. The dashed line marks the catch threshold (default: score >= 2).

**`score_dist.png`** — Stacked bar chart showing the count of 0/1/2/3 scores per tool.
Reveals whether a tool misses completely (0s) or gets close but not quite (1s).
