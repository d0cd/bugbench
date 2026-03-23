# Pilot Report: Leo Dataset — 2026-03-22 (Updated)

## Executive Summary

First cross-tool comparison of AI code review tools on 58 curated bug cases from
ProvableHQ/leo. GitHub Copilot leads at 60% mechanical catch rate, followed by
Greptile at 45%. The in-house Claude Agent SDK achieved 38% in diff-only mode.
CodeRabbit scored 3.4% mechanical catch but posts high-level summaries rather
than line-specific findings — LLM judge scoring is needed for a fair comparison.

## Dataset

**Source:** ProvableHQ/leo (Rust compiler for zero-knowledge circuits)

| Metric | Value |
|--------|-------|
| PRs fetched | 500 |
| Cases mined | 232 |
| After curation | **58 active** |
| Excluded | 174 |

**Exclusion breakdown:**
| Reason | Count |
|--------|-------|
| Dependency bumps (Dependabot, "Bump ...") | 93 |
| Duplicate introducing PR | 25 |
| No buggy lines (blame failed / no overlap) | 20 |
| All-test-expectation (ground truth in .out files only) | 11 |
| CI/release script fixes | 9 |
| Self-referential (introducing == fix PR) | 7 |
| Feature PRs mislabeled as fix | 6 |
| Doc-only fixes | 3 |

**Active case quality:**
- All 58 have source-code buggy lines, valid introducing commits, bug descriptions
- Blame tiers: 29 A (high), 15 B, 14 C
- Median buggy lines: 14
- Categories: 43 other, 7 type, 5 logic, 2 runtime, 1 memory
- All language=rust

## Tool Evaluation Results

### Full Leaderboard (LLM Judge: claude-opus-4-6 via SDK)

All 7 configurations scored by Opus LLM judge on detection (0-3) and quality (0-4).

| Rank | Tool | Model | Context | Catch | Detection | Quality | Judge $ |
|------|------|-------|---------|-------|-----------|---------|---------|
| 1 | **Copilot** | — | PR | **60.3%** | **0.88** | 1.84 | $3.28 |
| 2 | **Greptile** | — | PR | 44.8% | 0.47 | 1.93 | $3.30 |
| 3 | **Agent SDK** | Sonnet 4.6 | diff-only | 37.9% | 0.60 | **2.09** | $3.41 |
| 4 | **Agent SDK** | Opus 4.6 | diff-only | 34.5% | 0.66 | 1.71 | $3.18 |
| 5 | **Agent SDK** | Opus 4.6 | diff+repo 60t | 27.6% | 0.50 | 1.67 | $2.62 |
| 6 | **Agent SDK** | Sonnet 4.6 | diff+repo | 24.1% | 0.45 | 1.76 | $3.30 |
| 7 | **CodeRabbit** | — | PR | 3.4% | 0.09 | 0.16 | $3.50 |

### Statistical Significance (permutation test, BH-corrected)

| Comparison | p-value | Significant? |
|-----------|---------|-------------|
| Copilot vs CodeRabbit | 0.0000 | Yes |
| Greptile vs CodeRabbit | 0.0000 | Yes |
| Copilot vs Greptile | 0.1367 | No |

### Key Findings

1. **Copilot is the strongest tool** — 60% catch rate with the highest detection
   score (0.88). It reviews automatically on PR open with precise inline comments.

2. **Greptile is competitive** — 45% catch rate, not statistically different from
   Copilot (p=0.14). Posts detailed summaries with file:line references. Requires
   `@greptile` trigger comment and manual dashboard activation per repo. $1/review
   beyond 50 free tier.

3. **Sonnet diff-only has the best review quality** (2.09) — the agent writes
   the most thorough, well-reasoned reviews when focused on the diff. Higher
   quality than Copilot (1.84) despite lower catch rate.

4. **Repo access consistently hurts agent performance** — Both Sonnet and Opus
   perform worse with repo access (24% and 28%) than diff-only (38% and 34%).
   Even with 60 turns (2x budget), Opus diff+repo (28%) doesn't beat Sonnet
   diff-only (38%). The exploration dilutes focus instead of adding context.

5. **Opus does not beat Sonnet** — Opus diff-only (34.5%) slightly underperforms
   Sonnet diff-only (37.9%). Higher detection score (0.66 vs 0.60) but lower
   quality (1.71 vs 2.09). The model upgrade alone doesn't close the gap
   with Copilot.

6. **CodeRabbit is ineffective** even with LLM judge — 3.4% catch, 0.16 quality.
   Its walkthrough summaries don't identify specific bugs.

7. **Precision is low across all tools** — 17-26% precision means 74-83% of
   comments are false positives or low-value. This is expected for automated
   code review — tools flag many stylistic/potential issues alongside real bugs.

### By Difficulty

| Difficulty | Copilot | Greptile | Agent SDK (diff-only) |
|-----------|---------|----------|----------------------|
| Easy | — | — | 25% |
| Medium | — | — | 44% |
| Hard | — | — | 38% |

