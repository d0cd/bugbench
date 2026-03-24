# Architectural Decisions

## 1. Exploration vs Analysis Problem

Why diff-only mode (37.9% catch rate) consistently outperforms diff+repo (24.1%):
- Agents with repository access enter exploration loops: read diff → grep callers → read files → grep more callers
- This consumes turns before synthesis can occur
- Opus with doubled turn budget (60 turns → 27.6%) fails to beat Sonnet with standard budget (30 turns → 37.9%)
- Docker runs with Bash/rg tools achieved only 21%, with 41% hitting turn limits producing zero output
- The problem is architectural (time management), not capability

## 2. Two-Pass Architecture

Designed to solve the exploration vs analysis problem:
- **Pass 1 (Explorer):** 30 turns, full tool access (Read, Glob, Grep, WebSearch), gathers structured context about the codebase around the diff
- **Pass 2 (Reviewer):** 15 bounded turns, receives explorer notes + diff, synthesizes findings without exploration pressure
- This prevents output truncation by giving the reviewer a bounded analytical task
- Implemented as `agent-sdk-2pass` tool variant

## 3. Three-Phase V3 Runner

Extension of the two-pass concept:
- **Phase 1 (Survey):** Quick scan of the diff to identify areas of concern
- **Phase 2 (Investigate):** Deep exploration with full repo access, guided by survey findings
- **Phase 3 (Report):** Final JSON findings synthesis with strict output format
- Implemented as `agent-sdk-v3` tool variant

## 4. Mechanical vs LLM-Judged Scoring

The most important methodological finding: mechanical catch rates are systematically inflated.

| Tool | Mechanical (±10 lines) | LLM-Judged (Det≥2) |
|------|----------------------|---------------------|
| Copilot | 60% | 29% |
| Opus diff-only | 34% | 22% |
| Sonnet diff-only | 38% | 21% |
| Greptile | 45% | 14% |
| CodeRabbit | 3% | 3% |

**Why the discrepancy:**
- Mechanical scorer awards "caught" when any tool comment falls within ±10 lines of a buggy line
- Copilot posts 8-10 comments per PR, increasing random hit probability by file-level coincidence
- Det≥2 requires the LLM judge to confirm the tool actually identified and described the bug
- CodeRabbit shows no discrepancy because it rarely comments (both metrics agree at ~3%)

**Implication:** Use Det≥2 as the primary metric for all future evaluations.

## 5. PR Tool Integration Lessons

Eight operational patterns emerged from real failures during the pilot:

1. **Two-phase approach:** open-prs → scrape-prs. Single-phase blocks on slow tools and closes PRs before reviews arrive.
2. **Issue comment endpoints:** CodeRabbit posts as issue comments (not PR review comments). Must check both `/pulls/{n}/comments` and `/issues/{n}/comments`.
3. **Explicit triggers:** Greptile requires `@greptile` mention on non-default branches. `scrape-prs` re-triggers pending cases.
4. **Per-tool clone management:** Stale clones miss commits. Auto-fetch + reset before each use.
5. **Git identity handling:** Use `--reset-author` with generic bugeval identity to avoid GH007 rejection on private emails.
6. **Binary patch fallback:** `git apply` fails on binary diffs. Cherry-pick as fallback.
7. **Immutable result tracking:** Never delete `pr_state=pending-review` files (they track open PRs). Only error results can be deleted.
8. **Concurrency model:** 1 concurrent operation per tool for PR creation (git operations). Tools run in parallel since each has its own clone.

## 6. SDK Cancel Scope Bug

**Bug:** `claude_agent_sdk` async runtime raises `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in` when exiting the explorer pass in two-pass mode.
**Impact:** Crashes explorer pass → $0.00 cost, empty output.
**Workaround:** Use `--docker` mode, which runs `claude -p` as a clean subprocess instead of the SDK's async `query()`.
**Status:** Upstream bug in claude_agent_sdk; Docker mode is the production workaround.

## 7. Cost Structure

- 92% of evaluation cost comes from agent API calls
- 8% comes from judge scoring (LLM judge at $0.03-0.19/case, avg $0.08)
- Prompt caching could reduce agent costs by ~30%
- Batch API could halve judge scoring costs
