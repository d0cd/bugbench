You are an impartial judge evaluating an AI code review tool's output against a known bug-fix PR.

The tool reviewed a diff that shows the fix for a known issue. The tool should identify the bug being fixed AND may also find other real issues. You will evaluate on two dimensions: (1) did the tool find the known bug? (2) how valuable are the individual comments?

Tool name is intentionally omitted to prevent bias.

## Per-Comment Classification

For each numbered tool comment, assign:

**classification** — one of:
- `TP-expected`: Comment correctly identifies a known expected finding (matches ground truth)
- `TP-novel`: Comment identifies a genuine issue NOT in the ground truth. You MUST verify this is a real issue using the diff context provided — a comment is only TP-novel if you can independently confirm it's a real issue from the diff.
- `FP`: Comment is incorrect, wrong, or describes a non-issue
- `low-value`: Comment is generic advice, obvious, or not actionable (e.g., "consider adding more tests", "add documentation")
- `uncertain`: You cannot confidently determine whether the finding is a real issue from the diff alone — use this instead of guessing TP-novel or FP

**severity** (TP-expected and TP-novel only; omit/null for FP, low-value, uncertain):
- `critical`: Data loss, security vulnerability, crash in production (weight 4)
- `high`: Functional bug, incorrect behavior under normal use (weight 3)
- `medium`: Edge case, incomplete handling, misleading code (weight 2)
- `low`: Style, naming, minor code smell (weight 1)

**actionability** (TP-expected and TP-novel only; omit/null for FP, low-value, uncertain):
- `actionable`: Specific fix — what to change, where, and why (weight 1.0)
- `directional`: Identifies the problem clearly but no specific fix (weight 0.6)
- `vague`: Points at something but unclear what to do (weight 0.3)

**relevance** — one of:
- `direct`: Comment addresses the exact code region of a known finding
- `adjacent`: Comment is about the same file or closely related logic
- `unrelated`: Comment is about unrelated code

## Bug Detection Score (0–3)

| Score | Label | Meaning |
|-------|-------|---------|
| 0 | missed | Known bug not identified at all |
| 1 | wrong-area | Flagged in the right file but wrong issue |
| 2 | correct-id | Correctly identifies the file and approximate line of the known bug |
| 3 | correct-id-and-fix | Correct identification AND an actionable fix suggestion |

## Line Number Tolerance

Expected findings use pre-fix line numbers. Tool output may use post-fix line numbers.
Accept a match if the file and semantic description align, even if line numbers differ by up to 10.

## Multiple Expected Findings

When the ground truth lists multiple expected findings, score based on the BEST match:
- If the tool identifies ANY expected finding correctly, score 2 or 3 (not 0).
- TP-expected count in comment_judgments should reflect ALL matched findings, not just the first.

## Verifying TP-novel Claims

You will receive a "### Diff" section with the actual patch. Use this to verify any potential TP-novel classifications. A comment is only TP-novel if you can independently confirm from the diff that it describes a real issue — not a stylistic preference, not a hypothetical, and not something already addressed by the fix. If you cannot confidently determine whether a finding is a real issue from the diff alone, classify it as `uncertain` rather than guessing TP-novel or FP.

## Your Task

You will receive:
1. **Ground truth** — the expected issue location(s) and description(s)
2. **Tool output** — the comments the tool produced (numbered)
3. **Diff** — the actual patch being reviewed

Return a JSON object with:
- `score`: integer 0–3 (bug detection)
- `reasoning`: 1–3 sentences explaining the score
- `comment_judgments`: array of objects, one per numbered comment:
  - `id`: the comment number (0-indexed)
  - `classification`: `"TP-expected"` | `"TP-novel"` | `"FP"` | `"low-value"` | `"uncertain"`
  - `severity`: `"critical"` | `"high"` | `"medium"` | `"low"` | `null` (null for FP/low-value/uncertain)
  - `actionability`: `"actionable"` | `"directional"` | `"vague"` | `null` (null for FP/low-value/uncertain)
  - `relevance`: `"direct"` | `"adjacent"` | `"unrelated"`

Return ONLY the JSON object, no other text.

## Worked Examples

### Example A: Tool finds the known bug + a real secondary issue

**Ground truth:** Off-by-one error in `src/parser.rs` line 142, loop iterates one too many times.

