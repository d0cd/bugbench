# Codebase Audit — 2026-03-23

Comprehensive audit for handoff readiness. Covers code quality, documentation, test coverage, presentation accuracy, and tech debt.

---

## 1. Automated Check Results

| Check | Status | Details |
|-------|--------|---------|
| **ruff** | PASS | Clean — no lint issues |
| **pyright** | FAIL | 27 errors (see §2) |
| **pytest** | FAIL | 879 passed, 1 failed (see §3) |
| **Coverage** | 81% overall | Many large files at 0% (see §4) |

---

## 2. Pyright Errors (27 total)

### `agent_runner.py` — 25 errors

**Possibly unbound variables (6 errors, lines 2311–2318):**
Variables `explorer_text`, `explorer_cost`, `explorer_msgs`, `reviewer_text`, `reviewer_cost`, `reviewer_msgs` may be unbound if the try block fails. Need default values before the try block.

**Anthropic SDK type mismatches (14 errors, lines 2175–2486):**
Accessing `.text` on `ThinkingBlock`/`ToolUseBlock`/`ToolResultBlock` and `.name` on `TextBlock`/`ThinkingBlock`/`ToolResultBlock`. These blocks don't have those attributes — need `isinstance` guards or `hasattr` checks. Appears in three separate code blocks (two-pass review and SDK runner).

**`subprocess.os` access (1 error, line 2102):**
`subprocess.os` is not a valid attribute — should be `import os` separately.

### `_docker_runner.py` — 1 error

**Line 58:** `.content` accessed on `object` type — needs type narrowing.

### `llm.py` — 1 error

**Line 165:** `import "openai"` could not be resolved — `openai` is an optional dependency. The import should be guarded or the module should handle `ImportError`.

---

## 3. Test Failure

### `test_filters_to_coderabbit` (`tests/test_coderabbit_runner.py:138`)

**Root cause:** `scrape_pr_comments()` now makes 2 API calls (PR review comments via `/pulls/{n}/comments` + issue comments via `/issues/{n}/comments`). The test mocks `run_gh` with a single return value, so both calls return the same data, doubling results (4 instead of expected 2).

**Fix:** Use `side_effect` on the mock to return different data for each call, or mock the specific API path.

---

## 4. Test Coverage Gaps

### Files at 0% coverage (no tests execute their code):
| File | Lines | Purpose |
|------|-------|---------|
| `analyze.py` | 277 | Statistical analysis and charts |
| `blame.py` | 299 | Git blame for introducing commits |
| `clean_cases.py` | 92 | Generate non-bug control cases |
| `cli.py` | 355 | Click CLI entry point |
| `curate.py` | 204 | LLM-powered case curation |
| `dashboard.py` | 419 | Flask web UI |
| `dashboard_models.py` | 119 | Dashboard data models |
| `evaluate.py` | 334 | Evaluation orchestrator |
| `ground_truth.py` | 414 | Buggy line computation |
| `greptile_runner.py` | 71 | Greptile PR tool runner |
| `llm.py` | 77 | Multi-provider LLM abstraction |
| `score.py` | 287 | Mechanical + LLM judge scoring |

### Files with minimal coverage:
| File | Coverage | Notes |
|------|----------|-------|
| `agent_runner.py` | 10% (935 lines, 842 missed) | Only model/config code tested |
| `copilot_runner.py` | 19% (282 lines, 228 missed) | Only comment scraping tested |
| `coderabbit_runner.py` | 28% (71 lines, 51 missed) | Partial scraping tests |
| `mine.py` | 10% (651 lines, 583 missed) | Only basic mining logic tested |

---

## 5. Code Quality Issues

### 5a. Giant file: `agent_runner.py` (2,590 lines)

Largest file by 2x. Contains:
- Anthropic API runner (~300 lines)
- Google Gemini runner (~200 lines)
- OpenAI runner (~200 lines)
- Claude CLI runner (~200 lines)
- Gemini CLI runner (~100 lines)
- Codex CLI runner (~100 lines)
- Claude Agent SDK runner (~300 lines)
- Two-pass review architecture (~300 lines)
- Docker wrapper integration (~100 lines)
- Tool execution engine (~200 lines)
- Shared utilities (~200 lines)
- Transcript saving (~100 lines)
- Cost tracking (~100 lines)

