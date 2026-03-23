# Group 0 Analysis: leo-001 through leo-022

14 active cases. Tools: Copilot (PR), Greptile (PR), CodeRabbit (PR), Sonnet diff-only (run-01), Opus diff-only (run-04).

Notation: X = caught (comment near buggy lines), . = missed. Det/Qual = detection_score/review_quality (0-3 / 0-4).

---

### leo-001: [Fix] Harden leo-fmt for 4.0 syntax validation and integrate CI
**Ground truth:** `crates/fmt/tests/harness.rs`:143-176 -- Missing test infrastructure code (AST equivalence validation, type-checking validation, external repo tests, helper functions).
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 0 | 1 | Nitpicked `tested` counter placement (FP); missed actual missing infrastructure |
| Greptile | X | 0 | 1 | Low-value doc-comment path nit; missed missing validation code entirely |
| CodeRabbit | . | 0 | 0 | No comments produced |
| Sonnet diff | . | 0 | 1 | Incorrect claim about `use super::*` not importing private items (FP) |
| Opus diff | . | 0 | 0 | No comments produced |

**Analysis:** No tool detected the actual bug. The ground truth here is unusual -- the "bug" is an absence of substantial test infrastructure that should have been added in the introducing PR. This is essentially missing code rather than incorrect code, making it extremely hard for any tool to flag from a diff review. The buggy lines span 30+ lines of code that simply don't exist yet. This case tests whether tools can identify that a refactoring PR failed to carry over important functionality. **Ground truth validity: questionable for code review detection** -- this is more of a feature gap than a reviewable bug in the introducing diff.

---

### leo-002: Better errors for forbidden items in final blocks
**Ground truth:** `errors/src/errors/type_checker/type_checker_error.rs`:1181-1183 -- The `invalid_operation_inside_final_block` error has an unclear message that should provide guidance about capturing values in local variables.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | . | 0 | 2 | Found 7 real grammar/formatting issues in error messages (all TP-novel) |
| Greptile | . | 0 | 0 | No comments at all |
| CodeRabbit | . | 0 | 2 | 2 novel findings (DCE dropping FinalFn, missing type-checking guards) + 1 FP + 4 low-value |
| Sonnet diff | . | 0 | 3 | 11 novel findings: caught `panic!()` replacing `emit_err()` across refactoring + grammar errors |
| Opus diff | . | 0 | 2 | 10 novel findings: same `panic!` vs `unreachable!()` pattern, grammar issues |

**Analysis:** No tool caught the target bug, but every tool that produced output found genuine novel issues in the diff. Sonnet diff-only stood out with 11 TP-novel findings at quality=3. The bug is about improving an error message's clarity -- a UX concern that's hard to flag mechanically since the existing message isn't technically wrong, just unhelpful. The PR is a large vocabulary refactoring with many secondary issues, which drew all tools' attention away from the specific error message that needed improvement. **Ground truth is valid** but represents a subtle "error message quality" bug that tools reasonably missed.

---

### leo-003: [Fix] `leo devnet` CI on `master`.
**Ground truth:** `crates/leo/src/cli/commands/devnet/mod.rs`:352-639 -- Missing `--storage`/`--path` arguments and per-node storage directories needed for snarkOS compatibility.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 1 | 3 | 5 TP-novel: touched cleanup area (det=1), plus real finds (data corruption risk, u16 overflow, unchecked HTTP) |
| Greptile | X | 0 | 2 | 1 TP-novel (exit code concern), 3 low-value u16 overflow nits |
| CodeRabbit | . | 1 | 2 | 7 TP-novel + 2 FP; many tangential issues (shell escaping, port overflow) but missed core bug |
| Sonnet diff | . | 0 | 2 | 2 TP-novel (Darwin arch, silenced HTTP); mostly speculative edge cases |
| Opus diff | X | 0 | 2 | 3 TP-novel (u16 truncation, Darwin arch, CI config); missed actual --storage/--path issue |

**Analysis:** Copilot came closest (det=1) by touching the cleanup area, but no tool identified the root cause: missing `--storage` and `--path` CLI arguments for snarkOS compatibility. This is an external API compatibility bug -- the introducing PR built against one snarkOS version, and the fix adapts to a newer release. **Tools can't detect this without knowing the snarkOS API changed.** All tools instead found secondary issues (u16 overflow, HTTP error handling, etc.) which are legitimate but tangential. Ground truth is valid but requires external knowledge.