### By PR Size

| Size | Overall Catch Rate |
|------|-------------------|
| XL (>500 lines) | 75% |
| Small (10-50) | 46% |
| Medium (50-200) | 29% |
| Tiny (<10) | 17% |

Larger PRs have higher catch rates — more code to review means more chances to
find the buggy lines, but also the bugs may be more obvious in large refactors.

## Pipeline Improvements Made During Pilot

### Dataset Construction
- **Content matching fallback** in ground truth: when line numbers drift beyond
  ±3 tolerance, matches by exact line text content. Recovered 28 cases.
- **Basename file matching**: handles directory renames (e.g., `leo-fmt/` → `crates/fmt/`).
  Recovered 5 cases.
- **Non-source file filtering**: Cargo.lock, CI configs, lockfiles excluded from
  buggy lines.
- **Improved curation**: 8 exclusion rules (was 4) — catches dependency bumps,
  CI fixes, doc fixes, features, test-expectation-only, self-referential,
  corrupted data.
- **Category classifier fixed**: Word-boundary regex replaced substring matching
  that was matching "lock" inside "blockquote" → 101 false "concurrency" labels
  eliminated.
- **Severity "low" added**: Text-based detection for typo/cosmetic/style fixes.

### PR Tool Infrastructure
- **Two-phase PR evaluation**: `open-prs` (create PRs, return immediately) +
  `scrape-prs` (check for reviews, scrape, close). Decouples PR creation from
  review timing.
- **Issue comments endpoint**: CodeRabbit and Greptile post as issue comments,
  not PR review comments. Both endpoints now checked.
- **Re-trigger on scrape**: If no review found during scrape, automatically
  re-posts `@greptile` or `@coderabbitai review` trigger.
- **Preflight validation**: Cases exist, repo valid, GitHub auth works, sample
  SHAs reachable — all checked before touching GitHub.
- **Result protection**: `open-prs` never overwrites `pending-review` results.
  Error results auto-retried on re-run.
- **Retry with backoff**: All `gh` CLI calls retry 3× on transient errors
  (timeouts, 500s, rate limits).
- **Clone management**: Auto-fetch, auto-reset working tree, incomplete clone
  cleanup.
- **Branch cleanup**: Orphaned remote branches deleted on failure. `cleanup-prs`
  command for manual cleanup.
- **Email privacy fix**: All commits use generic `bugeval@users.noreply.github.com`
  identity to avoid GH007 push rejections.
- **Binary patch fallback**: Cherry-pick when `git apply` fails on binary files.

### Codebase Quality
- **773 tests** (up from 633 at session start), ruff clean, pyright clean
- All new features have tests
- Docs updated: pilot-plan.md, runbook.md, CLAUDE.md, todo.md

## Costs

| Item | Cost |
|------|------|
| Agent SDK diff-only (58 cases) | ~$49 |
| Agent SDK diff+repo (58 cases) | ~$56 |
| PR tools (Copilot) | $0 (free) |
| PR tools (Greptile) | ~$8 (58 reviews, $1 each beyond 50 free) |
| PR tools (CodeRabbit) | $0 (free) |
| **Total pilot** | **~$113** |

## Next Steps

1. **LLM Judge scoring** (Step 4) — needs ANTHROPIC_API_KEY. Will give
   detection_score (0-3), review_quality (0-4), and comment verdicts (TP/FP).
   Critical for fair CodeRabbit evaluation.
2. **Clean cases** (Step 5) — generate 10 non-bug cases for false alarm rate.
3. **Multi-repo** (Step 7) — mine snarkOS and sdk repos.
4. **Full model comparison** (Step 8) — Anthropic vs Google vs OpenAI API runners.
5. **Golden set curation** — use dashboard to confirm/dispute cases.

## Appendix: Tool Response Characteristics

**Copilot** (`copilot-pull-request-reviewer`):
- Responds automatically on PR open
- Posts inline PR review comments with file + line
- Response time: 3-6 minutes
- No manual trigger needed

**Greptile** (`greptile[bot]`):
- Requires `@greptile` trigger comment on non-default branches
- Requires manual dashboard activation per repo
- Posts as issue comment with HTML summary including file:line references
- Response time: 5-15 minutes
- $1/review beyond 50 free

**CodeRabbit** (`coderabbitai[bot]`):
- Requires `@coderabbitai review` trigger on non-default branches
- Posts as issue comments (walkthrough summaries) + some inline review comments
- Response time: 5-30 minutes (highly variable, possible queuing)
- Free tier sufficient

**Agent SDK** (Claude Code):
- Direct API/SDK call, no GitHub integration
- Multi-turn conversation with file tools (Read, Glob, Grep, WebSearch)
- Response time: 2-8 minutes per case
- Cost: ~$0.85/case (diff-only), ~$0.97/case (diff+repo)
