# Scoring Rubric v2: Code Review Evaluation Specification

## Design Principles

1. **Every judgment is atomic.** The judge classifies individual comments, not entire reviews. Aggregate quality emerges from the data.
2. **No subjective composite scores.** The judge never assigns a holistic "review quality" number. Per-comment dimensions are concrete and reproducible.
3. **Bug detection stays objective.** The 0-3 score has an answer key (expected_findings). Don't change what works.
4. **Impact and actionability are first-class.** A critical finding with a fix suggestion is worth more than three vague style nits. The rubric captures this through severity and actionability weights.
5. **Single summary number.** All per-comment dimensions synthesize into **quality-adjusted precision** for tool comparison.

---

## Layer 1: Bug Detection Score

Objective, ground-truth-based. One score per (case, tool) pair. The judge compares tool output against `expected_findings` from the test case YAML.

| Score | Label | Criteria |
|-------|-------|----------|
| 0 | missed | Known bug not identified |
| 1 | wrong-area | Right file, wrong issue or wrong location |
| 2 | correct-id | Correct file + approximate line (±10) of the known bug |
| 3 | correct-id-and-fix | Correct identification + actionable fix suggestion |

**Catch threshold:** score >= 2 counts as a detection.

**Line number tolerance:** Expected findings use pre-fix line numbers. Tool output may use post-fix line numbers. Accept a match if the file and semantic description align, even if line numbers differ by up to 10.

**Multiple expected findings:** Score based on the BEST match. If the tool identifies ANY expected finding correctly, score 2 or 3. TP-expected count in comment_judgments should reflect ALL matched findings.

---

## Layer 2: Per-Comment Assessment

For each numbered tool comment, the judge assigns up to four fields. This is the core of the rubric — all review quality metrics are derived from these atomic judgments.

### 2a. Classification

| Value | Definition | Judge guidance |
|-------|-----------|----------------|
| `TP-expected` | Matches a known expected finding from ground truth | File + semantic description align. Line ±10 tolerance. A comment that identifies the same bug described in expected_findings, even if worded differently, is TP-expected. |
| `TP-novel` | Genuine issue NOT in ground truth, verified by judge | Judge MUST verify from the diff that this describes a real, concrete problem. Not a hypothetical ("this could be an issue if..."), not a stylistic preference ("I'd prefer X"), not something the fix already addresses. The issue must be independently confirmable from the code shown in the diff. |
| `FP` | Incorrect or describes a non-issue | The claim is factually wrong (e.g., "this variable is unused" when it is used), or the "issue" is not actually a problem (e.g., flagging intentionally correct behavior as a bug). |
| `low-value` | Generic, obvious, or not actionable | Advice any reviewer could give on any PR without reading the code: "add more tests," "consider error handling," "improve naming," "add documentation." Also includes observations that are technically true but provide no insight: "this function is long." |
| `uncertain` | Cannot verify from available context | The comment claims an issue in code not shown in the diff, or the judge cannot determine correctness without running the code or seeing files not in the patch. Prefer `uncertain` over guessing TP-novel or FP. |

### 2b. Severity

**Applies to:** TP-expected and TP-novel only. Omit (null) for FP, low-value, uncertain.

| Value | Weight | Definition | Examples |
|-------|--------|-----------|----------|
| `critical` | 4 | Data loss, security vulnerability, crash in production | Use-after-free, SQL injection, unhandled null deref on all paths, cryptographic key exposure |
| `high` | 3 | Functional bug, incorrect behavior under normal use | Off-by-one, wrong return value, broken invariant, missing authorization check |
| `medium` | 2 | Edge case, incomplete handling, misleading code | Missing error path, race under unusual load, stale comment contradicting code, unused parameter shadowing |
| `low` | 1 | Style, naming, minor code smell | Inconsistent formatting, redundant variable, suboptimal but correct algorithm, misleading variable name |

### 2c. Actionability

**Applies to:** TP-expected and TP-novel only. Omit (null) for FP, low-value, uncertain.

