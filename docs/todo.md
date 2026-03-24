# TODO

## Open Items (2026-03-23)

See [audit.md](audit.md) for the full codebase audit and [plans/2026-03-23-audit-remediation.md](plans/2026-03-23-audit-remediation.md) for the detailed remediation plan.

### Tech Debt
- [x] Refactor `agent_runner.py` (2,590→788 lines) into 6 sub-modules per provider
- [x] Extract shared PR runner utilities from `copilot_runner.py` into `pr_utils.py`
- [x] Move hardcoded constants (MODEL, MAX_TOKENS, COST_CEILING_USD) to env var overrides
- [x] Replace broad `except Exception` catches with specific types
- [x] Replace `SystemExit(1)` with `click.ClickException` in cli.py
- [x] Add CI workflow (.github/workflows/ci.yml)

### Methodology
- [x] Separate TP-novel from TP in precision/SNR metrics
- [x] Add sensitivity analysis for mechanical tolerance parameter
- [x] Lower contamination threshold from 0.5 to 0.3
- [x] Report contaminated results separately in analysis
- [x] Apply FDR correction to sliced analysis p-values
- [x] Fix `compute_catch_rate()` to use `detection_score >= 2` instead of mechanical `caught` field
- [ ] Filter clean cases from slice analysis and permutation tests (blocked — no clean cases exist yet)

### Dataset
- [ ] Generate clean (non-bug) control cases for false alarm rate
- [ ] Mine cases from snarkVM for repository diversity
- [x] Improve bug category classification (added Leo-specific patterns)
- [x] Populate quality_flags on all active cases (enhanced with 5 flag types)
- [ ] Review ~15% questionable ground truth cases (leo-284 README, leo-253 Cargo.toml, leo-041 test-heavy)
- [ ] Recover ~90 real bugs with weak ground truth via LLM-based ground truth computation
- [x] Filter non-source files (Cargo.toml, README, test files) from buggy_lines

### Testing
- [x] Add integration test for evaluate -> score -> analyze pipeline
- [x] Add checkpoint resume test
- [x] Increase coverage on score.py, evaluate.py, analyze.py (82% overall, 963 tests)

### Bug Fixes
- [x] Fix Docker diff-only workspace bug (mounts /dev/null when workspace=None)
- [x] Silent Docker failure detection (empty stdout now recorded as error)

### Dataset Curation
- [ ] **Run LLM curation on snarkVM (163 active) and snarkOS (115 active)** — heuristic filters bring dataset to ~55-60% legit. LLM gate (`bugbench curate --llm`) needed to catch: feature additions without `[Feature]` prefix, reverts disguised as fixes, refactors, test-only changes with ambiguous titles. Estimated to bring quality to ~85%+ (matching Leo after manual review).
- [ ] **Manual dashboard review** — confirm/dispute cases in golden set after LLM curation. Priority: snarkVM-002 (security), snarkVM-442 (name→resource), snarkOS-037 (HackerOne).

### Ground Truth (see [ground-truth-improvements.md](ground-truth-improvements.md))
- [ ] **P1: LLM-augmented ground truth** — recover ~150 of 199 no-buggy-lines cases, refine 89 diffuse cases. LLM reads both diffs and produces structured `BugDescription(file, line_range, what, why)`. Est. $35-175.
- [ ] **P2: Hierarchical ground truth model** — separate primary bug location from symptoms (test expectations) and secondary effects (callers). Enables tiered scoring.
- [ ] **P3: Multi-signal fusion** — combine line intersection + LLM + fix PR review comments + regression tests + issue descriptions. Locations confirmed by 2+ signals get high confidence.
- [ ] **P4: Test-based ground truth** — cherry-pick regression tests from fix onto introducing commit; test failure = precise ground truth. Highest quality, most expensive.

### Dashboard Performance
- [ ] **Case loading is slow** — `load_cases()` reads every YAML on each request. Add in-memory cache with file-mtime invalidation.
- [ ] **Large transcript rendering** — v3 transcripts with 300+ messages are huge JSON blobs. Paginate or lazy-load phases.
- [ ] **Score computation on-the-fly** — precompute aggregate stats (catch rate, SNR) and store in a summary file instead of recalculating per page load.
- [ ] **Static asset caching** — templates re-render on every request in debug mode. Add ETag/cache headers for production.

### Remaining (new features + external infrastructure)
- [ ] Human judge calibration interface (see audit.md §10)
- [ ] SWE-bench style patch generation evaluation (see future-work.md)
- [ ] Generate clean (non-bug) control cases (needs live repo runs)
- [x] Mine cases from snarkVM and snarkOS (482 + 434 = 916 cases mined)
- [ ] Review ~15% questionable ground truth cases (needs human review)
- [ ] Recover ~150+ bugs with weak ground truth (needs LLM-augmented ground truth — see above)
- [ ] Prompt caching for agent runners (30% cost savings)
- [ ] Batch API for judge scoring (50% cost savings)
- [ ] Early termination — stop agent when structured findings are produced
- [ ] Ensemble overlap analysis — measure Copilot + Opus union catch rate
- [ ] Model cascading for judge (Haiku first, Opus for borderline scores)
- [ ] **v4 agent**: diff-first scan (no tools) then selective exploration (combines diff-only focus with diff+repo context)
- [ ] **Parallel LLM scoring** — current scorer is sequential; add ThreadPoolExecutor for SDK backend
- [ ] **Score diff-only runs for snarkVM/snarkOS** — 292 active cases ready for evaluation