**Recommendation:** Split into:
- `agent_runner.py` — orchestrator + shared utilities + tool execution
- `_anthropic_runner.py` — Anthropic API multi-turn loop
- `_gemini_runner.py` — Google Gemini runner
- `_openai_runner.py` — OpenAI runner
- `_cli_runners.py` — CLI subprocess runners (claude, gemini, codex)
- `_sdk_runner.py` — Claude Agent SDK runner
- `_two_pass.py` — Two-pass review architecture

### 5b. `type: ignore` comments (28 instances)

Concentrated in:
- `agent_runner.py` (21) — SDK type compatibility
- `llm.py` (5) — SDK type compatibility
- `copilot_runner.py` (1) — `int()` conversion
- `mine.py` (1) — `raise` in except

Most are legitimate SDK typing gaps, but the volume indicates the runner code is fighting the type system.

### 5c. Broad exception handling

`copilot_runner.py:400,423` silently catches `GhError` and `json.JSONDecodeError` with `pass` — errors are swallowed without logging.

### 5d. Cross-module coupling

`greptile_runner.py` and `coderabbit_runner.py` both import 8+ functions from `copilot_runner.py`. These shared PR utilities should be in a dedicated `pr_runner_utils.py` module.

### 5e. Late import in `cli.py`

Line 749: `from bugeval.curate import curate  # noqa: E402` — late import to avoid circular dependency. Consider restructuring.

### 5f. Broad exception handling (14+ locations)

Broad `except Exception` catches found in:
- `agent_runner.py` (5 locations) — API/CLI/SDK runners swallow all errors
- `cli.py` (9 locations) — inconsistent: some exit, some log-and-continue
- `evaluate.py` — silently skips failed cases without tracking which failed
- `add_case.py`, `blame.py` — silent `except Exception: pass`
- `greptile_runner.py`, `coderabbit_runner.py` — silent swallows after scraping

### 5g. Code duplication in agent runners

Three nearly-identical `_make_result()` functions (lines 764, 958, 1174) — only differ in `tool=` parameter. Should be a single parameterized function.

### 5h. Hardcoded constants (15+ locations)

Module-level constants in `agent_runner.py` that should be in config.yaml:
- `MODEL = "claude-sonnet-4-6"` (line 25)
- `MAX_TOKENS = 4096` (line 26)
- `COST_CEILING_USD = 2.0` (line 27)
- `API_TIMEOUT_SECONDS = 120.0` (line 28)
- Per-token pricing rates for Google/OpenAI (lines 952-953, 1168-1169)
- Docker image names vary across functions in evaluate.py

### 5i. Unused import

`agent_runner.py` line 14: `from dataclasses import field as dataclass_field` — never used.

### 5j. Undocumented tool variants

`agent-sdk-2pass` and `agent-sdk-v3` tool variants exist in evaluate.py but are not documented in README, experiment-design.md, or runbook.md.

---

## 6. Documentation Issues

### 6a. CLI entry point inconsistency — FIXED

- `pyproject.toml` defines entry point as **`bugbench`**
- `README.md` — updated to use `bugbench` (was `bugeval`)
- `CLAUDE.md` correctly uses **`bugbench`**

### 6b. Missing `.env.example`

CLAUDE.md references `.env` + `python-dotenv` but:
- No `.env.example` file exists
- `python-dotenv` is not in `pyproject.toml` dependencies
- Environment variables needed are undocumented

Required env vars (found by grepping):
- `ANTHROPIC_API_KEY`
- `GITHUB_TOKEN` (for `gh` CLI)
- `GREPTILE_API_KEY` / `GREPTILE_API_TOKEN`
- `OPENAI_API_KEY` (optional)
- `GOOGLE_API_KEY` (optional)
- `EVAL_ORG` (GitHub org for fork management)

### 6c. README is not handoff-ready