| Value | Weight | Definition | Example |
|-------|--------|-----------|---------|
| `actionable` | 1.0 | Specific fix: what to change, where, and why | "Change `<= len` to `< len` on line 142 — off-by-one causes buffer overread" |
| `directional` | 0.6 | Identifies the problem clearly, no specific fix | "The loop bound looks wrong here — it may iterate one too many times" |
| `vague` | 0.3 | Points at something but unclear what to do | "This area might have issues" |

### 2d. Relevance

**Applies to:** All comments.

| Value | Definition |
|-------|-----------|
| `direct` | Addresses the exact code region of a known finding |
| `adjacent` | Same file or closely related logic |
| `unrelated` | About unrelated code |

---

## Per-Comment JSON Schema

The judge returns one object per tool comment:

```json
{
  "id": 0,
  "classification": "TP-novel",
  "severity": "medium",
  "actionability": "actionable",
  "relevance": "adjacent"
}
```

For FP, low-value, and uncertain comments, `severity` and `actionability` are omitted:

```json
{
  "id": 1,
  "classification": "low-value",
  "severity": null,
  "actionability": null,
  "relevance": "unrelated"
}
```

---

## Full Judge Response Schema

```json
{
  "score": 2,
  "reasoning": "Comment [0] correctly identifies the off-by-one. Comment [1] finds a real log injection issue. Comment [2] is generic advice.",
  "comment_judgments": [
    {"id": 0, "classification": "TP-expected", "severity": "high", "actionability": "actionable", "relevance": "direct"},
    {"id": 1, "classification": "TP-novel", "severity": "medium", "actionability": "directional", "relevance": "adjacent"},
    {"id": 2, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"}
  ]
}
```

Note: `review_quality` is NOT in the judge response. It is replaced by derived metrics computed from per-comment data.

---

## Derived Metrics

All metrics are computed from the per-comment judgments and bug detection score. The judge never assigns these directly.

### The Summary Number: Quality-Adjusted Precision

```
comment_value(c) = severity_weight(c) × actionability_weight(c)
    where severity_weight: critical=4, high=3, medium=2, low=1
    where actionability_weight: actionable=1.0, directional=0.6, vague=0.3

weighted_signal = Σ comment_value(c) for all TP comments in a case

quality_adjusted_precision = weighted_signal / total_comments
    (0.0 if total_comments == 0)
```

**Interpretation:** When this tool speaks, how valuable is each comment on average? Higher is better. A tool with 1 critical actionable finding and 0 noise scores `(4×1.0)/1 = 4.0`. A tool with 1 critical actionable finding buried in 19 low-value comments scores `(4×1.0)/20 = 0.2`.

### Signal Metrics

| Metric | Formula | What it answers |
|--------|---------|-----------------|
| Weighted signal | Σ severity_weight × actionability_weight for all TP | How important and actionable are the findings? |
| Quality-adjusted precision | weighted_signal / total_comments | When the tool speaks, how valuable is it? |
| Precision | (TP-expected + TP-novel) / total_comments | What fraction of comments are real issues? |
| Actionability rate | count(actionable) / count(TP-expected + TP-novel) | Can developers act without follow-up? |
| Novel finding rate | TP-novel count / total_cases | Does the tool find value beyond the known bug? |

### Noise Metrics

| Metric | Formula | What it answers |
|--------|---------|-----------------|
| FP rate | FP / total_comments | How often is the tool wrong? |
| Noise ratio | (FP + low-value) / total_comments | What fraction of output is useless? |
| Comment volume | total_comments per case | Is the tool verbose? |

### Bug Detection Metrics (unchanged)

| Metric | Formula |
|--------|---------|
| Catch rate | % cases with score >= 2 |
| Score distribution | % at each 0-3 level |

---

## Build-vs-Buy Decision Table

These are the numbers that go into the recommendation:

| Question | Primary metric | Supporting metrics |
|----------|---------------|-------------------|
| Which tool catches the most known bugs? | Catch rate | Score distribution, catch rate by slice |
| Which tool provides the most valuable feedback? | Quality-adjusted precision | Weighted signal, novel finding rate |
| Which tool has the least noise? | Noise ratio | FP rate, comment volume |
| Which tool gives the most actionable feedback? | Actionability rate | Precision |
| Which tool is worth the money? | Weighted signal / cost per review | Cost per detection |
| Overall: should we build or buy? | All above, side by side | Sliced by category, difficulty, severity |