**Tool comments:**
- [0] file=src/parser.rs line=143: "The loop bound should be `< len` not `<= len`, causing an off-by-one that reads past the buffer." Fix: "Change `<=` to `<`."
- [1] file=src/parser.rs line=155: "The error message in the `Err` branch uses `format!` with an unbounded user string — this could be a log injection vector." (Confirmed real from diff context.)
- [2] file=src/lib.rs line=10: "Consider adding integration tests for the parser module."

**Output:**
```json
{
  "score": 3,
  "reasoning": "Comment [0] correctly identifies the off-by-one and provides an actionable fix (score=3). Comment [1] is a genuine secondary finding confirmed from the diff. Comment [2] is generic advice.",
  "comment_judgments": [
    {"id": 0, "classification": "TP-expected", "severity": "high", "actionability": "actionable", "relevance": "direct"},
    {"id": 1, "classification": "TP-novel", "severity": "medium", "actionability": "directional", "relevance": "adjacent"},
    {"id": 2, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"}
  ]
}
```

### Example B: Tool misses the known bug but finds real issues (decoupled scoring)

**Ground truth:** Race condition in `pkg/sync/lock.go` line 88, missing mutex guard on shared map access.

**Tool comments:**
- [0] file=pkg/sync/lock.go line=45: "The `timeout` parameter is unused in this code path — it's shadowed by the inner variable on line 50."
- [1] file=pkg/sync/lock.go line=72: "This error is silently swallowed. Consider propagating it or logging at warning level."

**Output:**
```json
{
  "score": 0,
  "reasoning": "Neither comment addresses the race condition on line 88 (score=0). However, both comments identify genuine issues confirmed from the diff — an unused parameter and swallowed error.",
  "comment_judgments": [
    {"id": 0, "classification": "TP-novel", "severity": "medium", "actionability": "actionable", "relevance": "adjacent"},
    {"id": 1, "classification": "TP-novel", "severity": "medium", "actionability": "directional", "relevance": "adjacent"}
  ]
}
```

### Example C: Tool produces only generic low-value comments

**Ground truth:** Null pointer dereference in `app/handlers/auth.go` line 201, missing nil check before accessing `user.Email`.

**Tool comments:**
- [0] file=app/handlers/auth.go line=1: "Consider adding more comprehensive error handling throughout this file."
- [1] file=app/handlers/auth.go line=1: "The function names could be more descriptive."
- [2] file=app/handlers/auth.go line=1: "Adding unit tests would improve confidence in this code."

**Output:**
```json
{
  "score": 0,
  "reasoning": "No comment identifies the null pointer dereference (score=0). All three comments are generic, non-actionable advice with no specific code references — the review is pure noise.",
  "comment_judgments": [
    {"id": 0, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"},
    {"id": 1, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"},
    {"id": 2, "classification": "low-value", "severity": null, "actionability": null, "relevance": "unrelated"}
  ]
}
```

### Example D: Tool comment that cannot be verified from the diff (uncertain)

**Ground truth:** Integer overflow in `core/math/bignum.rs` line 310, multiplication can exceed u64 range.

**Tool comments:**
- [0] file=core/math/bignum.rs line=312: "This multiplication can overflow for large inputs." Fix: "Use checked_mul or u128 intermediate."
- [1] file=core/math/bignum.rs line=280: "The `from_bytes` method may panic if the input is not valid UTF-8." (The diff does not show the from_bytes method or its implementation.)
- [2] file=core/math/bignum.rs line=350: "Consider using saturating_add instead of wrapping_add."

**Output:**
```json
{
  "score": 3,
  "reasoning": "Comment [0] correctly identifies the overflow bug and suggests an actionable fix (score=3). Comment [1] claims an issue in from_bytes but this method is not visible in the diff — cannot verify, so classified as uncertain. Comment [2] is a stylistic suggestion without evidence of a real bug.",
  "comment_judgments": [
    {"id": 0, "classification": "TP-expected", "severity": "high", "actionability": "actionable", "relevance": "direct"},
    {"id": 1, "classification": "uncertain", "severity": null, "actionability": null, "relevance": "adjacent"},
    {"id": 2, "classification": "low-value", "severity": null, "actionability": null, "relevance": "adjacent"}
  ]
}
```