Current README is 38 lines — bare minimum. Missing:
- Project motivation and context
- Architecture overview
- Setup prerequisites (gh CLI, Docker, API keys)
- Detailed CLI command descriptions
- Results interpretation guide
- Links to analysis documents
- Contributing guidelines

### 6d. `docs/todo.md` is stale

Says "No blocking issues. All pre-scaling items resolved." but the audit-remediation plan (`docs/plans/2026-03-23-audit-remediation.md`) has 19 actionable tasks across 3 workstreams.

### 6e. Missing CI pipeline

`.github/workflows/` contains only a `CLAUDE.md` file — no actual CI workflow. CLAUDE.md references `.github/workflows/ci.yml` in the MEMORY.md but it doesn't exist.

---

## 7. Presentation Audit (`docs/presentation.html`)

### 7a. CRITICAL: Dataset version mismatch

The presentation uses **67 cases** (from v3 run, Mar 23) while the pilot report uses **58 cases** (Mar 22). This is never explicitly stated. All percentages differ between the two documents because they reference different evaluation runs:

| Metric | Presentation (67 cases, Det>=2) | Pilot Report (58 cases, mechanical) |
|--------|-------------------------------|-------------------------------------|
| Copilot catch rate | 23% | 60.3% |
| Greptile catch rate | 11% | 44.8% |
| Opus diff-only catch | 31% | 34.5% |
| Total pilot cost | $509 | $113 |

**Root cause:** The presentation uses **LLM-judged Det>=2** scoring while the pilot report uses **mechanical catch rate** (line proximity). These are fundamentally different metrics. The Det>=2 metric is stricter — it requires the LLM judge to confirm the tool actually identified the bug, not just commented near buggy lines.

**Fix needed:** Add a clarifying note on the title slide or slide 5: "Results from 67-case v3 evaluation (Opus-judged, Det>=2 metric, completed Mar 23) — see pilot report for 58-case mechanical scoring baseline."

### 7b. Secondary presentation issues

- **Slide 9:** Claims Opus has "half" the false positives of Copilot (22 vs 32). 22/32 = 69%, not 50%. Should say "about two-thirds."
- **Slide 3:** Lists Greptile's key strength as "82% catch rate" (their marketing claim). Pilot found 11-45% depending on metric. Could mislead readers.
- **Slide 10:** References "3-phase v3 runner" without prior introduction.
- **Slide 11:** Ensemble detection "44%" is not documented in pilot report or analysis.md — needs source citation.
- **Slide 5:** Exclusion arithmetic is internally consistent (500→311→146→67) but doesn't match pilot report (500→232→58).
- **Tool naming:** Minor inconsistency — "GitHub Copilot" (slide 3) vs "Copilot" (elsewhere); "Claude Agent SDK" (slide 4) vs "Agent SDK" (elsewhere). Acceptable for presentation.
- **No spelling errors** found. No broken HTML/CSS. Logical flow is sound.

### 7c. Metrics terminology inconsistency

The presentation uses "Det>=2" and "Catch rate" interchangeably across slides, but these are different metrics:
- **Mechanical catch rate** = tool commented within 10 lines of buggy line
- **Det>=2** = LLM judge confirmed tool identified the bug (stricter)

Should standardize terminology throughout and define on first use.

---

## 8. Analysis Preservation

### Committed analysis (safe):
- `docs/analysis.md` — detailed case-by-case analysis (1,255 lines)
- `docs/analysis/group0-3.md` — grouped analysis files
- `docs/pilot-report-2026-03-22.md` — pilot results
- `docs/future-work.md` — SWE-bench extension proposal

### At-risk analysis (in memory/gitignored):

**In `results/` (gitignored):**
- 13 run directories with raw YAML results, transcripts, and score files
- `experiments.yaml` — only index of all runs (metadata, status, notes)
- Judge cost data per case ($0.03-$0.19, avg $0.08) — in score YAMLs only
- Comparison CSVs with metrics (catch_rate, quality, precision, cost_per_bug)
- No matplotlib charts ever generated (code exists in analyze.py but never run)