---

### leo-004: [Fix] Devnet tests after snarkOS v4.5.0.
**Ground truth:** `leo/cli/commands/devnode/advance.rs`:40-63 and related -- Blocking reqwest client in async context, silently discarded HTTP responses, missing error propagation.
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 3 | 3 | Direct hit: blocking reqwest in async fn, discarded .send() result. 1 TP + 10 TP-novel |
| Greptile | X | 3 | 3 | Direct hit: blocking client will panic in async runtime. 1 TP + 3 TP-novel |
| CodeRabbit | X | 2 | 3 | Found blocking client + missing timeout. 1 TP + 9 TP-novel + 1 FP |
| Sonnet diff | X | 2 | 4 | 3 TPs on buggy lines (blocking client, ignored response, fragile rate-limit). 8 TP-novel. Exceptional quality |
| Opus diff | X | 3 | 3 | 2 TPs (blocking in async, discarded response). 5 TP-novel. Correct fix suggested |

**Analysis:** **Best case in the group -- all 5 tools caught the bug.** The pattern (blocking HTTP client inside async fn + ignored response) is a well-known Rust anti-pattern that LLMs and static analyzers can easily detect. Sonnet diff-only achieved the highest quality (4) with 3 direct TPs and 8 novel findings. Despite blame confidence C, the ground truth is clearly valid -- every tool independently identified the same core issues. This case demonstrates that **obvious API misuse bugs are reliably caught across all tools**.

---

### leo-005: fix(leo-fmt): wrap long binary expression chains: release-3.5
**Ground truth:** `leo-fmt/src/format.rs`:1458-1476 and many other locations -- Missing line-wrapping support for binary operator chains exceeding 100 characters.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 0 | 2 | 7 TP-novel: found real issues (missing newlines after block comments, dropped COLON_COLON tokens) but missed wrapping |
| Greptile | . | 0 | 1 | 1 TP-novel (removed test_parse_safety), 1 low-value |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | X | 1 | 2 | 4 FP + 1 TP-novel; touched format_binary area but diagnosed wrong issue (DOT_DOT missing) |
| Opus diff | . | 1 | 2 | 2 TP-novel (duplicate trailing comments, dropped operator tokens); flagged right function but wrong issue |

**Analysis:** No tool identified the actual missing feature: binary op chain wrapping at 100 chars. This is a **feature-level bug** -- the formatter produces syntactically valid but poorly formatted output. Tools can't detect this without understanding the formatter's design goals. Both Sonnet and Opus touched the `format_binary` function (det=1) but diagnosed different issues. Copilot found the most novel issues (7) in the same code area. **Ground truth is valid** but represents a design-level omission that's extremely hard to catch from diff review alone.

---

### leo-009: [Fix] Port a few fixes from master
**Ground truth:** Multiple: `tests/expectations/cli/test_add/contents/build/imports/credits.aleo`:53-96 (test expectations), `deploy.rs`/`execute.rs`/`upgrade.rs` (endpoint version-stripping regex).
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 1 | 2 | Flagged regex code duplication (right area, wrong diagnosis). 3 TP-novel |
| Greptile | X | 2 | 2 | Identified problematic regex in deploy.rs (right location). Suggested wrong fix (refine regex vs remove it) |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | X | 2 | 3 | 6 TPs on regex lines + 4 TP-novel. Found right location, suggested improving regex rather than removing |
| Opus diff | X | 1 | 2 | 6 FPs: found exact right location but fundamentally misdiagnosed (suggested refining regex instead of removing) |

**Analysis:** Tools consistently found the version-stripping regex but misdiagnosed the fix direction. The actual fix is to **remove** the stripping entirely (VM no longer expects bare endpoints), but tools suggested **improving** the regex pattern. This is a classic "found the code, missed the intent" situation. Sonnet diff scored highest (det=2, qual=3) with the most TPs. Opus diff is notable as a cautionary tale: it found the exact right lines (6 comments) but every one was scored FP because the suggested fix was backwards. **Scorer may be too strict here** -- locating buggy code and noting it's problematic should count even if the fix direction is wrong.

---

