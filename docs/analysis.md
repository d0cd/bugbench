# Case-by-Case Analysis: 58 Leo Bug Cases x 7 Tool Configurations

## Summary Findings

### Ground Truth Issues (~15% of cases)
Several cases have questionable ground truth that inflates or deflates scores:
- **leo-001**: "Bug" is missing code (feature gap), not reviewable wrong code
- **leo-013**: Buggy lines point to comments, not executable code
- **leo-029**: Release version bump — not a reviewable bug
- **leo-074**: Blame traced to module re-exports from 3 years ago, not actual logic bug
- **leo-091**: Accumulated technical debt in Display formatting, not a discrete defect
- **leo-136**: Scorer marks "caught" when tools comment on right file but wrong issue

### Tool Strengths
- **Copilot**: Best at line-specific detection; 60% catch rate
- **Greptile**: Strong summaries with file:line references; 45% catch rate
- **Sonnet diff-only**: Highest review quality (2.09); finds most novel issues
- **Opus diff-only**: Highest detection precision on caught cases

### When Tools Succeed
- Classic Rust anti-patterns: blocking in async, wrong variable, type mismatches
- Copy-paste errors: Wrong string literal, wrong enum variant
- Missing error handling: Unwrapped Results, panics replacing errors

### When Tools Fail
- External API changes: Bug requires knowing a dependency's API changed
- Missing code: Bug is absence of functionality, not wrong code
- Domain-specific: Cryptographic nonce security, ZK circuit correctness
- Large refactors: 20+ files changed, tools overwhelmed by volume

### Scorer Issues
- `caught=True` with `det=0` appears in several cases — mechanical scorer says hit
  but judge says wrong issue. Scorer is too permissive on file-level matches.
- CodeRabbit 3.4% catch is accurate even with LLM judge — its summaries genuinely
  dont identify specific bugs.

---

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
# Group 1 Analysis: leo-023 through leo-064

14 active cases. Tools evaluated: Sonnet diff-only, Opus diff-only, Sonnet repo (diff+repo), Opus repo (diff+repo). Copilot, Greptile, and CodeRabbit had no results for any case in this group (only ran on leo-002, leo-020, leo-022).

Scoring key: Det = detection (0-3), Qual = review quality (0-4), X = caught, . = missed.

---

### leo-023: Fix a bug where skipped programs did not get added to the VM
**Ground truth:** `leo/cli/commands/deploy.rs`:386-389 -- When a program is skipped during deployment, it is not added to the VM, causing downstream failures.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 0 | 0 | No comments produced |
| Opus diff | . | 0 | 0 | No comments produced |
| Sonnet repo | . | 0 | 0 | No comments produced |
| Opus repo | . | 0 | 0 | No comments produced |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Total wipeout -- no tool produced any comments. The bug is a missing `vm.process().write().add_program()` call in a `continue` path during deployment iteration. This is a logic omission in control flow that requires understanding the deploy loop's semantics. Diff-only tools see the fix being added but have no context for why the existing code was wrong. Even repo-level tools failed, likely because the bug is domain-specific (understanding that skipped programs still need VM registration). Ground truth appears solid (confidence A, clear fix PR).

---

### leo-026: Add support for empty arrays
**Ground truth:** `compiler/parser/src/parser/expression.rs`:689, `compiler/passes/src/loop_unrolling/statement.rs`:41, plus many test expectation files -- Parser and type-checker reject empty arrays (`[]`, `[u32;0]`) when they should be allowed.
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 0 | 3 | 4 comments on other issues: `to_usize` closure error msg bug, missing integer type assertion for repeat count, `repeat_count_not_evaluated` flag logic, no early return on oversized array |
| Opus diff | . | 0 | 0 | No comments produced |
| Sonnet repo | . | 0 | 0 | 3 comments on unrelated issues (same themes as Sonnet diff) |
| Opus repo | . | 0 | 2 | 4 comments on unrelated issues: `to_usize` closure bug, missing type validation, `Type::Err` not returning early, `repeat_count_not_evaluated` flag |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** This is a feature-addition PR (adding empty array support), not a traditional bug fix. The ground truth (blame confidence C) reflects uncertainty -- the "bug" is really that the language did not support `[u32;0]`. All tools missed the actual ground truth but Sonnet diff and Opus repo found genuine secondary issues in the new code. Sonnet diff scored qual=3 despite det=0, recognizing that its findings were real issues in the fix implementation. The ground truth may be too diffuse for meaningful detection scoring -- it spans parser, loop unrolling, and many test expectation files.

---

### leo-027: Don't delete local constants during const propagation and unroll
**Ground truth:** Spans many files -- `compiler/passes/src/const_prop_unroll_and_morphing.rs`:52-54, `compiler/passes/src/common/symbol_table/mod.rs`:111-117, `compiler/passes/src/const_propagation/ast.rs`:496-500, plus parser, path resolution, type checking, and symbol table creation files. The core bug: local constants were being deleted during `reset_but_consts`, losing them between iteration steps.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 0 | 0 | Timed out (600s), no comments |
| Opus diff | . | 0 | 2 | 10 comments, none on actual bug. Found: `visit_member_access` path bug, `lookup_path` fallback issue, `eq_user` returning false on None path, `fs::read_to_string` unwrap panic, `visit_module` not saving program_name, etc. Scored 6 FP. |
| Sonnet repo | . | 0 | 0 | Timed out (600s), no comments |
| Opus repo | . | 0 | 1 | 5 comments: lookup_path prioritization, regex compilation perf, legalize_path fallthrough, reconstruct_path prefix issue, check_shadow_variable limitation |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** This is a massive diff (the PR touches 20+ files across parser, passes, AST, and symbol table). Both Sonnet runs timed out at 600s. Opus diff produced 10 comments (6 FP) but none identified the actual const propagation retention bug. The ground truth is spread across many files, making it hard for any tool to identify the conceptual thread. The core issue (local constants lost during `reset_but_consts`) requires understanding the multi-pass compilation architecture. Opus diff's comments, while not hitting the target, showed some genuine code quality findings (unwrap panics, regex perf).

---

### leo-029: [Release] Leo v3.3.1
**Ground truth:** Version strings stuck at `3.3.0` across `.resources/release-version`, `Cargo.toml` (many workspace members), and test expectation JSON files.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 0 | 0 | No comments produced |
| Opus diff | . | 0 | 0 | No comments produced |
| Sonnet repo | . | 0 | 0 | No comments produced |
| Opus repo | . | 0 | 0 | No comments produced |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Total wipeout. This is a version bump PR -- the "bug" is that the version was 3.3.0 and needed to be 3.3.1. All tools correctly produced no bug-related comments because the diff is purely mechanical version string updates. This case arguably should not be in the dataset -- a version bump is not a code logic bug. The ground truth is technically correct but tests an inappropriate category for AI review tools.

---

### leo-030: Name Validation pass
**Ground truth:** `compiler/passes/src/type_checking/program.rs`:50-73 and 164-171, 428-432, plus test files -- Missing validation pass for names containing "aleo" keyword and record name prefix conflicts.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | X | 0 | 3 | 4 comments: found real bug in tuple_windows prefix detection (non-adjacent prefixes missed), span discarded for prefix record, perf issue with sym::aleo.to_string() allocation, error definition limitations |
| Opus diff | X | 3 | 4 | 2 comments: correctly identified adjacent-only prefix check bug (same as Sonnet), plus case-sensitive "aleo" check bypass. TP=1 |
| Sonnet repo | . | 0 | 0 | Timed out (600s), no comments |
| Opus repo | . | 0 | 0 | No comments produced |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Interesting divergence: diff-only tools outperformed repo-level tools. Opus diff scored det=3/qual=4, the highest in this group, by precisely identifying the `tuple_windows` bug in record name prefix checking. Sonnet diff found the same bug but was scored caught=True/det=0 -- this seems like scorer inconsistency since both identified the same algorithmic flaw. The scorer may have been stricter with Sonnet because its comment focused on the fix implementation rather than the pre-existing missing validation. Sonnet repo timed out; Opus repo produced nothing despite having full repo access. This suggests that for localized logic bugs in new code, diff-only review is more effective than repo-level review.

---

### leo-033: Fix Clippy Errors
**Ground truth:** `errors/src/common/formatted.rs` -- extensive changes across lines 50-391. Clippy lint failures due to new Rust version: unreachable conditions, unnecessary unwraps, formatting issues in error display code.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | X | 1 | 2 | 5 comments: coordinate system mismatch in multiline detection, double-printing underline, \r\n line ending assumption, env var perf, repeated lines().count() |
| Opus diff | X | 2 | 3 | 5 comments: unreachable condition (line 219), ANSI escape alignment bug, unnecessary unwrap (line 219), NOCOLOR perf, \r\n assumption. TP=2 |
| Sonnet repo | . | 0 | 0 | No comments produced |
| Opus repo | X | 2 | 3 | 4 comments: line_num coordinate mismatch, format string bug with {:start$}, same format bug in print_multiline_underline, \r\n assumption |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Diff-only tools performed well here. Opus diff (det=2, qual=3) found the unreachable condition and unnecessary unwrap on line 219, both exactly the kinds of issues Clippy would flag. Sonnet diff (det=1) found the same coordinate mismatch but was scored lower. Opus repo also scored det=2/qual=3 with 4 focused comments. The Clippy-style nature of this bug (code smell, dead code, unnecessary operations) plays to AI strengths. Sonnet repo again produced nothing. Ground truth is valid but very broad (many lines).

