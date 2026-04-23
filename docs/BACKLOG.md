# Backlog (out of scope)

> **Status:** The project is in maintenance mode as of 2026-04-23. The items below are **not planned for implementation**. They are preserved as a record of what was considered but deprioritized.
>
> Bug fixes and dependency updates are still accepted; new features and methodology research are not.

Original list compiled 2026-03-23 during the handoff audit. Completed items have been removed; only deferred work remains.

## Methodology

- Filter clean cases from slice analysis and permutation tests (blocked — no clean cases exist yet)

## Dataset

- Generate clean (non-bug) control cases for false alarm rate
- Mine cases from snarkVM for repository diversity
- Review ~15% questionable ground truth cases (leo-284 README, leo-253 Cargo.toml, leo-041 test-heavy)
- Recover ~90 real bugs with weak ground truth via LLM-based ground truth computation

## Dataset Curation

- **Run LLM curation on snarkVM (163 active) and snarkOS (115 active)** — heuristic filters bring dataset to ~55-60% legit. LLM gate (`bugbench curate --llm`) needed to catch: feature additions without `[Feature]` prefix, reverts disguised as fixes, refactors, test-only changes with ambiguous titles. Estimated to bring quality to ~85%+ (matching Leo after manual review).
- **Manual dashboard review** — confirm/dispute cases in golden set after LLM curation. Priority: snarkVM-002 (security), snarkVM-442 (name→resource), snarkOS-037 (HackerOne).

## Ground Truth (see [ground-truth-improvements.md](ground-truth-improvements.md))

- **P1: LLM-augmented ground truth** — recover ~150 of 199 no-buggy-lines cases, refine 89 diffuse cases. LLM reads both diffs and produces structured `BugDescription(file, line_range, what, why)`. Est. $35-175.
- **P2: Hierarchical ground truth model** — separate primary bug location from symptoms (test expectations) and secondary effects (callers). Enables tiered scoring.
- **P3: Multi-signal fusion** — combine line intersection + LLM + fix PR review comments + regression tests + issue descriptions. Locations confirmed by 2+ signals get high confidence.
- **P4: Test-based ground truth** — cherry-pick regression tests from fix onto introducing commit; test failure = precise ground truth. Highest quality, most expensive.

## Dashboard Performance

- **Case loading is slow** — `load_cases()` reads every YAML on each request. Add in-memory cache with file-mtime invalidation.
- **Large transcript rendering** — v3 transcripts with 300+ messages are huge JSON blobs. Paginate or lazy-load phases.
- **Score computation on-the-fly** — precompute aggregate stats (catch rate, SNR) and store in a summary file instead of recalculating per page load.
- **Static asset caching** — templates re-render on every request in debug mode. Add ETag/cache headers for production.

## New features and external infrastructure

- Human judge calibration interface (see [audit-2026-03-23.md](audit-2026-03-23.md) §10)
- SWE-bench style patch generation evaluation (see [future-work.md](future-work.md))
- Generate clean (non-bug) control cases (needs live repo runs)
- Review ~15% questionable ground truth cases (needs human review)
- Recover ~150+ bugs with weak ground truth (needs LLM-augmented ground truth — see above)
- Prompt caching for agent runners (30% cost savings)
- Batch API for judge scoring (50% cost savings)
- Early termination — stop agent when structured findings are produced
- Ensemble overlap analysis — measure Copilot + Opus union catch rate
- Model cascading for judge (Haiku first, Opus for borderline scores)
- **v4 agent**: diff-first scan (no tools) then selective exploration (combines diff-only focus with diff+repo context)
- **Parallel LLM scoring** — current scorer is sequential; add ThreadPoolExecutor for SDK backend
- **Score diff-only runs for snarkVM/snarkOS** — 292 active cases ready for evaluation
