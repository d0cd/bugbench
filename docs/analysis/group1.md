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