---

### leo-044: Fix incorrect error message variable in Package::initialize test file creation
**Ground truth:** `leo/package/src/package.rs`:152-164 -- `main_path.display()` used instead of `test_file_path.display()` in error message for test file write failure.
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | X | 3 | 4 | 3 comments: C0 precisely identifies the wrong variable in error message (exact bug). C1 found handle_test always returns Ok(). C2 found reconstruct_call panic. TP=1 |
| Opus diff | X | 3 | 3 | 6 comments: C1 identifies the bug (main_path vs test_file_path). Also found handle_test Ok() issue, catch_unwind RNG state, handler emit without advancing ledger. TP=1 |
| Sonnet repo | . | 0 | 0 | Timed out (600s) |
| Opus repo | X | 3 | 4 | 6 comments: C0 precisely identifies copy-paste bug. Also found interpreter state leak, HashMap iteration order, indirect script calling, RNG after panic, @test on async transition |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Strong performance across diff-only and Opus repo. All three producing tools identified the exact copy-paste bug (wrong variable in error message). This is a classic "wrong variable" pattern that AI tools excel at detecting. Both Sonnet diff and Opus repo scored det=3/qual=4 (perfect). Opus diff scored slightly lower on quality (3) despite also finding the bug. The additional findings (handle_test returning Ok, RNG state after panic) are genuine secondary issues. Ground truth is clear and well-defined (confidence B only because it's a minor cosmetic bug).

---

### leo-049: Correctly type check `return` in a constructor
**Ground truth:** `tests/tests/compiler/finalize/unknown_mapping_operation_fail.leo`:11-18 -- Test expectation change from Fail to Pass, reflecting a fix to return-in-constructor type checking.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | X* | 0 | 1 | 3 comments: missing newline (trivial), unused variable (FP), test expectation change without compiler changes. Scored caught=True but det=0 |
| Opus diff | . | 0 | 0 | No comments produced |
| Sonnet repo | . | 0 | 0 | Timed out (600s) |
| Opus repo | X* | 0 | 1 | 2 comments: noted test expectation change implications (finalize blocks with return types), missing newline. Scored caught=True but det=0 |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Problematic ground truth. The "bug" is a test expectation change with no visible compiler source changes in the diff. The ground truth points only to test file lines, making it nearly impossible for tools to identify the underlying compiler bug. Sonnet diff and Opus repo were scored caught=True/det=0, meaning they noticed something was off with the test expectation change but could not identify the actual bug. This case has weak signal -- the fix PR presumably includes compiler changes that are not captured in the buggy_lines. This case should be reviewed for ground truth completeness.

---

### leo-050: `leo clean` fixes
**Ground truth:** `leo/cli/commands/clean.rs`:39-48 -- `remove_dir_all` called without existence checks, fails on fresh/cleaned projects. Also missing manifest check.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | X | 3 | 4 | 10 comments: C0 precisely identifies missing existence check for remove_dir_all. Also found .DS_Store count issue, TestnetV0 hardcoding, env file handling, unwrap on network response. TP=1 |
| Opus diff | X | 3 | 4 | 6 comments: C0 precisely identifies missing existence check. Also found program name format mismatch, missing build dir cleanup, wrong error type in symbol(). TP=1 |
| Sonnet repo | X | 3 | 3 | 7 comments: C0 identifies missing existence check (at different line). Also found SourcePath/build path confusion, TestnetV0 hardcoding, program name mismatch, wrong error type |
| Opus repo | X | 3 | 3 | 7 comments: C1 identifies missing existence check. Also found program name format mismatch, missing validation in from_program, unwrap panics, assert_eq in release, deploy path name mismatch |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Best-performing case in the group -- all four tools caught the bug with det=3. The missing existence check before `remove_dir_all` is a straightforward defensive programming issue that all tools identified as their first or second comment. This is the ideal case for AI code review: a clear, localized error in error handling that is visible in the diff. The diff is large (refactoring of CLI commands) which also surfaced many secondary findings. Ground truth is solid.

---

### leo-052: Hide `--clear` flag for `leo add`
**Ground truth:** `leo/cli/commands/add.rs`:28-34 -- Broken `--clear` flag with `default_value = "false"` on bool, plus broken `--local` and `--network` flag definitions.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 2 | 3 | 4 comments: found is_valid_program_name empty string bug, identified broken clap default_value="false" on bool (line 34 = buggy line). TP=1, but scored caught=False because broader flag deprecation issue was missed |
| Opus diff | . | 2 | 3 | 4 comments: same empty string bug, same clap bool issue (line 124/34), plus unwrap on non-UTF-8 path. TP=1 |
| Sonnet repo | . | 0 | 0 | 4 comments: found removed error variants still referenced (compile error), empty string validation, digit-starting names, unwrap on to_str(). All FP relative to ground truth |
| Opus repo | . | 0 | 2 | 4 comments: same removed error variants compile error, empty string, byte indexing smell, digit-starting names |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Both diff-only tools found the `default_value = "false"` issue on the `clear` bool flag (one of the buggy lines) and scored det=2. However, neither was scored as "caught" because they missed the broader intent of the fix (hiding/deprecating the broken flags). The scorer distinguished between "found a bug in one of the buggy lines" and "understood the full scope of the issue." Sonnet repo and Opus repo found a genuine compile error (removed error variants still referenced in execute.rs) that is arguably a more severe bug than the one being fixed, but it was scored as FP relative to the ground truth.

---

### leo-054: Const-evaluate struct/function const arguments before monomorphizing
**Ground truth:** `errors/src/errors/compiler/compiler_errors.rs`:117-119 (new error definition) plus test expectation files -- The monomorphization logic fails to const-evaluate expressions like `2 * N` before generating monomorphized names, causing `Foo::[2 * 8]` and `Foo::[16]` to be treated as different types.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 0 | 2 | 6 comments: typo "argment", misleading hash comment, regex compilation perf, TODO in type table, missing call graph update, &Vec instead of &[]. All miss core bug |
| Opus diff | . | 0 | 2 | 5 comments: same misleading hash comment, regex perf, same typo, same TODO issue, same &Vec clippy lint. All miss core bug |
| Sonnet repo | . | 0 | 0 | Timed out (600s) |
| Opus repo | . | 0 | 2 | 7 comments: found eq_user ignoring const_arguments (significant!), TODO in type table, misleading hash comment, regex perf, typo, monomorphized_structs never read, &Vec clippy lint |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** All tools missed the core monomorphization bug. The ground truth points to an error definition and test expectation files, but the actual conceptual bug (expressions not const-evaluated before monomorphization) is a design-level issue that requires understanding the compilation pipeline. Opus repo's C0 (eq_user ignoring const_arguments) is the closest any tool came -- it identifies a real type equality problem that is conceptually related to the monomorphization issue. Several tools found the same set of secondary issues (typo, regex perf, TODO). Sonnet repo timed out again. The ground truth may be too narrowly defined -- the error definition lines are more symptom than cause.

---

### leo-060: [Fix] Add `test_network` feature and fix program ID in `leo execute`
**Ground truth:** `leo/cli/commands/execute.rs`:140-162 -- Wrong program ID used in `leo execute` (using `last()` instead of correct program lookup), plus test_network feature addition.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 0 | 3 | 9 comments: found parse_delimited_list empty string bug, uninterpolated format strings (2x), truncate panic, confirm prompt not interpolated, shadowed path variable, missing sleep, dead match arm, consensus version 5 mapped to V4 |
| Opus diff | . | 0 | 2 | 6 comments: confirm prompt not interpolated, missing sleep, uninterpolated error message, same empty string bug, consensus version 5->V4, dead match arm |
| Sonnet repo | . | 0 | 3 | 7 comments: same themes -- remove_dir_all without check, SourcePath bug, TestnetV0 hardcoding, missing sleep, uninterpolated error, confirm prompt, consensus version |
| Opus repo | . | 0 | 3 | 6 comments: parse_delimited_list, uninterpolated error, confirm prompt (2 bugs), consensus version, missing sleep, reversed DFS not valid topo sort |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Total miss on the ground truth bug (wrong program ID from `last()`), but every tool found many genuine secondary bugs in this large refactoring diff. The uninterpolated format strings, missing sleep call, and consensus version mapping error are all real bugs. Opus repo's C5 (reversed pre-order DFS not being a valid topological sort) is a particularly sophisticated finding. The ground truth bug (using `last()` to get program name) is subtle and requires understanding the program dependency ordering. This is a case where tools provided high value despite missing the specific tracked bug.

---

### leo-062: A few fixes to binary ops in the interpreter
**Ground truth:** `compiler/ast/src/interpreter_value/evaluate.rs`:750-757, 884-888 -- `checked_rem` (mod/remainder) incorrectly allowed on signed integers (I8-I128), and missing comparison support for addresses and structs.
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 0 | 2 | 5 comments: integer parsing expect() panic, Mul with unsuffixed Group operands, struct map key collision (HashMap<Symbol> vs HashMap<GlobalId>), ternary not propagating expected_ty, function call argument truncation. FP=2 |
| Opus diff | . | 0 | 1 | 4 comments: ternary expected_ty, tuple expected_ty, struct map collision, assignment expected_ty. All about type propagation, none about the actual bug |
| Sonnet repo | . | 0 | 0 | Timed out (600s) |
| Opus repo | . | 0 | 2 | 5 comments: struct init value stack leak, struct map collision, ternary expected_ty, integer Pow with unsuffixed rhs, tuple expected_ty |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Complete miss across all tools. The bug (signed integer mod and missing address/struct comparison) is in the evaluate.rs match arms -- a very pattern-heavy file with dozens of similar `(Value::XX(x), Value::XX(y)) => x.checked_rem(y).map(Value::XX)` lines. Tools focused on type propagation issues in the interpreter instead. The struct map key collision (HashMap<Symbol> vs HashMap<GlobalId>) was flagged by three tools and is a genuine concern, but it is not the tracked bug. The mod-on-signed-integers bug requires domain knowledge (Aleo's instruction set does not support signed mod). Ground truth is clear and well-targeted.

---

### leo-064: Remove input files from run and execute CLI commands
**Ground truth:** `leo/cli/cli.rs`:317, `leo/cli/commands/execute.rs`:64-97, `leo/cli/commands/run.rs`:19-98 -- Removing file-based input handling and adding network dispatch for run/execute commands. Also NETWORK default changed to "mainnet".
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | -- | -- | -- | No result for this case |
| Greptile | -- | -- | -- | No result for this case |
| Sonnet diff | . | 0 | 3 | 8 comments: move out of &self (compile error), open_manifest before init, Package::initialize with wrong path, removed name validation, missing NETWORK in .env, removed dotenv fallback, move in test (compile error), case-sensitive network name |
| Opus diff | X | 3 | 4 | 6 comments: C0 move out of &self (compile error), C1 handle_execute moves from &Execute (compile error), C2 handle_run moves from &Run (compile error), C3 open_manifest before init, C4 missing NETWORK in .env, C5 removed name validation. TP=2 |
| Sonnet repo | X | 3 | 3 | 8 comments: C0 move from &self, C1 open_manifest before init, C2 handle_run moves from &Run, C3 handle_execute moves from &Execute, C4 missing NETWORK, C5 removed dotenv fallback, C6 removed validation, C7 case-sensitive network |
| Opus repo | X | 3 | 4 | 7 comments: C0 open_manifest before init, C1 wrong path for Package::initialize, C2 move from &self, C3 moves from &Execute, C4 moves from &Run, C5 missing NETWORK, C6 case-sensitive network |
| CodeRabbit | -- | -- | -- | No result for this case |

**Analysis:** Three of four tools caught the bug (only Sonnet diff missed). The key finding is that `handle_execute` and `handle_run` take `&self`/`&Execute`/`&Run` (shared references) but attempt to move owned String/Vec fields out of them -- a Rust compile error. This is exactly the kind of bug AI tools excel at: type-system violations visible in the diff. Opus diff scored det=3/qual=4. Sonnet diff found the same individual issues but was scored caught=False, possibly because its comments were less precisely targeted at the buggy lines. Ground truth confidence C reflects that this is a refactoring PR with many changes, making it hard to define "the bug" precisely.

---

## Summary Statistics

| Tool | Cases with results | Caught | Avg Det (caught) | Avg Qual |
|------|-------------------|--------|-------------------|----------|
| Copilot | 0/14 | -- | -- | -- |
| Greptile | 0/14 | -- | -- | -- |
| CodeRabbit | 0/14 | -- | -- | -- |
| Sonnet diff | 14/14 | 4 | 1.0 | 1.7 |
| Opus diff | 14/14 | 5 | 2.8 | 1.8 |
| Sonnet repo | 14/14 | 2 | 1.5 | 0.4 |
| Opus repo | 14/14 | 5 | 2.2 | 1.8 |

## Key Findings

### 1. Commercial tools absent from this group
Copilot, Greptile, and CodeRabbit had no results for any of these 14 cases. All analysis is limited to the Anthropic SDK tools (Sonnet and Opus in diff-only and diff+repo modes).

### 2. Opus consistently outperforms Sonnet
Opus diff caught 5/14 vs Sonnet diff's 4/14. More importantly, Opus diff's average detection score when catching was 2.8 vs Sonnet diff's 1.0. Opus produced more precise, higher-confidence findings.

### 3. Repo context hurt more than it helped
Sonnet repo caught only 2/14 cases and timed out on 6/14 (600s limit). The extra repo context caused Sonnet to spend its budget exploring the codebase rather than analyzing the diff. Opus repo (5/14) matched Opus diff (5/14) but caught different cases -- repo context helped on leo-033, leo-044, leo-049, and leo-050 where diff-only also succeeded, but repo uniquely helped on none that diff-only missed.

### 4. Diff-only was sufficient for localized bugs
For well-defined bugs (leo-044 wrong variable, leo-050 missing existence check, leo-064 move from shared reference), diff-only review was sufficient and faster. Repo context added value mainly for understanding broader impact.

### 5. Sonnet repo timeout problem
Sonnet repo timed out (600s) on 6 of 14 cases (leo-027, leo-030, leo-044, leo-049, leo-054, leo-062), producing zero comments in each. These were generally larger diffs. This is a significant reliability issue.

### 6. Domain knowledge gap
Cases requiring Aleo-specific knowledge (leo-023 VM registration, leo-062 signed mod semantics) were universally missed. Tools performed best on general programming bugs: wrong variables (leo-044), missing null checks (leo-050), type system violations (leo-064), and code quality (leo-033).

### 7. Ground truth quality varies
- **leo-029** (version bump) is arguably not a reviewable bug -- no tool should be penalized for missing it.
- **leo-049** has incomplete ground truth -- only test file lines, no compiler source lines.
- **leo-026** (confidence C) is a feature addition, not a bug fix.
- **leo-064** (confidence C) is a broad refactoring with diffuse bug definition.

### 8. Secondary findings have real value
Even when tools missed the tracked bug, they frequently found genuine issues: compile errors, panic-on-unwrap, format string bugs, performance problems, dead code. In cases like leo-060, the secondary findings may be more practically valuable than the tracked bug.
# Group 2 Analysis: leo-066 through leo-095

Cases leo-066 through leo-095 (14 active, excluding excluded cases).

**Tool legend:**
- **Copilot** = GitHub Copilot (PR review, run-04-pr-tools)
- **Greptile** = Greptile (PR review, run-04-pr-tools)
- **CodeRabbit** = CodeRabbit (PR review, run-04-pr-tools)
- **Sonnet diff** = Agent SDK with Sonnet, diff-only (run-01-sdk-diffonly)
- **Opus diff** = Agent SDK with Opus, diff-only (run-04-opus-diffonly)
- **Sonnet repo** = Agent SDK with Sonnet, diff+repo (run-03-sdk-repo-v2)
- **Opus repo** = Agent SDK with Opus, diff+repo (run-05-opus-repo)

---

### leo-066: [Fix] Add network dependencies before making deployments.
**Ground truth:** `leo/cli/commands/deploy.rs`:244-298, `leo/cli/commands/execute.rs`:350-380
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | Y | 0 | 2 | Flagged unwrap() panic risk and unreachable code paths, missed dependency ordering |
| Greptile | Y | 0 | 2 | Found novel issues in deploy/execute but missed core dependency-ordering bug |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | Y | 1 | 3 | Identified issues in the right area, flagged network I/O robustness |
| Opus diff | Y | 0 | 2 | Found novel issues but missed the dependency ordering bug |
| Sonnet repo | Y | 3 | 0 | One TP matching a buggy line in deploy.rs |
| Opus repo | N | 0 | 2 | Completely missed the dependency ordering bug |

**Analysis:** The ground truth covers a large span (54 buggy lines across two files) about missing network dependency resolution before deployments. This is a high-level architectural issue -- programs must have their dependencies deployed first. Most tools flagged code quality issues in the same files (unwrap on network I/O, unreachable branches) but missed the core ordering logic. The B-tier blame confidence is justified: the introducing PR "First attempt to report transaction status" added the deployment flow without proper dependency ordering, but the connection is indirect. Sonnet repo got det=3 but qual=0, suggesting it identified the right code mechanically but provided no useful review context -- a scorer inconsistency where detection and quality diverge sharply.

---

### leo-067: Fix ArrayAccess in the interpreter and test it.
**Ground truth:** `interpreter/src/cursor.rs`:634, 654-660
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | N | 0 | 2 | Reviewed other areas, missed ArrayAccess bug entirely |
| Greptile | N | 0 | 3 | Thorough review but completely missed the cursor.rs ArrayAccess bug |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | N | 0 | 2 | Missed ArrayAccess/CoreConstant handling bug |
| Opus diff | N | 0 | 2 | Missed ArrayAccess handling bug |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 0 | 3 | Thorough review but missed the ArrayAccess bug in cursor.rs |

**Analysis:** Zero tools caught this bug. The ground truth is strong (A-tier blame) -- the Debugger PR (#28441) introduced interpreter code where `ArrayAccess` fell through to a `CoreConstant` handler instead of being handled as an array index operation. The bug is subtle: it requires understanding the interpreter's expression evaluation dispatch and noticing that `ArrayAccess` was missing its own handler, falling into the constant lookup path at line 654. The introducing PR was large ("Debugger" -- no description) making it hard for diff-only tools to understand context. Even repo-access tools missed it because the fix is a small logic addition that requires understanding the interpreter's value stack semantics.

---

### leo-071: Allow array indices to be unsuffixed literals
**Ground truth:** `compiler/passes/src/code_generation/expression.rs`:87-88, `compiler/passes/src/type_checking/expression.rs`:85-86, plus many test expectation files
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | Y | 1 | 2 | Touched buggy line 88 in codegen but addressed typo, not the unsuffixed literal bug |
| Greptile | Y | 0 | 1 | Caught typo in 'lierals' on buggy line but missed the actual type inference bug |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | N | 0 | 1 | Missed the unsuffixed literal inference for array indices |
| Opus diff | Y | 1 | 2 | Flagged related area but never identified the core bug |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 1 | 2 | Flagged related area but missed the core bug |

**Analysis:** The bug is that the unsuffixed-literals feature (#28642) failed to handle array indices -- `visit_expression_reject_numeric` was used instead of allowing inference to u32. Copilot and Greptile both landed on the buggy lines but for superficial reasons (a typo "lierals" in a comment on line 88). The judge awarded caught=Y and det=1 for touching the right lines, but neither tool understood the actual type inference issue. This is a case where surface-level findings accidentally overlap with ground truth lines without identifying the real bug. The A-tier blame is valid -- the unsuffixed literals PR directly introduced the gap.

---

### leo-072: Fix an issue with indexing an array with a const initialized with an unsuffixed integer
**Ground truth:** Multiple files in compiler/parser, compiler/passes (destructuring, codegen, SSA, type_checking), compiler/ast
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | N | 0 | 1 | Missed unsuffixed integer handling in codegen entirely |
| Greptile | N | 0 | 1 | Missed the unsuffixed integer codegen bug |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | N | 0 | 0 | No comments produced |
| Opus diff | N | 0 | 0 | No comments produced |
| Sonnet repo | N | 0 | 0 | No comments produced |
| Opus repo | N | 0 | 1 | Discussed ownership/move semantics, irrelevant to the bug |

**Analysis:** Universal miss across all tools. The bug is in the code_generation pass: when a constant is initialized with an unsuffixed integer and then used as an array index, the codegen emits incorrect VM instructions. The introducing PR (#28557, "Refactoring, especially Expression and Statement") was a massive refactoring that changed `Expression`, `Statement`, `Access`, and `Literal` types -- the buggy lines are scattered across 6+ files in parser, destructuring, SSA, and codegen passes. Both Sonnet and Opus diff-only produced zero comments, likely overwhelmed by the size of the refactoring diff. The A-tier blame is appropriate -- the refactoring directly restructured the expression types that the later unsuffixed-integer feature depended on.

---

### leo-073: Small fixes to errors.
**Ground truth:** `errors/src/common/formatted.rs`:91-92, 95, 110, 113-122, 125, 128
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | Y | 2 | 3 | Directly targeted formatted.rs line 123, identified Display impl issue |
| Greptile | N | 0 | 0 | No comments produced |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | N | 0 | 3 | Good review quality but missed formatted.rs Display bug |
| Opus diff | N | 0 | 0 | No comments produced |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 0 | 2 | Missed the formatted.rs bug entirely |

**Analysis:** Only Copilot caught this. The bug is in the `Formatted` error type's `Display` impl: when the source file cannot be found, it returned early without printing the error message. The introducing PR (#28566, "Revise SourceMap and related types") rewrote how source locations were tracked, and the new `Display` impl silently swallowed errors when source files were missing. Copilot correctly identified the Display implementation issues in formatted.rs (det=2, qual=3). The B-tier blame is appropriate -- the SourceMap rewrite introduced the new Display code, but the connection between the SourceMap changes and the error display fallback requires understanding the error reporting pipeline.

---

### leo-074: Clone all internal record inputs as outputs.
**Ground truth:** `compiler/ast/src/expressions/mod.rs`:38-44
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | N | 0 | 2 | Missed the expressions/mod.rs module declaration bug |
| Greptile | N | 0 | 2 | Missed the expressions/mod.rs bug |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | N | 0 | 1 | Missed module declaration/re-export issue |
| Opus diff | N | 0 | 1 | Identified the area (ternary, unary, value modules) but not the bug |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | Y | 1 | 2 | Flagged the right file and line in expressions/mod.rs |

**Analysis:** The ground truth here is questionable. The buggy lines are module declarations (`mod ternary`, `pub use ternary::*`, etc.) in expressions/mod.rs, blamed back to "Core circuits" PR #1885 from 2022. The fix PR title says "Clone all internal record inputs as outputs" which is about codegen correctness for record types, yet the ground truth points to basic module re-exports. The B-tier blame is generous -- the introducing PR is from 3 years before the fix, suggesting the blame traced to the original file structure rather than the actual logic bug. Only Opus repo (det=1) flagged anything in the right file, getting partial credit. This case likely has a ground truth validity issue: the real bug is in record input/output cloning logic, not in the module declarations that blame happened to trace to.

---

### leo-075: fix: typos in panic function and comments
**Ground truth:** `compiler/passes/src/write_transforming/mod.rs`:32-38, `compiler/passes/src/write_transforming/visitor.rs`:36-42, `compiler/passes/src/type_checking/expression.rs`:875
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | Y | 3 | 3 | Correctly identified 'assignemnts' -> 'assignments' and other typos |
| Greptile | Y | 3 | 3 | Correctly identified the 'assignemnts' typo |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | N | 0 | 1 | Missed typos in comments entirely |
| Opus diff | N | 0 | 1 | Missed documentation typos |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 0 | 1 | Missed documentation typos |

**Analysis:** This is a typo-fix case: the fix PR corrects spelling errors in comments ('assignemnts' -> 'assignments') and a panic message. Copilot and Greptile both caught the typos perfectly (det=3, qual=3). All agent SDK configurations missed them, which is notable -- the in-house agent focused on logic and correctness patterns rather than comment/doc spelling. The C-tier blame is correct: the typos were introduced in the "Write to array and struct members" PR (#28559) which added the write_transforming pass with misspelled comments. This is a low-severity case (severity=low) where commercial PR tools excel at surface-level catches that the agent SDK deprioritizes.

---

### leo-082: Handle leading zeros in field, group, and scalar literals.
**Ground truth:** `compiler/ast/src/expressions/literal.rs`:116-150, 157, 192-197
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | Y | 0 | 2 | Found novel issues but missed group literal type-checking validation |
| Greptile | Y | 0 | 2 | Missed group literal type checking, found other issues |
| CodeRabbit | N | 0 | 0 | Skipped review (too many changes) |
| Sonnet diff | N | 0 | 2 | Missed literal.rs Display formatting and leading zeros bug |
| Opus diff | N | 0 | 2 | Missed the literal.rs bug |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 1 | 2 | Flagged related area but never identified actual bug |

**Analysis:** The bug involves missing validation for group literals and improper handling of leading zeros in field/scalar literals in the Display implementation. The introducing PR (#28383, "hex and binary literals for integers") added hex/binary/octal literal support but the Display implementation for non-integer literals (field, group, scalar) did not strip leading zeros or validate group literal syntax. The fix was 189 lines added across 12 files (large PR). Copilot and Greptile both marked caught=Y but det=0, meaning the judge found novel findings in the right area but nothing that identified the actual leading-zeros/group-validation issue. The large diff size (PR size=large) likely diluted attention. This is a domain-specific Aleo type system bug that requires understanding the VM's literal format requirements.

---

### leo-085: Fix parsing of negatives for `leo run`.
**Ground truth:** `leo/cli/commands/deploy.rs`:24, 45-64, `leo/cli/commands/mod.rs`:30-31
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | N | 0 | 0 | No comments produced |
| Greptile | Y | 0 | 3 | Found novel issues but missed Deploy->LeoDeploy rename and negative parsing |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | Y | 0 | 3 | Found novel issues, missed the actual rename/parsing bug |
| Opus diff | Y | 0 | 2 | Missed the Deploy rename and negative parsing fix |
| Sonnet repo | Y | 3 | 0 | Mechanically matched a buggy line |
| Opus repo | N | 0 | 2 | Missed the Deploy rename bug |

**Analysis:** The fix renames `Deploy` to `LeoDeploy` and fixes negative value parsing for `leo run`. The C-tier blame traces to "Leo Deploy" PR #26901 from 2024 -- a naming collision where the `Deploy` struct name was too generic. Multiple tools scored caught=Y but det=0, meaning they found novel things in the same files without identifying the actual naming issue. Sonnet repo got det=3 but qual=0, another case of mechanical line matching without meaningful review. Copilot produced zero comments, an unusual total miss. The ground truth is somewhat weak: renaming a struct is more of a code quality improvement than a bug fix, and the blame back to the original Deploy introduction (C-tier) reflects this ambiguity.

---

### leo-086: Update comment about Pedersen hash types.
**Ground truth:** `compiler/passes/src/type_checking/checker.rs`:274, 288-292, 297, 311-316
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | N | 0 | 0 | No comments produced |
| Greptile | N | 0 | 2 | Missed Pedersen hash type-checking bug |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | N | 0 | 2 | Missed Pedersen hash type checking |
| Opus diff | N | 0 | 2 | Missed Pedersen hash type checking |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 0 | 1 | Missed the overly permissive type checking |

**Analysis:** Universal miss. The bug is in type checking for Pedersen hash functions: the checker was too permissive, allowing composite types (structs/records) as inputs to Pedersen64/Pedersen128 when only primitive types should be allowed. The fix PR is tiny (6 lines added, 2 deleted, 1 file) -- just updating comments and tightening type checks. The A-tier blame correctly traces to PR #28481 ("Refactor and improve type checking") which introduced the overly permissive check. This is a domain-specific bug requiring knowledge of Aleo's Pedersen hash circuit constraints. The introducing PR was a large type-checker refactoring, so the specific Pedersen constraint was likely an oversight in a sea of changes. Copilot produced zero comments, suggesting the diff was too small or uninteresting for its heuristics.

---

### leo-088: Small fixes to Display implementations.
**Ground truth:** `compiler/ast/src/functions/mod.rs`:139-145, `compiler/ast/src/struct/mod.rs`:138, 140
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | N | 0 | 0 | No comments produced |
| Greptile | Y | 1 | 3 | Flagged Display-related issues in the right area |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | Y | 3 | 3 | Correctly identified struct/mod.rs:138 missing struct keyword in Display |
| Opus diff | Y | 3 | 3 | Correctly identified the struct Display bug |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 1 | 3 | Flagged Display issues but missed specific known bugs |

**Analysis:** The bugs are in `Display` implementations: the function output formatting (mod.rs:139-145) and struct Display missing the `struct` keyword (struct/mod.rs:138). Both Sonnet diff and Opus diff scored det=3, qual=3 -- correctly identifying the struct Display bug at the exact line. This is one of the strongest agent SDK performances in the dataset. Greptile got det=1 (in the right area but not exact). Copilot produced zero comments. The repo-access variants both degraded: Sonnet repo produced nothing, Opus repo got det=1. This reinforces the finding that diff-only mode outperforms repo mode -- the focused diff allowed the agent to scrutinize the Display implementations closely.

---

### leo-090: Correctly parse double negation.
**Ground truth:** `compiler/parser/src/parser/expression.rs`:266-293
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | Y | 1 | 2 | Touched negation-folding logic but didn't identify double-negation bug |
| Greptile | N | 0 | 1 | Completely missed the double-negation parsing bug |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | Y | 1 | 2 | Identified negation folding area, partial detection |
| Opus diff | N | 0 | 0 | No comments produced |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 0 | 0 | No comments produced |

**Analysis:** The bug is that the parser folds `-(-x)` by absorbing the minus sign into the literal, producing `--x` instead of negating a negative value. This was a long-standing issue (introduced in 2023, PR #2522 "Add `id` to AST nodes", A-tier blame). The fix correctly separates the unary negation from the literal value. Only Copilot and Sonnet diff scored det=1, touching the negation-folding code without fully identifying the double-negation issue. The introducing PR is interesting: adding `id` fields to AST nodes didn't directly create the bug, but the blame traces to the parser code that was reorganized during that change. The real bug pattern (absorbing negation into literals) was present earlier, making the A-tier blame somewhat generous.

---

### leo-091: Fix up Display implementations for AST nodes.
**Ground truth:** `compiler/ast/src/functions/mod.rs`:139, `compiler/ast/src/program/mod.rs`:43, `compiler/ast/src/statement/block.rs`:40, `compiler/ast/src/statement/conditional.rs`:40, `compiler/ast/src/statement/iteration.rs`:56-57, `errors/src/emitter/mod.rs`:46, `compiler/ast/src/program/program_scope.rs`:45, 48, 51
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | N | 0 | 0 | No comments produced |
| Greptile | N | 0 | 1 | Mischaracterized PR as mechanical formatting |
| CodeRabbit | N | 0 | 0 | Rate-limited, never reviewed |
| Sonnet diff | N | 0 | 0 | No comments produced |
| Opus diff | N | 0 | 0 | No comments produced |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 0 | 0 | No comments produced |

**Analysis:** Universal miss -- no tool caught anything. The fix PR is large (291 lines added, 72 deleted, 23 files changed) and completely rewrites Display implementations across the entire AST. The buggy lines are spread across 7 files covering functions, programs, statements, and error formatting. The C-tier blame traces to PR #2142 ("Fix output type for finalize block") from 2022 -- the original Display implementations were wrong from the beginning. This is essentially a complete rewrite case where the "bug" is accumulated technical debt in Display formatting rather than a discrete defect. The massive diff (23 files) with many incremental formatting changes likely overwhelmed all tools. Most tools produced zero comments, suggesting the diff appeared as routine code cleanup.

---

### leo-095: chore: remove redundant words and fix some typos in comment
**Ground truth:** `compiler/passes/src/common/graph/mod.rs`:108
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| Copilot | Y | 3 | 3 | Correctly identified the 'to to' duplicate word typo |
| Greptile | N | 0 | 1 | Missed the 'to to' typo |
| CodeRabbit | N | 0 | 0 | Auto reviews disabled, skipped |
| Sonnet diff | Y | 3 | 4 | Correctly identified the exact 'to to' typo on line 108 |
| Opus diff | Y | 3 | 4 | Correctly identified the exact 'to to' typo on line 108 |
| Sonnet repo | N | 0 | 0 | No useful output |
| Opus repo | N | 0 | 0 | No comments produced |

**Analysis:** A single-line typo case: "to to" should be "to" on line 108 of the graph module. Copilot and both diff-only agents caught it perfectly (det=3). Both agent diff-only variants scored qual=4 (exceptional) -- the highest quality score in the dataset. Greptile missed it, and both repo-access agents produced nothing. The C-tier blame traces to PR #2178 ("Add `DiGraph` data structure") from 2023. This case validates that simple, focused typo detection works well in diff-only mode. The stark contrast between diff-only (3/3) and repo-access (0/0) for both Sonnet and Opus is a clean demonstration of the focus-dilution effect.

---

## Group 2 Summary

| Case | Difficulty | Blame | Copilot | Greptile | CodeRabbit | Sonnet-diff | Opus-diff | Sonnet-repo | Opus-repo |
|------|-----------|-------|---------|----------|------------|-------------|-----------|-------------|-----------|
| leo-066 | hard | B | Y(0/2) | Y(0/2) | N(0/0) | Y(1/3) | Y(0/2) | Y(3/0) | N(0/2) |
| leo-067 | medium | A | N(0/2) | N(0/3) | N(0/0) | N(0/2) | N(0/2) | N(0/0) | N(0/3) |
| leo-071 | hard | A | Y(1/2) | Y(0/1) | N(0/0) | N(0/1) | Y(1/2) | N(0/0) | N(1/2) |
| leo-072 | hard | A | N(0/1) | N(0/1) | N(0/0) | N(0/0) | N(0/0) | N(0/0) | N(0/1) |
| leo-073 | medium | B | Y(2/3) | N(0/0) | N(0/0) | N(0/3) | N(0/0) | N(0/0) | N(0/2) |
| leo-074 | hard | B | N(0/2) | N(0/2) | N(0/0) | N(0/1) | N(0/1) | N(0/0) | Y(1/2) |
| leo-075 | medium | C | Y(3/3) | Y(3/3) | N(0/0) | N(0/1) | N(0/1) | N(0/0) | N(0/1) |
| leo-082 | hard | B | Y(0/2) | Y(0/2) | N(0/0) | N(0/2) | N(0/2) | N(0/0) | N(1/2) |
| leo-085 | medium | C | N(0/0) | Y(0/3) | N(0/0) | Y(0/3) | Y(0/2) | Y(3/0) | N(0/2) |
| leo-086 | easy | A | N(0/0) | N(0/2) | N(0/0) | N(0/2) | N(0/2) | N(0/0) | N(0/1) |
| leo-088 | medium | B | N(0/0) | Y(1/3) | N(0/0) | Y(3/3) | Y(3/3) | N(0/0) | N(1/3) |
| leo-090 | medium | A | Y(1/2) | N(0/1) | N(0/0) | Y(1/2) | N(0/0) | N(0/0) | N(0/0) |
| leo-091 | hard | C | N(0/0) | N(0/1) | N(0/0) | N(0/0) | N(0/0) | N(0/0) | N(0/0) |
| leo-095 | medium | C | Y(3/3) | N(0/1) | N(0/0) | Y(3/4) | Y(3/4) | N(0/0) | N(0/0) |

Format: Caught(Det/Qual)

### Catch rates in this group

| Tool | Caught | Rate |
|------|--------|------|
| Copilot | 7/14 | 50% |
| Greptile | 5/14 | 36% |
| CodeRabbit | 0/14 | 0% |
| Sonnet diff | 5/14 | 36% |
| Opus diff | 5/14 | 36% |
| Sonnet repo | 2/14 | 14% |
| Opus repo | 1/14 | 7% |

### Key observations

1. **CodeRabbit completely failed** -- rate-limited or auto-reviews disabled on every single case in this group, producing zero actionable reviews.

2. **Repo access severely degrades performance** -- Sonnet drops from 36% to 14%, Opus from 36% to 7%. The two Sonnet-repo "catches" both had det=3 but qual=0 (mechanical line matching, no useful review), making them arguably false catches.

3. **Diff-only agents excel at Display/formatting bugs** -- leo-088 is the standout: both Sonnet and Opus diff-only scored det=3, qual=3 on the struct Display bug, while all other tools missed it.

4. **Typo detection split** -- Copilot catches typos (leo-075, leo-095) while agent SDK misses them in diff-only mode. But for leo-095 (single-line typo), the agent SDK caught it perfectly. The difference is that leo-075's typos were in documentation comments that the agent deprioritized.

5. **Universal misses on hard+deep bugs** -- leo-067 (interpreter ArrayAccess), leo-072 (unsuffixed integer codegen), leo-086 (Pedersen type checking), and leo-091 (complete Display rewrite) were missed by all tools. These require deep domain knowledge of Aleo's compiler architecture.

6. **Scorer inconsistency: caught=Y with det=0** -- Multiple cases show tools marked as "caught" with detection=0 (e.g., leo-066 Copilot, leo-082 Copilot/Greptile). The judge awarded caught=Y for TP-novel findings in the same file but det=0 because those findings did not match the specific ground truth bug. This creates a misleading catch rate where "caught" does not mean "identified the known bug."

7. **Ground truth quality varies** -- leo-074 (B-tier, module declarations blamed from 3 years ago), leo-085 (C-tier, struct rename), and leo-091 (C-tier, complete rewrite) have ground truth that does not cleanly map to a single discrete bug, making tool evaluation against these cases less reliable.
# Group 3 Analysis: leo-104 through leo-202

Cases analyzed: leo-104, leo-115, leo-116, leo-117, leo-119, leo-136, leo-139, leo-157, leo-159, leo-162, leo-164, leo-168, leo-181, leo-193, leo-200, leo-202

Tool abbreviations used in tables:
- **S-diff** = Sonnet agent-sdk diff-only (run-01)
- **S-repo** = Sonnet agent-sdk diff+repo (run-02)
- **S-v2** = Sonnet agent-sdk diff+repo v2 (run-03)
- **O-diff** = Opus agent-sdk diff-only (run-04)
- **O-repo** = Opus agent-sdk diff+repo (run-05)
- **CR** = CodeRabbit PR-tool (run-04)
- **Copilot** = GitHub Copilot PR-tool (run-04)
- **Greptile** = Greptile PR-tool (run-04)

---

### leo-104: [Fix] Update HTTP headers in Leo CLI.
**Ground truth:** `leo/cli/commands/mod.rs:287-291`; `utils/retriever/src/retriever/mod.rs:525-528`
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | Yes | 3 | 4 | 301 branch is dead code; ureq returns non-2xx as Err, making match unreachable |
| S-repo | No* | 3 | 4 | Same finding -- 301 match arms unreachable in both files |
| S-v2 | Yes | 3 | 0 | Same core finding; also flagged catch-all arm for non-200 2xx codes |
| O-diff | No* | 3 | 4 | 301 branch dead code in both files; error info lost via broad map_err |
| O-repo | No* | 3 | 4 | map_err()?  catches 4xx/5xx before status match; 301 branch dead code |
| CR | No | 0 | 0 | Rate-limited, no review produced |
| Copilot | Yes | 2 | 3 | send_json returns Error::Status for non-2xx; map_err swallows it |
| Greptile | Yes | 3 | 4 | 301 match arms unreachable in both files (P1 severity) |

**Analysis:** Strong ground truth -- the ureq 2.x behavior where non-2xx responses are Err variants makes the status match arms dead code. This was the easiest case for tools: every agent run identified the core bug (det=3 across the board), though some scored `caught=False` due to scorer disagreement (likely a threshold issue in the judge). CodeRabbit was rate-limited throughout all PR-tool runs in this group. S-v2 scored qual=0 despite finding the bug, which appears to be a scorer anomaly (missing reasoning field). Greptile and Sonnet diff-only performed best here.

---

### leo-115: More cli fixes.
**Ground truth:** `leo/cli/cli.rs:55-58`; `leo/cli/commands/example.rs:20-40`; `leo/package/src/example.rs:20-40`; `leo/cli/commands/mod.rs:27`
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 2 | 3 | Flagged tic_tac_toe vs tictactoe naming inconsistency, wrong input paths |
| S-repo | No | 3 | 4 | Wrong include_str! paths (tic_tac_toe -> tictactoe), wrong input filenames |
| S-v2 | Yes | 3 | 0 | context.dir() returns parent dir not project dir; include_str! path wrong |
| O-diff | Yes | 2 | 3 | Inconsistent directory names between example.rs files; dead parameter |
| O-repo | Yes | 3 | 4 | include_str! references non-existent tic_tac_toe path; wrong input filenames |
| CR | No | 0 | 0 | Rate-limited, no review produced |
| Copilot | Yes | 1 | 2 | Flagged unused parameters but missed core path bugs |
| Greptile | Yes | 2 | 3 | Wrong input file path, wrong directory naming, dead code |

**Analysis:** Multi-file bug involving incorrect example paths and naming inconsistencies. Agent models performed well, especially with repo context -- Sonnet diff+repo and Opus diff+repo both achieved det=3 by verifying actual filesystem paths against include_str! macros. Copilot found the right files but focused on surface-level issues (unused parameters) rather than the path bugs. The bug spans many files, making it easier to catch partially but hard to identify completely.

---

### leo-116: Fixes to clap CLI code.
**Ground truth:** `leo/cli/cli.rs:76-80`; `leo/cli/commands/mod.rs:38-40`; `leo/cli/mod.rs:26-28`; `leo/cli/query_commands/*.rs` (multiple files, 8+ files)
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | Yes* | 0 | 3 | Found &&/\|\| logic bug in validation functions (novel, not the known bug) |
| S-repo | Yes* | 0 | 3 | Same &&/\|\| logic bug; missed clap ArgGroup naming conflicts |
| S-v2 | Yes* | 3 | 0 | Same &&/\|\| logic bug plus more findings |
| O-diff | Yes* | 0 | 3 | &&/\|\| logic bug, leading slash in URL, integer underflow |
| O-repo | Yes* | 0 | 3 | &&/\|\| logic bug in all three validation functions |
| CR | No | 0 | 0 | Rate-limited, no review produced |
| Copilot | Yes* | 2 | 2 | Found required_unless_present_any referencing non-existent "range" arg |
| Greptile | Yes* | 0 | 3 | Inverted validation logic; leading slash in URL paths |

**Analysis:** Low-confidence ground truth (C). The known bug is about clap ArgGroup naming conflicts that only manifest in the `dev` profile, plus argument short-name conflicts -- a very framework-specific issue. Every tool independently discovered the &&/\|\| logic bug in validation functions (is_valid_hash, is_valid_transaction_id, etc.), which is a real bug but not the known one. This is a strong case where tools found genuinely important novel bugs while missing the specific known issue. The scoring (det=0 for most) is technically correct but undersells the tools' value. Copilot uniquely caught a clap-specific issue (missing "range" argument reference). The C-tier blame confidence suggests the ground truth itself may be imprecise for this large refactoring PR.

---

### leo-117: Fix help messages for command line options.
**Ground truth:** `leo/cli/commands/mod.rs:115,117`
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 0 | 2 | Focused on update.rs: silent error swallowing in quiet mode |
| S-repo | No | 0 | 2 | Same update.rs issues; updater.rs return type mismatch |
| S-v2 | No | 0 | 0 | All comments on update.rs error handling |
| O-diff | No | 0 | 2 | Silent error discard in quiet mode; circular glob re-export |
| O-repo | No | 0 | 2 | Same quiet-mode error swallowing; circular re-export |
| CR | No | 0 | 0 | Rate-limited (FP=2 for meta-comments) |
| Copilot | No | 0 | 0 | No comments produced |
| Greptile | No | 0 | 2 | update.rs error silently discarded in quiet mode (P0) |

**Analysis:** Universal miss. The bug is incorrect help message text (cosmetic string content), which is essentially impossible for automated tools to catch without domain knowledge of what the correct help text should say. Every tool that produced comments instead focused on the update.rs changes in the same PR, finding legitimate issues there (error swallowing in quiet mode was flagged by 5+ tools). This case demonstrates a fundamental limitation: tools cannot validate semantic correctness of human-facing strings. The C-tier blame confidence and "easy" difficulty rating are contradictory -- easy for a human reviewer who reads help text, but near-impossible for tools.

---

### leo-119: Require comma separators (and nothing else) between struct members.
**Ground truth:** `compiler/parser/src/parser/file.rs:144-156,182-194`
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 0 | 2 | Stale parameter name in visitor; grammar nit ("an struct") |
| S-repo | No | 0 | 2 | Token::Circuit Display arm removed but variant kept; no error for circuit token |
| S-v2 | No | 0 | 0 | No comments |
| O-diff | No | 0 | 0 | No comments produced |
| O-repo | No | 0 | 1 | Wrong claim about Member struct usage; Token::Circuit Display issue |
| CR | No | 0 | 0 | Rate-limited (FP=2) |
| Copilot | No | 0 | 1 | Grammar nits only ("an struct", message text) |
| Greptile | No | 0 | 0 | No comments produced |

**Analysis:** Universal miss. The bug is that the parser accepts both commas and semicolons as struct member separators when it should only accept commas. This is a parser-level semantic issue embedded in a large circuit-to-struct renaming PR. The signal-to-noise ratio is very low: the actual parser logic change is buried among many mechanical renames. Even with repo context, no tool identified the separator acceptance logic. The C-tier blame confidence and "hard" difficulty are well-justified. This is a case where only a domain expert who understands Leo's grammar specification would catch the issue.

---

### leo-136: Remove --seed flag from account sign command
**Ground truth:** `leo/cli/commands/account.rs` (lines 24-380, many locations -- seed flag definition and all usage sites)
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | Yes* | 0 | 2 | PathBuf::parse infallible; private key as CLI arg; missed --seed |
| S-repo | Yes* | 0 | 2 | Misleading error messages; errors silently discarded; missed --seed |
| S-v2 | Yes* | 3 | 0 | Error propagation issues; cli_runtime_error misuse |
| O-diff | Yes* | 0 | 2 | Misleading test comments; private key exposure; missed --seed |
| O-repo | No | 0 | 1 | Only comment about error code insertion order |
| CR | No | 0 | 0 | Rate-limited (FP=2) |
| Copilot | Yes* | 0 | 2 | Error propagation issues; misleading test comments; missed --seed |
| Greptile | Yes* | 0 | 2 | Misleading test comments; private key exposure; missed --seed (FP=1) |

**Analysis:** Despite A-tier blame confidence, every tool missed the actual security bug: the --seed flag allowing deterministic nonce generation in cryptographic signatures. The `caught=True` flags in the scorer are misleading -- they appear to be triggered by comments in the right file (account.rs) but none identify the seed/nonce security issue. This is a cryptography-domain bug that requires understanding why deterministic nonces in signatures are dangerous. Tools instead found legitimate but unrelated issues (error handling, private key exposure via CLI args). The scorer's `caught=True` with `det=0` pattern across multiple tools is a red flag suggesting the `caught` field is too permissive when a tool comments on the right file but the wrong issue.

---

### leo-139: [Fix] Futures in Tuples
**Ground truth:** `compiler/ast/src/types/type_.rs:106,117,122,128`; `compiler/passes/src/common/symbol_table/mod.rs:125`; `compiler/passes/src/flattening/flatten_expression.rs:86`; `compiler/passes/src/type_checking/checker.rs:185`
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | Yes | 1 | 2 | eq_flat_relax_composite doc mismatch; asymmetric Mapping comparison |
| S-repo | Yes | 1 | 2 | Mapping arm uses eq_flat for value instead of eq_flat_relax_composite |
| S-v2 | Yes | 3 | 0 | Same Mapping arm inconsistency; dead error variants |
| O-diff | No | 0 | 0 | No comments produced |
| O-repo | Yes | 2 | 3 | Mapping comparison uses eq_flat (strict) instead of relaxed variant |
| CR | No | 0 | 0 | Rate-limited (FP=2) |
| Copilot | Yes | 1 | 1 | Flagged same Mapping asymmetry at type_.rs:122 |
| Greptile | No | 0 | 2 | Dead error variants only; missed eq_flat issue |

**Analysis:** The core bug is introducing `eq_flat_relax_composite` for relaxed type equality checking, with the key issue being inconsistent application (Mapping value uses strict eq_flat). Multiple tools identified the asymmetry in the Mapping arm at type_.rs:122, which is a genuine part of the bug. However, the broader changes across symbol_table, flatten_expression, and checker were missed by all tools. Opus diff-only produced zero comments (unusual), while Opus diff+repo gave the cleanest single-comment analysis (det=2, qual=3). This is a case where partial detection was common but full understanding of the cross-cutting type system change was not achieved by any tool.

---

### leo-157: [Fix] Compression formats for `leo update`.
**Ground truth:** `Cargo.toml:124,142-148`
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 0 | 0 | No comments produced |
| S-repo | No | 0 | 0 | No comments produced |
| S-v2 | No | 0 | 0 | No comments produced |
| O-diff | No | 0 | 1 | Stray `dependencies = { }` in linter/Cargo.toml (unrelated) |
| O-repo | No | 0 | 0 | No comments produced |
| CR | No | 0 | 0 | Rate-limited |
| Copilot | No | 0 | 1 | Same stray dependencies line; repeated dependency versions |
| Greptile | No | 0 | 1 | Same stray dependencies line |

**Analysis:** Universal miss. The bug is a missing feature flag (`deflate`) on the `self_update` crate's zip dependency in Cargo.toml. This is a build configuration issue that requires understanding that the update binary needs deflate decompression support. No tool can reason about Cargo feature flags' runtime effects from a diff alone. The few comments produced all targeted an unrelated `linter/Cargo.toml` formatting issue. Despite A-tier blame confidence (the fix is localized and clear), the bug type (dependency feature configuration) is outside the capability envelope of current code review tools.

---

### leo-159: Fix some doc.
**Ground truth:** `compiler/parser/src/parser/file.rs:298-313,331`
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 0 | 2 | Wrong type name in simple_node_impl! macro; external input mapping gap |
| S-repo | No | 3 | 4 | Found doc bug (parse_input says "output" not "input") plus battleship bugs |
| S-v2 | No | 0 | 0 | simple_node_impl! wrong type; SSA/flattening iteration issues |
| O-diff | No | 0 | 1 | simple_node_impl! wrong type; external input not mapped |
| O-repo | Yes | 0 | 2 | simple_node_impl! wrong type; external input not mapped; parse_input eat() |
| CR | No | 0 | 0 | Rate-limited |
| Copilot | Yes | 3 | 3 | Directly identified parse_input doc saying "output" instead of "input" |
| Greptile | Yes | 3 | 3 | Identified parse_input doc bug at file.rs:299 |

**Analysis:** The bug is a copy-paste error in doc comments (parse_input's docstring says "function output" instead of "function input"). Sonnet diff+repo actually found this (det=3) but was scored `caught=No` -- a scorer inconsistency. Copilot and Greptile both correctly identified the exact bug. Most agent runs were distracted by the `simple_node_impl!(FunctionOutputExternal)` issue (a wrong type name in a macro invocation), which is a real novel bug but not the known one. This case shows how doc bugs in a PR with many real code changes get buried -- the tools often found more "interesting" bugs and missed the simpler doc fix.

---

### leo-162: [Fix] Recommend build from source in documentation
**Ground truth:** `README.md:87-109`
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 0 | 2 | MACOSX_DEPLOYMENT_TARGET issues in release.yml |
| S-repo | No | 0 | 1 | MACOSX_DEPLOYMENT_TARGET in release.yml |
| S-v2 | No | 0 | 0 | MACOSX_DEPLOYMENT_TARGET in release.yml |
| O-diff | No | 0 | 0 | No comments produced |
| O-repo | No | 0 | 0 | No comments produced |
| CR | No | 0 | 0 | Rate-limited (FP=2) |
| Copilot | Yes* | 1 | 1 | Touched README crates.io section but misidentified issue |
| Greptile | Yes* | 1 | 2 | Banner comment syntax; MACOSX_DEPLOYMENT_TARGET; missing version constraint |

**Analysis:** The bug is that the crates.io/cargo install section should be removed from README until snarkvm releases a compatible version. Copilot touched the right area but flagged a link/name mismatch rather than the need to remove the section entirely. Greptile commented on README but focused on different issues. Agent runs were entirely distracted by the CI workflow changes (MACOSX_DEPLOYMENT_TARGET), missing the README content issue. This is a domain-knowledge bug: you need to know that snarkvm hasn't released a new version to understand why cargo install instructions are problematic. CodeRabbit was uniquely useful here in the run-04 PR tools -- it actually scored det=2 with tp=6 on the testnet defaults (this is leo-164, not this case). Documentation correctness bugs that require external context remain very hard for tools.

---

### leo-164: [Fix] Set defaults to `testnet`.
**Ground truth:** `errors/src/errors/utils/util_errors.rs:117`; many `.env` files; `run.sh` scripts; `leo/cli/commands/add.rs:31`
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | Yes* | 0 | 2 | Flagged hardcoded private keys in .env; missed testnet default issue |
| S-repo | No | 0 | 2 | add.rs panic on invalid network; parser error message change |
| S-v2 | No | 0 | 0 | Hardcoded network in env.rs; panic on invalid network |
| O-diff | No | 2 | 3 | Correctly identified hardcoded mainnet should be testnet in env.rs/account.rs |
| O-repo | No | 1 | 2 | Same hardcoded mainnet issue but in env.rs (not in ground truth lines) |
| CR | Yes | 2 | 2 | Identified mainnet exposure with private keys across .env files |
| Copilot | Yes | 2 | 3 | Flagged util_errors.rs:117 hardcoded mainnet; risky default for deploy |
| Greptile | No | 1 | 2 | Hardcoded mainnet in env.rs (not in ground truth line list) |

**Analysis:** A sprawling bug across 30+ files where "mainnet" should be "testnet". The ground truth spans .env files, shell scripts, error messages, and CLI defaults. Opus diff-only gave the best agent analysis (det=2), correctly identifying the pattern even though it targeted env.rs/account.rs rather than the exact ground truth lines. CodeRabbit -- in one of its few non-rate-limited reviews in this group -- identified the mainnet exposure pattern. Copilot correctly flagged util_errors.rs:117. The wide ground truth makes scoring tricky: tools that identify the correct class of bug in slightly different files than the ground truth list get det=1 instead of det=2+. This case highlights how ground truth for bulk-change PRs can be somewhat arbitrary in which specific lines are listed.

---

### leo-168: Fix and improve some doc.
**Ground truth:** `asg/src/lib.rs:27`; `ast/src/lib.rs:23-24`; `errors/src/common/mod.rs:34-35,45`; `errors/src/lib.rs:24,32`; `parser/src/lib.rs:23`; `test-framework/src/lib.rs:25-26`
**Blame confidence:** C

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 0 | 2 | circuit_self removal; deleted error variants |
| S-repo | No | 0 | 3 | Extra forward-slash in doc; 'cricuit' typo; stale error message prefix |
| S-v2 | No | 0 | 0 | No comments |
| O-diff | No | 0 | 3 | Deleted error variants shift error codes; 'covnert' typo unfixed |
| O-repo | No | 0 | 3 | 'covnert' typos; "should be be" double word; stale error prefix |
| CR | No | 0 | 0 | Skipped review (PR exceeded 150-file limit, 299 files) |
| Copilot | No | 0 | 0 | No comments produced |
| Greptile | No | 0 | 3 | Malformed doc comment; duplicated word; multiple error code shifts |

**Analysis:** Universal miss on the known bugs, which are doc-attribute fixes (include_str paths for lib.rs crate-level docs), a 'deserialze' typo, a double period, and a 'cleaneronce' missing space. This is a massive 299-file PR where CodeRabbit refused to review due to file count limits. The known bugs are needle-in-haystack documentation issues. However, every tool that produced comments found genuinely valuable novel bugs: 'covnert' typos, "should be be" duplications, malformed doc comments, and error code shifts from deleted variants. The C-tier blame confidence is appropriate -- the ground truth is documentation quality issues that are hard to attribute to a single introducing PR. The tools' novel findings (qual=3 from multiple tools) were arguably more valuable than the known bugs.

---

### leo-181: [Fix] Panic on unknown variable.
**Ground truth:** `compiler/passes/src/type_checking/check_expressions.rs:480,482,648,711`
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 0 | 0 | No comments produced |
| S-repo | No | 0 | 0 | No comments produced |
| S-v2 | No | 0 | 0 | No comments produced |
| O-diff | No | 0 | 0 | No comments produced |
| O-repo | No | 0 | 0 | No comments produced |
| CR | No | 0 | 0 | Rate-limited |
| Copilot | No | 0 | 1 | Only commented on .rustfmt.toml/.rusty-hook.toml config |
| Greptile | No | 0 | 1 | Only commented on .rusty-hook.toml and .rustfmt.toml config |

**Analysis:** Complete failure across all tools. The bug is that type inference panics (unwrap) when encountering an unknown variable -- the fix adds graceful error handling. This is a high-severity runtime bug that requires understanding the control flow: `unwrap()` on a lookup that can fail if the user writes invalid code. The introducing PR appears to be a large reformatting/tooling PR (.rustfmt.toml, .rusty-hook.toml changes), which explains why tools focused on config files rather than the type checker. The actual buggy lines in check_expressions.rs may not have had visible changes in the introducing PR's diff, making this bug invisible to diff-based review. This case strongly argues for whole-file analysis beyond just the diff.

---

### leo-193: [Fix] External struct in async function
**Ground truth:** `compiler/ast/src/stub/function_stub.rs:271`
**Blame confidence:** B

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 3 | 3 | Found _program unused parameter (the bug) but in FP-heavy review |
| S-repo | No | 0 | 0 | No comments produced |
| S-v2 | No | 0 | 0 | Wrong key_name usage; _is_async unused |
| O-diff | No | 0 | 0 | No comments produced |
| O-repo | No | 0 | 0 | No comments produced |
| CR | No | 0 | 0 | Rate-limited |
| Copilot | Yes | 2 | 2 | Directly flagged _program unused parameter at line 271 |
| Greptile | No | 0 | 1 | Only style nits (unnecessary braces, extra imports) |

**Analysis:** The bug is that the `program` parameter in `from_finalize` was renamed to `_program`, silently discarding what should be used for external struct resolution. Copilot was the only tool to directly and correctly flag this. Sonnet diff-only found it in comment 4 (det=3, tp=1) but was scored `caught=No`, likely because the overall review had 4 FPs diluting the signal. Multiple agent runs produced zero comments -- the diff may have appeared as a simple refactoring that didn't warrant review. This case demonstrates Copilot's strength at catching unused/renamed parameters, a pattern it's specifically trained for.

---

### leo-200: [Fix] Flattening finalize.
**Ground truth:** `compiler/passes/src/flattening/flatten_program.rs:19`; many `tests/expectations/compiler/**/*.out` files
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | Yes | 2 | 3 | Flagged flatten_program.rs:19 -- ProgramScope/Statement imports added without visible usage |
| S-repo | No | 0 | 0 | No comments produced |
| S-v2 | No | 0 | 0 | No comments produced |
| O-diff | Yes* | 0 | 1 | Flagged same line but misunderstood (imports are part of fix, not bug) |
| O-repo | No | 0 | 2 | Test expectation hashes changed; pre-existing error rename |
| CR | No | 0 | 0 | Rate-limited |
| Copilot | No | 0 | 1 | Test case assertion logic; error message text |
| Greptile | No | 0 | 1 | Debug assertion left in test; missing error variant |

**Analysis:** The bug is that the flattening pass was not processing finalize blocks, fixed by adding Statement/StatementReconstructor imports. Sonnet diff-only was the best performer (det=2), correctly noting the imports were added without corresponding function changes visible in the diff. Opus diff-only flagged the same line but misinterpreted it. The bulk of the ground truth is test expectation file changes, which no tool meaningfully analyzed. This is a compiler-internals bug where the diff is almost entirely test output changes with one small import fix -- the signal-to-noise ratio is extremely low. Tools with repo context paradoxically performed worse, possibly because they got lost exploring the large test output changes.

---

### leo-202: [Fix] Remove deprecation warning for `leo build`.
**Ground truth:** `leo/cli/cli.rs:21,118-133`
**Blame confidence:** A

| Tool | Caught | Det | Qual | Finding summary |
|------|--------|-----|------|-----------------|
| S-diff | No | 0 | 2 | Bare `exit` in run.sh scripts (novel finding, wrong file) |
| S-repo | No | 0 | 2 | Same bare `exit` issue across example run.sh scripts |
| S-v2 | No | 0 | 0 | Same bare `exit` issue (10 FPs) |
| O-diff | No | 0 | 2 | Premature success message in build.rs; bare `exit` |
| O-repo | No | 0 | 2 | Package::open validation order change in build.rs |
| CR | No | 0 | 0 | Rate-limited (FP=2) |
| Copilot | Yes* | 1 | 2 | Touched cli.rs deprecation block but flagged wrong issue |
| Greptile | Yes* | 1 | 2 | Touched cli.rs:133 deprecation block but suggested simplification not removal |

**Analysis:** The bug is a deprecation warning for `leo build` that should be removed entirely. Copilot and Greptile both commented on the exact buggy lines but misdiagnosed: Copilot flagged private keys in .env files, while Greptile suggested simplifying the tracing span rather than removing the deprecation. Agent runs were entirely distracted by the bare `exit` pattern in example shell scripts (a real but unrelated issue). The S-v2 run produced 10 FPs all about bare exit, demonstrating how a repeating pattern across files can dominate the review. This is another case where understanding the intent (deprecation should be removed, not simplified) requires context that tools lack.

---

## Cross-cutting observations

**CodeRabbit was rate-limited on 15 of 16 cases** (all except leo-164 where it got through and leo-168 where it refused due to file count). This makes CodeRabbit data essentially unusable for this group.

**Scorer `caught` field is unreliable.** Multiple cases show `caught=True` with `det=0` (leo-116, leo-136) where tools commented in the right file but on unrelated issues. Conversely, leo-159's Sonnet diff+repo scored `caught=No` with `det=3`. The `caught` boolean and `detection_score` should be better aligned.

**S-v2 (Sonnet repo v2) has missing reasoning** across all cases (empty reasoning field), and many qual=0 scores despite finding bugs. This run appears to have a systematic scorer issue.

**Novel findings were pervasive.** In 8 of 16 cases (leo-116, leo-117, leo-136, leo-159, leo-168, leo-181, leo-193, leo-202), tools found legitimate bugs that were not in the ground truth. The &&/|| validation logic bug in leo-116 was found by every tool. The 'covnert' typos in leo-168 were caught by multiple agents.

**Difficulty vs. tool performance:**
- Easy cases (leo-104, leo-117): Tools caught leo-104 easily but universally missed leo-117 (help message text).
- Medium cases (leo-115, leo-157, leo-159, leo-162, leo-202): Mixed results. Path/naming bugs (leo-115) were catchable; config bugs (leo-157) were not.
- Hard cases (leo-116, leo-119, leo-136, leo-139, leo-164, leo-168, leo-181, leo-193, leo-200): Mostly missed. Compiler internals (leo-119, leo-139, leo-181) and security bugs (leo-136) were hardest.

**Repo context did not consistently help.** In several cases (leo-104, leo-200), diff-only outperformed diff+repo. The additional context may cause tools to explore tangential code paths rather than focusing on the changes.