### leo-010: [Fix] Port function inlining fix to release branch
**Ground truth:** `compiler/passes/src/function_inlining/program.rs`:32-36 -- Unfiltered `post_order()` call in function inlining includes external program functions that pollute ordering.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | . | 0 | 1 | 1 FP: meta-concern about PR description vs changes (not a code defect) |
| Greptile | . | 1 | 2 | Summary correctly described the fix but no comment flagged the buggy lines directly |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | . | 0 | 0 | No comments at all |
| Opus diff | . | 0 | 0 | No comments at all |

**Analysis:** **Total miss across all tools.** This is a subtle compiler pass ordering bug where external program functions pollute the call graph traversal. The bug is in 5 lines that call `.post_order()` without filtering -- understanding why this is wrong requires deep knowledge of the compiler's function inlining architecture. Both diff-only agents produced zero comments, suggesting the diff was too small or domain-specific to trigger findings. Greptile's summary showed it understood the change but couldn't surface it as an actionable finding. **Ground truth is valid** -- this is a genuinely hard-to-catch compiler bug.

---

### leo-012: [Fix] SSA, intrinsic type checking, storage variables
**Ground truth:** Multiple files: `compiler/ast/src/passes/visitor.rs`:343-375 (storage variable visitor), `compiler/passes/src/type_checking/visitor.rs`:309-345 (Get/Set type checking for vectors/mappings).
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | . | 0 | 1 | 2 FPs: speculative swap_remove and storage classification concerns |
| Greptile | X | 0 | 2 | 3 TP-novel (missing VectorClear guard, swap_remove analysis, nested vector rejection) |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | . | 0 | 0 | No comments at all |
| Opus diff | . | 0 | 2 | 2 TP-novel (missing VectorClear check, new_mappings scope leak) |

**Analysis:** No tool caught the actual bugs (SSA incorrectly pathing external global variables, type inference for `get_or_use` with unsuffixed numerics). The SSA bug requires understanding how the compiler resolves variable names across program boundaries -- deep domain knowledge. Interestingly, both Greptile and Opus independently found a missing `check_access_allowed` guard for `VectorClear`, suggesting this is a real novel issue. **Ground truth is valid** but the bugs are deep compiler semantics that require understanding Leo's SSA pass and type inference system.

---

### leo-013: [Fix] leo upgrade skip + CLI improvements
**Ground truth:** `leo/cli/commands/build.rs`:108,112 -- Logic for distinguishing network/local .aleo dependencies from local Leo dependencies.
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | . | 0 | 2 | 3 TP-novel: name-consistency check, confusing error path, semver-breaking API removal |
| Greptile | . | 0 | 2 | 1 TP-novel: validation gap in from_aleo_path_impl |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | . | 0 | 2 | 2 TP-novel + 1 FP: found real issues in dependency resolution but missed build.rs |
| Opus diff | . | 0 | 2 | 1 TP-novel: edge case in .aleo path handling |

**Analysis:** **Total miss on the target bug across all tools**, but every tool that produced output achieved quality=2 with novel findings. The buggy lines are two comment lines in build.rs that describe incorrect dependency-type discrimination logic -- the comments are misleading about when bytecode vs Leo compilation should be used. With blame confidence C, this ground truth is the weakest in the group. The bug may be more about missing cases in the match logic than about those specific comment lines. **Ground truth validity: questionable** -- the buggy lines point to comments rather than executable code, and the fix PR description focuses on `leo upgrade` skip behavior, not dependency classification.

---

### leo-016: [Fix] CLI integration test improvements
**Ground truth:** `leo/tests/integration.rs`:18-54 and extensive sections -- Stale test infrastructure that should switch from snarkOS/devnet to devnode, fix ErasedJson::pretty, and refactor to per-test unit tests.
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 0 | 2 | 5 TP-novel: zombie process, stale directory, argument mismatches. Missed core devnode migration |
| Greptile | X | 0 | 2 | 2 TP-novel: timeout on polling loop, argument count mismatch |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | X | 0 | 2 | 6 TP-novel: infinite loop, version-blind check, stale files, thread-safety. Missed devnet-to-devnode |
| Opus diff | X | 0 | 2 | 6 TP-novel: same secondary issues (infinite loop, stale files, CwdRaii thread-safety) |

