# Ground Truth Improvements

## Problem Statement

Our ground truth computation uses **line intersection**: lines added by the introducing commit that were deleted by the fix commit. This textual proxy for "what's buggy" fails in predictable ways, losing 215+ cases across three repos and producing imprecise results for many more.

### Current Pipeline

```
Introducing commit → git diff → lines added
Fix commit         → git diff → lines deleted
Intersection (with ±3 line tolerance) → buggy_lines
```

### Impact

| Problem | Cases Lost | % of Total |
|---------|-----------|------------|
| No intersection found (no-buggy-lines) | 199 | 16% |
| Only test expectations changed | 16 | 1% |
| Diffuse (>50 lines, too broad to score) | 89 | 7% |
| Thin (<=2 lines, possibly incomplete) | 49 | 4% |
| **Total affected** | **353** | **29%** |

Only 221/1227 mined cases (18%) have precise, mechanically-scorable ground truth (3-50 non-test buggy lines).

## The Five Failure Modes

### 1. No-buggy-lines (199 cases)

The fix doesn't textually overlap with the introduction. Three sub-patterns:

**(a) Additive fix — bug is an omission.** The introducing PR didn't add wrong code; it failed to add a necessary check. The fix ADDS the check. There are no lines to intersect because the bug is what's NOT there.

Example: A function missing bounds validation. The introducing PR adds the function without the check. The fix adds `if value > MAX { return Err(...) }`. The intersection is empty because the fix only adds lines.

**(b) Fix is elsewhere.** The introducing PR adds function A with a bug. The fix modifies function B (a caller or sibling) to work around or correct the interaction. Different files, no intersection.

Example: PR #3138 adds `unqualify()` using `.name()`. If the fix had been in a caller instead of in `unqualify()` itself, the intersection would miss it.

**(c) Code moved between commits.** The buggy code was refactored, renamed, or moved to a different file between the introducing commit and the fix. Our basename matching helps but misses deep restructuring.

### 2. All-test-expectation (16 cases)

Compiler bugs where the fix only changes golden test output files (`.out`, `.expected`). The actual source-code bug exists but isn't in the test expectation files. The intersection captures test hash changes, not the root cause.

### 3. Diffuse ground truth (89 cases)

Large introducing PRs that changed hundreds of lines. The fix touches a subset. The intersection captures too many lines because the introducing commit modified entire files. With ±3 line tolerance, almost any comment on those files matches mechanically, inflating scores.

Example: A 500-line feature PR where 3 lines have a bug. The intersection returns 200+ lines. Mechanical scoring gives credit for any comment anywhere in those files.

### 4. Thin ground truth (49 cases)

Only 1-2 buggy lines found. Could be genuinely a 1-line bug (like `.name()` → `.resource()`), or the intersection missed most of the relevant context. Without semantic understanding, we can't tell if the ground truth is complete.

### 5. Sibling merge dilution

When one introducing PR has multiple bugs fixed by different fix PRs, we merge all buggy lines into one case. The combined ground truth conflates distinct bugs into one undifferentiated list, making it harder to track which specific bugs a tool found.

## Proposed Solutions

### Priority 1: LLM-Augmented Ground Truth

**The most impactful and practical improvement.** After computing line intersection, ask an LLM to refine, validate, and fill gaps.

**Input to LLM:**
- The introducing diff
- The fix diff
- The fix PR title, body, and linked issue descriptions
- The current buggy_lines (if any)

**Output from LLM:**
```yaml
bugs:
  - file: console/program/src/data_types/plaintext_type/mod.rs
    line_range: [72, 72]
    what: "Uses .name() instead of .resource() for ExternalStruct unqualification"
    why: ".name() returns the program name, not the struct name, causing wrong type resolution for external structs in mappings"
    severity: high
    fix_pr_number: 3144
```

**What it solves:**
- **No-buggy-lines**: LLM reads both diffs and identifies bugs even with zero intersection
- **All-test-expectation**: LLM traces from test output changes back to the source-code root cause
- **Diffuse**: LLM narrows 200 lines to the 3 that actually matter
- **Thin**: LLM confirms completeness or identifies additional buggy lines the intersection missed
- **Sibling dilution**: LLM produces separate bug entries per distinct issue