---

## Worked Examples

### Example A: High-value review (finds known bug + novel issue)

**Ground truth:** Off-by-one in `src/parser.rs` line 142.

**Tool comments:**
- [0] `src/parser.rs:143` — "Loop bound should be `< len` not `<= len`, off-by-one reads past buffer." Fix: "Change `<=` to `<`."
- [1] `src/parser.rs:155` — "Error branch uses `format!` with unbounded user string — log injection vector." (Verified real from diff.)
- [2] `src/lib.rs:10` — "Consider adding integration tests."

**Judge output:**
```json
{
  "score": 3,
  "reasoning": "Comment [0] identifies the known off-by-one with actionable fix. Comment [1] is a real secondary finding confirmed from the diff. Comment [2] is generic advice.",
  "comment_judgments": [
    {"id": 0, "classification": "TP-expected", "severity": "high", "actionability": "actionable", "relevance": "direct"},
    {"id": 1, "classification": "TP-novel", "severity": "medium", "actionability": "directional", "relevance": "adjacent"},
    {"id": 2, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"}
  ]
}
```

**Derived metrics:**
- Bug detection: 3 (correct-id-and-fix)
- Weighted signal: (3 × 1.0) + (2 × 0.6) = 4.2
- Quality-adjusted precision: 4.2 / 3 = 1.4
- Precision: 2/3 = 0.67
- Actionability rate: 1/2 = 0.5
- Noise ratio: 1/3 = 0.33

### Example B: Misses known bug, finds real secondary issues

**Ground truth:** Race condition in `pkg/sync/lock.go` line 88.

**Tool comments:**
- [0] `pkg/sync/lock.go:45` — "Timeout parameter unused — shadowed by inner variable on line 50."
- [1] `pkg/sync/lock.go:72` — "Error silently swallowed. Propagate or log at warning level."

**Judge output:**
```json
{
  "score": 0,
  "reasoning": "Neither comment addresses the race condition on line 88. Both find genuine secondary issues confirmed from the diff.",
  "comment_judgments": [
    {"id": 0, "classification": "TP-novel", "severity": "medium", "actionability": "actionable", "relevance": "adjacent"},
    {"id": 1, "classification": "TP-novel", "severity": "medium", "actionability": "directional", "relevance": "adjacent"}
  ]
}
```

**Derived metrics:**
- Bug detection: 0 (missed)
- Weighted signal: (2 × 1.0) + (2 × 0.6) = 3.2
- Quality-adjusted precision: 3.2 / 2 = 1.6
- Precision: 2/2 = 1.0
- Actionability rate: 1/2 = 0.5
- Noise ratio: 0/2 = 0.0

Note: This tool has *excellent* review quality (precision 1.0, high QAP) despite missing the known bug. The metrics correctly separate these dimensions.

### Example C: All noise

**Ground truth:** Null pointer deref in `app/handlers/auth.go` line 201.

**Tool comments:**
- [0] `app/handlers/auth.go:1` — "Consider adding more comprehensive error handling."
- [1] `app/handlers/auth.go:1` — "Function names could be more descriptive."
- [2] `app/handlers/auth.go:1` — "Adding unit tests would improve confidence."

**Judge output:**
```json
{
  "score": 0,
  "reasoning": "No comment identifies the null pointer deref. All three are generic, non-actionable advice with no specific code references.",
  "comment_judgments": [
    {"id": 0, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"},
    {"id": 1, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"},
    {"id": 2, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"}
  ]
}
```

**Derived metrics:**
- Bug detection: 0
- Weighted signal: 0
- Quality-adjusted precision: 0.0
- Precision: 0.0
- Noise ratio: 3/3 = 1.0

### Example D: Uncertain — comment references code not in diff

**Ground truth:** Integer overflow in `core/math/bignum.rs` line 310.

