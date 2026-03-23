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