**Analysis:** Every tool except CodeRabbit produced comments near the buggy area but none identified the core issue: the test infrastructure needs to be rewritten to use devnode instead of devnet. Tools focused on code quality issues within the existing code (infinite loops, thread safety, stale files) rather than recognizing the architectural problem. Both diff-only agents found 6 novel issues each. **Ground truth has blame confidence C** and the "bug" is essentially "this entire test file needs rewriting" -- more of an architectural debt item than a point bug. This is inherently hard for code review tools to flag.

---

### leo-018: [Fix] Remove unnecessary allocations in struct construction
**Ground truth:** `compiler/ast/src/struct/mod.rs`:82-123 -- Inefficient `vec!` + `collect_vec` + `concat` pattern for constructing record members; should use preallocated Vec with push/extend.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 3 | 3 | Direct hit: identified vec+concat allocation inefficiency, suggested preallocated Vec (exact fix). Plus 2 TP-novel, 1 FP |
| Greptile | X | 0 | 3 | 6 TP-novel (inverted owner mode, dropped visibility, Array panic, silently omitted structs). Missed allocation bug |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | X | 0 | 2 | 2 TP-novel (inverted mode, Array panic). Missed allocation bug |
| Opus diff | X | 0 | 2 | 2 TP-novel (dropped visibility, Array panic). 1 FP on owner mode |

**Analysis:** **Only Copilot caught the actual bug** with det=3, suggesting the exact preallocated Vec fix that matches the ground truth. This is a performance bug (unnecessary allocations) rather than a correctness bug, which most tools don't prioritize. Interestingly, multiple tools (Greptile, Sonnet, Opus) all independently found what appears to be a real novel issue: the inverted owner mode mapping (`is_private()` -> `Mode::Public`). Whether this is intentional Aleo semantics or a real bug is unclear. The Array type panic finding also appeared across 3 tools independently. **Ground truth is valid** -- Copilot's detection proves it's findable.

---

### leo-020: [Fix] Lossless parser token definitions
**Ground truth:** `compiler/parser-lossless/src/tokens.rs`:106-117 -- `#[token]` used instead of `#[regex]` for group::*, signature::*, Future::* patterns. Logos `#[token]` matches literal strings, not regex metacharacters.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 3 | 4 | Direct hit: #[token] vs #[regex] with root cause explanation. 1 TP + 7 TP-novel (typos, error spans) |
| Greptile | . | 0 | 2 | 4 TP-novel (duplicate NodeID, BitXorAssign typo, missing quote, unhandled ParseError). Missed #[token] bug |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | . | 0 | 3 | 6 TP-novel (BitXorAssign typo, missing quote, duplicate node ID, etc.). Missed core bug |
| Opus diff | X | 3 | 4 | Direct hit: #[token] vs #[regex] with example (group::GEN). 1 TP + 10 TP-novel. Exceptional quality |

**Analysis:** **Copilot and Opus diff both achieved perfect detection (3) with exceptional quality (4).** Both explained the Logos framework semantics: `#[token]` matches exact literal strings while `#[regex]` interprets regex patterns. This requires knowing the Logos crate's API, which apparently both models have in training data. Greptile and Sonnet diff missed the core bug but both found the same secondary issues (BitXorAssign display string typo, missing closing quote in Address). This case is a **tiny (3 lines changed) high-signal bug** -- the simplest ground truth in the group, and the tools that caught it did so perfectly.

---

### leo-021: [Fix] Edition handling for network dependencies
**Ground truth:** `leo/cli/commands/execute.rs`:314-321 -- Edition defaulting to 1 via `unwrap_or(1)` before proper network fetching; incorrect print logic for credits.aleo.
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 0 | 1 | 1 low-value comment about emoji spacing |
| Greptile | X | 1 | 2 | 3 TP-novel (forbid->deny downgrade, OnceLock safety, CI coverage). Touched line 321 but only flagged style issue |
| CodeRabbit | . | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | X | 2 | 3 | 1 TP on buggy lines (misleading "already included" message). 1 TP-novel (emoji spacing) |
| Opus diff | . | 0 | 0 | No comments at all |

**Analysis:** Sonnet diff-only came closest with det=2, identifying a real logic issue in the flagged buggy region (the "already included" message for credits.aleo is misleading since the program is still passed to `add_programs_with_editions`). However, it didn't identify the broader edition-handling problem. Greptile touched the right area but only flagged a style issue. Opus produced nothing. The bug is about how editions are fetched from the network vs defaulted -- **requires understanding the Leo deployment model** to recognize `unwrap_or(1)` as wrong. With blame confidence C, the ground truth may be imprecise about exactly which lines are buggy.

