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