**In Claude memory files (session-ephemeral):**
- `project_pilot_status.md` — Real Det>=2 catch rates (Copilot 29%, Opus 22%, Sonnet 21%)
- `feedback_pr_tools.md` — 9 operational lessons from PR tool integration
- `feedback_agent_review.md` — Root cause analysis of exploration vs analysis problem, two-pass architecture design, SDK cancel scope bug workaround
- `session_2026_03_22_curation_scoring.md` — Dataset evolution (232→311→67 cases), 192 novel TPs, SNR 0.75 LLM-judged vs 0.14 mechanical

### Recommendations:

1. **Before cleanup:** Extract comparison CSVs and score YAMLs from `results/` to preserve cost data
2. **Create `docs/architectural-decisions.md`** from memory files — document two-pass design, exploration vs analysis problem, SDK cancel scope workaround
3. **Add Det>=2 vs mechanical comparison table** to pilot report — the 60%→29% Copilot discrepancy is the most important methodological finding
4. **Document the 192 novel TPs** found across all tools — shows tools provide value beyond catching known bugs
5. **Generate charts** by running `uv run bugbench analyze --run-dir results/run-v3-opus` before cleanup

---

## 9. Handoff TODOs

### Critical (must-fix before handoff):
1. ~~Fix the 1 failing test~~ — DONE (mock `side_effect` for dual API call)
2. ~~Fix pyright errors (27 → 0)~~ — DONE (unbound vars, getattr pattern, import os)
3. ~~Update README to professional quality~~ — DONE (architecture, CLI ref, scoring, setup)
4. ~~Create `.env.example`~~ — DONE
5. ~~Fix CLI entry point inconsistency~~ — DONE (README now uses `bugbench`)
6. **Add dataset version disclaimer to presentation** — the 67-case v3 vs 58-case pilot mismatch will confuse readers
7. **Preserve analysis from gitignored `results/`** — extract CSVs and cost data before cleanup

### Important (should-do):
6. Add TODO document for human judge calibration interface
7. Split `agent_runner.py` into sub-modules (or at minimum document its structure)
8. Extract PR runner shared utilities from `copilot_runner.py`
9. Update `docs/todo.md` to reflect actual open items
10. Ensure scoring methodology insights are preserved in committed docs

### Nice-to-have:
11. Add CI workflow
12. Increase test coverage on critical paths (score.py, evaluate.py)
13. Add `python-dotenv` to dependencies (or remove reference from CLAUDE.md)
14. Execute the audit-remediation plan tasks (docs/plans/2026-03-23-audit-remediation.md)

---

## 10. Human Judge Calibration Interface TODO

A calibration interface would help ensure consistent human scoring. Key requirements:

### Purpose
Enable human reviewers to calibrate their bug detection scoring against ground truth before judging real cases. This improves inter-rater reliability and catches scoring drift.

### Core Features
1. **Calibration exercises**: Present pre-scored cases (with known correct scores) and compare human scores against gold standard
2. **Side-by-side display**: Show tool output alongside ground truth buggy lines, diff, and fix PR
3. **Scoring rubric reference**: Always-visible scoring guide (detection 0-3, quality 0-4)
4. **Agreement tracking**: Compute Cohen's kappa between human scorer and ground truth
5. **Drift detection**: Track scoring consistency over time within a session

### Implementation Approach
- Extend existing Flask dashboard (`dashboard.py`) with a `/calibrate` route
- Reuse `score_models.py` schemas for storing human scores
- Select calibration cases from the golden set (cases with high-confidence ground truth)
- Store calibration results in `results/calibration/` for analysis

### Suggested UI Flow
1. Reviewer sees tool output for a case (comments, file/line references)
2. Reviewer assigns detection score (0-3) and quality score (0-4)
3. System reveals ground truth and shows agreement
4. After 10 calibration cases, show aggregate agreement metrics
5. Reviewer is "calibrated" when kappa > 0.7

### Data Model Addition
```python
class CalibrationResult(BaseModel):
    reviewer_id: str
    case_id: str
    tool: str
    human_detection: int  # 0-3
    human_quality: int    # 0-4
    gold_detection: int
    gold_quality: int
    timestamp: datetime
```