**Implementation:**
1. New model: `BugDescription(file, line_range, what, why, severity, fix_pr_number)`
2. New field on `GroundTruth`: `llm_bugs: list[BugDescription]`
3. New CLI command: `bugbench enrich-ground-truth --cases-dir ... --repo-dir ... --models claude`
4. Scoring: use `llm_bugs` when available, fall back to `buggy_lines`
5. Cross-validate: compare LLM ground truth with mechanical intersection for consistency

**Estimated cost:** ~$0.10/case with Haiku, ~$0.50/case with Opus. For 353 affected cases: $35-175.

**Estimated recovery:** ~150 of 199 no-buggy-lines cases; refinement of all 89 diffuse cases.

### Priority 2: Test-Based Ground Truth

**The most rigorous approach.** If the fix PR adds a regression test, that test IS the ground truth.

**Method:**
1. Check out repo at the introducing commit
2. Cherry-pick ONLY the test files from the fix commit
3. Build and run the test — it should FAIL (proving the bug exists at the introducing commit)
4. The test code + failure message = precise ground truth

**What it solves:** All five problems. A regression test is the most precise specification of what's wrong. Works regardless of code movement, omissions, or diffuse changes.

**Limitations:**
- Not all fix PRs add regression tests (~40% do for Leo)
- Requires building the project at historical commits (flaky — deps change)
- Expensive in compute and time
- Only works for repos with good test infrastructure

**Implementation:**
1. Detect if fix PR added test files (check fix diff for new files in `tests/`)
2. Docker container: checkout at introducing commit, apply test files, build, run
3. Store test result (pass/fail, output) as ground truth signal
4. Weight test-based ground truth highest in scoring

### Priority 3: Multi-Signal Fusion

**Combine multiple weak signals into strong ground truth.** No single method works for all cases; combining them covers more.

| Signal | Source | Strength | Coverage |
|--------|--------|----------|----------|
| Line intersection | `compute_buggy_lines` | Medium | ~60% of cases |
| LLM analysis | Priority 1 above | Medium | ~90% of cases |
| Regression test | Priority 2 above | Very high | ~40% of cases |
| Fix PR review comments | GitHub API (already fetched) | High | ~30% of cases |
| Issue description | GitHub API (already fetched) | Low | ~50% of cases |
| Fix commit messages | git log (already fetched) | Low | ~80% of cases |

**Implementation:**
1. Each signal produces candidate buggy locations with confidence scores
2. Fusion: locations confirmed by 2+ signals get high confidence
3. Store all signals; scoring weights by confidence
4. Dashboard shows signal provenance so reviewers can verify

### Priority 4: Hierarchical Ground Truth Model

**Model bugs as structured objects, not flat line lists.**

```
Bug
├── Primary location (the wrong code)
│   └── file:line + what's wrong
├── Secondary locations (affected callers/types)
│   └── [file:line + how they're affected]
├── Symptoms (test failures, wrong outputs)
│   └── [test file:line + expected vs actual]
└── Description
    ├── what: one-sentence bug description
    ├── why: invariant violated
    └── category: logic/type/memory/omission/...
```

This separates "the bug is on line 72" from "the symptom appears in test output line 15." A tool that identifies the primary location scores higher than one that only notices symptoms. Both are valid findings at different levels.

## Recommended Execution Order

1. **LLM-Augmented Ground Truth** — highest ROI, recovers ~150 lost cases, refines 89 diffuse cases
2. **Hierarchical model** — small schema change that enables better scoring
3. **Multi-Signal Fusion** — review comments and issue descriptions are already in the dataset; just need to extract location signals from them
4. **Test-Based Ground Truth** — highest quality but most expensive to implement; do for a subset of high-value cases

## Current Workarounds

Until these are implemented:
- **Diffuse cases**: Scored by LLM judge only (mechanical scoring skipped for >50 buggy lines)
- **No-buggy-lines**: Excluded from evaluation (lost)
- **All-test-expectation**: Excluded from evaluation (lost)
- **Thin cases**: Quality-flagged for manual review in dashboard