**Tool comments:**
- [0] `core/math/bignum.rs:312` — "Multiplication can overflow for large inputs." Fix: "Use checked_mul."
- [1] `core/math/bignum.rs:280` — "from_bytes may panic on invalid UTF-8." (Code not visible in diff.)

**Judge output:**
```json
{
  "score": 3,
  "reasoning": "Comment [0] identifies the overflow bug with an actionable fix. Comment [1] references from_bytes which is not in the diff — cannot verify.",
  "comment_judgments": [
    {"id": 0, "classification": "TP-expected", "severity": "high", "actionability": "actionable", "relevance": "direct"},
    {"id": 1, "classification": "uncertain", "severity": null, "actionability": null, "relevance": "adjacent"}
  ]
}
```

**Derived metrics:**
- Bug detection: 3
- Weighted signal: (3 × 1.0) = 3.0
- Quality-adjusted precision: 3.0 / 2 = 1.5
- Precision: 1/2 = 0.5 (uncertain excluded from TP count, included in total)
- Noise ratio: 0/2 = 0.0 (uncertain is neither signal nor noise)

**Note on `uncertain`:** Excluded from both TP and FP/low-value counts. It contributes to `total_comments` (denominator) but not to any numerator. This is conservative — we don't reward or penalize what we can't verify.

---

## Implementation Changes

### Models (`judge_models.py`)

```
CommentClassification:
  - tp_expected = "TP-expected"
  - tp_novel = "TP-novel"
  - fp = "FP"
  - low_value = "low-value"
  - uncertain = "uncertain"
  - _missing_: "TP" → tp_expected (backward compat)

CommentJudgment:
  - id: int
  - classification: CommentClassification
  - severity: str | None = None        # "critical"|"high"|"medium"|"low"
  - actionability: str | None = None    # "actionable"|"directional"|"vague"
  - relevance: str = ""                 # "direct"|"adjacent"|"unrelated"

NoiseStats:
  - total_comments: int = 0
  - true_positives: int = 0    # TP-expected
  - novel_findings: int = 0    # TP-novel
  - false_positives: int = 0   # FP
  - low_value: int = 0
  - uncertain: int = 0
  - weighted_signal: float = 0.0
  - actionability_rate: float = 0.0
  - @property precision: float
  - @property noise_ratio: float
  - @property quality_adjusted_precision: float

JudgeScore:
  - test_case_id: str
  - tool: str
  - score: int (0-3)
  - votes: list[int]
  - reasoning: str
  - comment_judgments: list[CommentJudgment]
  - noise: NoiseStats
  - vote_agreement: float
  # REMOVED: review_quality, review_quality_votes
```

### Scoring config (`pr_eval_models.py`)

```
ScoringConfig:
  - scale: [0, 1, 2, 3]         # bug detection (unchanged)
  - labels: {0-3 mapping}        # unchanged
  - catch_threshold: 2            # unchanged
  # REMOVED: review_quality_scale, review_quality_labels

  - severity_weights: {"critical": 4, "high": 3, "medium": 2, "low": 1}
  - actionability_weights: {"actionable": 1.0, "directional": 0.6, "vague": 0.3}
```

### Judge prompt (`config/judge_prompt.md`)

- Remove review_quality score section
- Add severity and actionability instructions to per-comment section
- Update output schema (no review_quality, add severity/actionability per comment)
- Update worked examples with severity and actionability fields
- Keep uncertain classification and verification guidance

### Judge parsing (`judge.py`)

- Remove review_quality extraction and majority voting
- Parse severity and actionability from comment_judgments
- Compute weighted_signal and actionability_rate in NoiseStats
- Compute quality_adjusted_precision

### Analysis (`analyze.py`)

- Remove avg_review_quality, review_quality_dist
- Add: avg_weighted_signal, avg_quality_adjusted_precision, avg_actionability_rate, avg_novel_finding_rate
- Update markdown table and CSV columns
- Update charts

### Experiment design doc

- Update §7 with this rubric
- Update §8 judge output description
- Update §9 metrics tables
- Remove review_quality references throughout