---

### leo-022: [Fix] leo test exit code + JSON output support
**Ground truth:** `leo/cli/cli.rs`:244-264 -- `leo test` exits with code 0 when tests fail; needs `CliError::tests_failed` variant and exit code propagation.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | X | 1 | 2 | 6 TP-novel: alignment issues in deploy/upgrade, regex patterns, JSON output error. Touched cli.rs but flagged different issues |
| Greptile | X | 0 | 2 | 2 TP-novel: JSON serialization expect, structurally always-true field |
| CodeRabbit | . | 0 | 0 | Skipped review due to branch config |
| Sonnet diff | . | 0 | 3 | 8 TP-novel: enum misuse, silent no-op for --json-output, integer truncation. Strong quality but missed exit code bug |
| Opus diff | . | 0 | 2 | 3 TP-novel: silent failure UX, lost broadcast info, behavioral change on failure |

**Analysis:** No tool caught the exit-code bug despite it being conceptually simple: `leo test` should return non-zero when tests fail. The fix adds a new `CliError::tests_failed` variant. This is a **missing error handling path** -- the code doesn't have the wrong behavior, it's missing behavior entirely. Tools focused on the JSON output feature (the larger portion of the diff) rather than the exit-code issue. Sonnet diff achieved the highest quality (3) with 8 novel findings. The ground truth is valid -- the fix PR explicitly lists "Add CliError::tests_failed" as a to-do item.

---

## Summary Statistics

| Tool | Cases caught (det>0) | Avg detection | Avg quality | Total TPs | Total novel | Total FPs |
|------|---------------------|--------------|------------|----------|------------|----------|
| Copilot | 4 (004,018,020,003) | 1.07 | 2.0 | 4 | 63 | 6 |
| Greptile | 3 (004,009,020-partial) | 0.71 | 1.71 | 2 | 28 | 0 |
| CodeRabbit | 1 (004) | 0.14 | 0.50 | 1 | 11 | 12 |
| Sonnet diff | 3 (004,009,021) | 0.64 | 1.86 | 10 | 44 | 5 |
| Opus diff | 3 (004,020,003-partial) | 0.86 | 1.57 | 3 | 51 | 8 |

Note: CodeRabbit was rate-limited in 10 of 14 cases, severely impacting its results.

## Key Observations

1. **Only one case (leo-004) was caught by all tools.** The blocking-HTTP-in-async pattern is a well-known anti-pattern that all models recognize. This sets a baseline for "easily catchable" bugs.

2. **CodeRabbit was rate-limited in most cases**, producing no actual review in 10/14 cases. Its results are not comparable to the other tools and should be excluded or re-run.

3. **Sonnet diff-only and Opus diff-only diverge significantly on specific cases.** Opus caught leo-020 (token vs regex) perfectly while Sonnet missed it; Sonnet caught leo-021 (edition handling) while Opus produced nothing. This suggests model-level variance matters.

4. **Copilot was the only tool to catch the performance bug (leo-018)** with a perfect det=3. Copilot's strength appears to be pattern-based code quality checks (allocations, API misuse).

5. **Ground truth validity concerns:**
   - **leo-001**: Bug is "missing code" -- inherently undetectable from diff review.
   - **leo-013** (blame C): Buggy lines point to comments, not executable code.
   - **leo-016** (blame C): Bug is "rewrite entire file" -- not a point defect.
   - Cases with blame confidence C should be flagged for ground truth review.

6. **Scorer strictness on leo-009:** Opus found 6 comments on the exact buggy lines but all scored FP because the suggested fix direction was wrong (refine regex vs remove it). Consider whether locating the bug with wrong-direction fix should score det=1 instead of det=0 with 6 FPs.

7. **Novel findings are consistently high quality.** Even when tools miss the target bug, they find 2-10 legitimate secondary issues per case. The "owner mode inversion" finding appeared independently in 4 tools for leo-018, suggesting it may be a real undiscovered bug.

8. **Diff-only context is sufficient for pattern bugs** (leo-004, leo-020) but insufficient for architectural bugs (leo-016) or external-API-change bugs (leo-003, leo-009).
