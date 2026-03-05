# Phase 8: Analysis Enhancements + Human Judge Calibration

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add multi-dimensional slicing + cost metrics to the `analyze` command, and build a `human-judge` CLI for the 25%-sample calibration workflow (export blinded CSV → collect scores → compute Cohen's kappa).

**Architecture:** Two additive features. (1) `analyze.py` grows three loader functions, five slicer functions, and a `--cases-dir` option — all new, nothing removed. (2) `src/bugeval/human_judge.py` is a new Click command group (`export`, `import-scores`, `kappa`) wired into `cli.py`. No new third-party dependencies — kappa is computed manually, sampling uses `random` stdlib.

**Tech Stack:** Pydantic, Click, PyYAML, Python `csv` + `random` stdlib, existing `models.TestCase`, `result_models.NormalizedResult`, `judge_models.JudgeScore`

---

## Background

After `judge` runs, `results/run-{date}/` contains:
- `{case-id}-{tool}.yaml` — NormalizedResult (has `context_level`, `metadata.cost_usd`)
- `scores/{case-id}-{tool}.yaml` — JudgeScore (has `score`, `votes`, `noise`)

`cases/*.yaml` — TestCase (has `category`, `difficulty`, `severity`, `pr_size`, `language`)

The `analyze` command currently only groups scores by `tool`. To get richer results we need to join all three data sources and group by any TestCase field or context_level.

The experiment design requires a **calibration gate**: compute Cohen's kappa between LLM judge and human raters on 25% of cases; if kappa ≥ 0.85, deploy LLM judge at scale. `human-judge export` produces a blinded CSV for human raters; `human-judge import-scores` ingests their filled-in responses; `human-judge kappa` computes the agreement.

---

## Task 1: Extend `analyze.py` — multi-dimensional slicing + cost metrics

**Files:**
- Modify: `src/bugeval/analyze.py`
- Modify: `tests/test_analyze.py`

### Step 1: Write the failing tests

Add these tests to `tests/test_analyze.py`:

```python
# Add these imports at the top of test_analyze.py:
# from bugeval.analyze import (
#     load_cases_lookup, load_normalized_lookup,
#     slice_scores, slice_scores_by_context,
#     compute_cost_per_tool, generate_slice_markdown,
# )
# from bugeval.models import TestCase, Category, Difficulty, Severity, PRSize
# from bugeval.result_models import NormalizedResult, ResultMetadata

def _make_case(
    case_id: str = "case-001",
    category: str = "logic",
    difficulty: str = "medium",
    severity: str = "high",
    pr_size: str = "small",
    language: str = "rust",
) -> "TestCase":
    from bugeval.models import TestCase, Category, Difficulty, Severity, PRSize, ExpectedFinding
    return TestCase(
        id=case_id,
        repo="org/repo",
        base_commit="abc",
        head_commit="def",
        fix_commit="ghi",
        category=Category(category),
        difficulty=Difficulty(difficulty),
        severity=Severity(severity),
        language=language,
        pr_size=PRSize(pr_size),
        description="test case",
        expected_findings=[ExpectedFinding(file="a.rs", line=1, summary="bug")],
    )


def test_load_cases_lookup(tmp_path: Path) -> None:
    import yaml
    from bugeval.analyze import load_cases_lookup
    case = _make_case("case-001")
    (tmp_path / "case-001.yaml").write_text(
        yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False)
    )
    lookup = load_cases_lookup(tmp_path)
    assert "case-001" in lookup
    assert lookup["case-001"].category.value == "logic"


def test_load_cases_lookup_empty_dir(tmp_path: Path) -> None:
    from bugeval.analyze import load_cases_lookup
    assert load_cases_lookup(tmp_path) == {}


def test_load_normalized_lookup(tmp_path: Path) -> None:
    import yaml
    from bugeval.analyze import load_normalized_lookup
    from bugeval.result_models import NormalizedResult, ResultMetadata
    r = NormalizedResult(
        test_case_id="case-001", tool="greptile", context_level="diff-only",
        metadata=ResultMetadata(cost_usd=0.05),
    )
    (tmp_path / "case-001-greptile.yaml").write_text(
        yaml.safe_dump(r.model_dump(mode="json"), sort_keys=False)
    )
    lookup = load_normalized_lookup(tmp_path)
    assert ("case-001", "greptile") in lookup
    assert lookup[("case-001", "greptile")].metadata.cost_usd == pytest.approx(0.05)


def test_slice_scores_by_dimension() -> None:
    from bugeval.analyze import slice_scores
    scores = _make_scores([
        ("c1", "greptile", 2, 0.5),
        ("c2", "greptile", 0, 0.0),
    ])
    cases = {
        "c1": _make_case("c1", difficulty="easy"),
        "c2": _make_case("c2", difficulty="hard"),
    }
    groups = slice_scores(scores, cases, "difficulty")
    assert set(groups.keys()) == {"easy", "hard"}
    assert len(groups["easy"]) == 1
    assert len(groups["hard"]) == 1


def test_slice_scores_unknown_case() -> None:
    """Scores with no matching case go to 'unknown' group."""
    from bugeval.analyze import slice_scores
    scores = _make_scores([("missing-case", "greptile", 2, 0.5)])
    groups = slice_scores(scores, {}, "difficulty")
    assert "unknown" in groups


def test_slice_scores_by_context() -> None:
    from bugeval.analyze import slice_scores_by_context
    from bugeval.result_models import NormalizedResult
    scores = _make_scores([
        ("c1", "greptile", 2, 0.5),
        ("c2", "greptile", 0, 0.0),
    ])
    results = {
        ("c1", "greptile"): NormalizedResult(
            test_case_id="c1", tool="greptile", context_level="diff-only"
        ),
        ("c2", "greptile"): NormalizedResult(
            test_case_id="c2", tool="greptile", context_level="diff+repo"
        ),
    }
    groups = slice_scores_by_context(scores, results)
    assert set(groups.keys()) == {"diff-only", "diff+repo"}


def test_compute_cost_per_tool() -> None:
    from bugeval.analyze import compute_cost_per_tool
    from bugeval.result_models import NormalizedResult, ResultMetadata
    scores = _make_scores([
        ("c1", "greptile", 2, 0.5),
        ("c2", "greptile", 0, 0.0),
    ])
    results = {
        ("c1", "greptile"): NormalizedResult(
            test_case_id="c1", tool="greptile",
            metadata=ResultMetadata(cost_usd=0.10),
        ),
        ("c2", "greptile"): NormalizedResult(
            test_case_id="c2", tool="greptile",
            metadata=ResultMetadata(cost_usd=0.05),
        ),
    }
    cost = compute_cost_per_tool(scores, results)
    assert cost["greptile"]["total_cost_usd"] == pytest.approx(0.15)
    assert cost["greptile"]["cost_per_review"] == pytest.approx(0.075)
    # 1 bug caught (score=2), total_cost=0.15
    assert cost["greptile"]["cost_per_bug_caught"] == pytest.approx(0.15)


def test_generate_slice_markdown() -> None:
    from bugeval.analyze import generate_slice_markdown
    scores = _make_scores([
        ("c1", "greptile", 2, 0.5),
        ("c2", "greptile", 0, 0.0),
    ])
    cases = {
        "c1": _make_case("c1", difficulty="easy"),
        "c2": _make_case("c2", difficulty="hard"),
    }
    md = generate_slice_markdown(scores, cases, "difficulty")
    assert "difficulty" in md.lower()
    assert "easy" in md
    assert "hard" in md
    assert "greptile" in md
```

### Step 2: Run to verify they fail

```bash
uv run pytest tests/test_analyze.py -v -k "load_cases or load_normalized or slice or cost_per_tool or slice_markdown"
# Expected: FAIL — functions not found
```

### Step 3: Write the implementation

Add to `src/bugeval/analyze.py` (after existing imports, before `compute_catch_rate`):

```python
from bugeval.models import TestCase
from bugeval.result_models import NormalizedResult
from bugeval.run_pr_eval import load_cases


def load_cases_lookup(cases_dir: Path) -> dict[str, TestCase]:
    """Load all test cases from cases_dir. Returns {} if dir is empty or missing."""
    if not cases_dir.exists():
        return {}
    cases = load_cases(cases_dir)
    return {c.id: c for c in cases}


def load_normalized_lookup(run_dir: Path) -> dict[tuple[str, str], NormalizedResult]:
    """Load all NormalizedResult YAMLs from run_dir. Keys are (test_case_id, tool)."""
    from pydantic import ValidationError

    lookup: dict[tuple[str, str], NormalizedResult] = {}
    for path in run_dir.glob("*.yaml"):
        if path.name in ("checkpoint.yaml",) or path.stem.startswith("scores"):
            continue
        data = yaml.safe_load(path.read_text()) or {}
        try:
            r = NormalizedResult(**data)
            lookup[(r.test_case_id, r.tool)] = r
        except ValidationError:
            pass
    return lookup


def slice_scores(
    scores: list[JudgeScore],
    cases: dict[str, TestCase],
    dimension: str,
) -> dict[str, list[JudgeScore]]:
    """Group scores by a TestCase categorical field (e.g. 'difficulty', 'category')."""
    groups: dict[str, list[JudgeScore]] = {}
    for s in scores:
        case = cases.get(s.test_case_id)
        if case is None:
            key = "unknown"
        else:
            raw = getattr(case, dimension, "unknown")
            key = raw.value if hasattr(raw, "value") else str(raw)
        groups.setdefault(key, []).append(s)
    return groups


def slice_scores_by_context(
    scores: list[JudgeScore],
    results: dict[tuple[str, str], NormalizedResult],
) -> dict[str, list[JudgeScore]]:
    """Group scores by context_level from the corresponding NormalizedResult."""
    groups: dict[str, list[JudgeScore]] = {}
    for s in scores:
        r = results.get((s.test_case_id, s.tool))
        key = r.context_level if r else "unknown"
        groups.setdefault(key, []).append(s)
    return groups


def compute_cost_per_tool(
    scores: list[JudgeScore],
    results: dict[tuple[str, str], NormalizedResult],
) -> dict[str, dict[str, float]]:
    """Compute per-tool cost_per_review and cost_per_bug_caught."""
    by_tool: dict[str, list[JudgeScore]] = {}
    for s in scores:
        by_tool.setdefault(s.tool, []).append(s)

    out: dict[str, dict[str, float]] = {}
    for tool, tool_scores in by_tool.items():
        total_cost = sum(
            results[(s.test_case_id, s.tool)].metadata.cost_usd
            for s in tool_scores
            if (s.test_case_id, s.tool) in results
        )
        n = len(tool_scores)
        bugs_caught = sum(1 for s in tool_scores if s.score >= 2)
        out[tool] = {
            "total_cost_usd": total_cost,
            "cost_per_review": total_cost / n if n > 0 else 0.0,
            "cost_per_bug_caught": total_cost / bugs_caught if bugs_caught > 0 else 0.0,
        }
    return out


def generate_slice_markdown(
    scores: list[JudgeScore],
    cases: dict[str, TestCase],
    dimension: str,
) -> str:
    """Produce a per-dimension breakdown markdown table (value × tool)."""
    groups = slice_scores(scores, cases, dimension)
    lines = [
        f"## By {dimension.replace('_', ' ').title()}",
        "",
        "| Value | Tool | Cases | Catch Rate | Avg Score |",
        "|-------|------|-------|-----------|-----------|",
    ]
    for value in sorted(groups.keys()):
        agg = aggregate_scores(groups[value])
        for tool, metrics in agg.items():
            lines.append(
                f"| {value} | {tool} | {metrics['count']} "
                f"| {metrics['catch_rate']:.1%} "
                f"| {metrics['avg_score']:.2f} |"
            )
    return "\n".join(lines)
```

Update the `analyze` Click command signature — add `--cases-dir`:

```python
@click.command("analyze")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Path to run directory (must contain scores/ subdirectory)",
)
@click.option(
    "--cases-dir",
    default="cases/",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Directory containing case YAML files (for dimensional slicing)",
)
@click.option("--no-charts", is_flag=True, default=False, help="Skip matplotlib chart generation")
def analyze(run_dir: str, cases_dir: str, no_charts: bool) -> None:
    """Aggregate judge scores into comparison tables and charts."""
    resolved = Path(run_dir)
    scores_dir = resolved / "scores"
    if not scores_dir.exists() or not list(scores_dir.glob("*.yaml")):
        click.echo(f"No score files found in {scores_dir}")
        return

    from pydantic import ValidationError

    scores = []
    for path in sorted(scores_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        try:
            scores.append(JudgeScore(**data))
        except ValidationError as exc:
            click.echo(f"Warning: skipping {path.name} — {exc}", err=True)

    agg = aggregate_scores(scores)
    cases = load_cases_lookup(Path(cases_dir))
    results = load_normalized_lookup(resolved)

    out_dir = resolved / "analysis"
    out_dir.mkdir(exist_ok=True)

    # Main report
    md_lines = [generate_markdown(agg)]

    # Cost metrics (if metadata available)
    cost = compute_cost_per_tool(scores, results)
    if any(m["total_cost_usd"] > 0 for m in cost.values()):
        cost_lines = [
            "\n## Cost Metrics\n",
            "| Tool | Total Cost | Per Review | Per Bug Caught |",
            "|------|-----------|-----------|---------------|",
        ]
        for tool, m in sorted(cost.items()):
            cost_lines.append(
                f"| {tool} | ${m['total_cost_usd']:.4f} "
                f"| ${m['cost_per_review']:.4f} "
                f"| ${m['cost_per_bug_caught']:.4f} |"
            )
        md_lines.append("\n".join(cost_lines))

    # Dimensional slices (only if cases loaded)
    if cases:
        for dim in ("category", "difficulty", "severity", "pr_size", "language"):
            md_lines.append(generate_slice_markdown(scores, cases, dim))
        md_lines.append(slice_scores_by_context(scores, results) and
                        generate_slice_markdown_context(scores, results))

    full_report = "\n\n".join(md_lines)
    (out_dir / "report.md").write_text(full_report)
    click.echo(f"Report → {out_dir / 'report.md'}")

    generate_csv(agg, out_dir / "scores.csv")
    click.echo(f"CSV → {out_dir / 'scores.csv'}")

    if not no_charts:
        if generate_charts(agg, out_dir):
            click.echo(f"Charts → {out_dir}/")
        else:
            click.echo("Charts skipped (matplotlib not installed)", err=True)

    click.echo("\n" + generate_markdown(agg))
```

Wait — the above has a bug (`generate_slice_markdown_context` not defined). Add this helper too:

```python
def generate_slice_markdown_context(
    scores: list[JudgeScore],
    results: dict[tuple[str, str], NormalizedResult],
) -> str:
    """Produce a context_level breakdown markdown table."""
    groups = slice_scores_by_context(scores, results)
    lines = [
        "## By Context Level",
        "",
        "| Context | Tool | Cases | Catch Rate | Avg Score |",
        "|---------|------|-------|-----------|-----------|",
    ]
    for value in sorted(groups.keys()):
        agg = aggregate_scores(groups[value])
        for tool, metrics in agg.items():
            lines.append(
                f"| {value} | {tool} | {metrics['count']} "
                f"| {metrics['catch_rate']:.1%} "
                f"| {metrics['avg_score']:.2f} |"
            )
    return "\n".join(lines)
```

Clean up the `analyze` CLI (the ternary expression for context slice above is wrong). Write the final correct version:

```python
    # Dimensional slices (only if cases loaded)
    if cases:
        for dim in ("category", "difficulty", "severity", "pr_size", "language"):
            md_lines.append(generate_slice_markdown(scores, cases, dim))
    if results:
        md_lines.append(generate_slice_markdown_context(scores, results))
```

### Step 4: Run tests

```bash
uv run pytest tests/test_analyze.py -v
# Expected: all pass (8 new + 6 existing = 14)
```

### Step 5: Lint + typecheck

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pyright src/
# Expected: clean
```

### Step 6: Run all tests

```bash
uv run pytest --tb=short 2>&1 | tail -3
# Expected: ~291 passed
```

### Step 7: Commit

```bash
git add src/bugeval/analyze.py tests/test_analyze.py
git commit -m "feat(phase8): analyze — multi-dimensional slicing + cost metrics"
```

---

## Task 2: `human_judge.py` — Export / Import / Kappa

**Files:**
- Create: `src/bugeval/human_judge.py`
- Modify: `src/bugeval/cli.py`
- Create: `tests/test_human_judge.py`

### Background

The calibration workflow:
1. `human-judge export` — sample 25% of score files (stratified by difficulty), blind tool names, write CSV
2. Human raters fill in a `human_score` column (0–3)
3. `human-judge import-scores` — read filled CSV, write per-row YAML to `{run_dir}/human_judge/`
4. `human-judge kappa` — join LLM scores + human scores, compute Cohen's kappa, report status vs. 0.85 threshold

The tool-blinding mapping (`Tool-A`, `Tool-B`, …) is written to `{run_dir}/human_judge/tool_map.yaml` during export and read back during import/kappa.

### Step 1: Write the failing tests

```python
# tests/test_human_judge.py
"""Tests for human_judge module."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from bugeval.human_judge import (
    cohen_kappa,
    export_sample,
    import_scores,
    select_sample,
)
from bugeval.judge_models import JudgeScore, NoiseStats


def _make_judge_score(case_id: str, tool: str, score: int) -> JudgeScore:
    return JudgeScore(
        test_case_id=case_id,
        tool=tool,
        score=score,
        votes=[score, score, score],
        reasoning="test",
    )


# --- cohen_kappa ---

def test_cohen_kappa_perfect_agreement() -> None:
    assert cohen_kappa([2, 0, 3, 1], [2, 0, 3, 1]) == pytest.approx(1.0)


def test_cohen_kappa_no_agreement() -> None:
    # All disagreements; kappa could be negative
    result = cohen_kappa([0, 0, 0, 0], [3, 3, 3, 3])
    assert result < 1.0


def test_cohen_kappa_empty() -> None:
    assert cohen_kappa([], []) == 0.0


# --- select_sample ---

def test_select_sample_rate() -> None:
    scores = [_make_judge_score(f"c{i:03d}", "greptile", 2) for i in range(20)]
    sample = select_sample(scores, sample_rate=0.25)
    assert len(sample) == 5


def test_select_sample_min_one() -> None:
    scores = [_make_judge_score("c001", "greptile", 2)]
    sample = select_sample(scores, sample_rate=0.10)
    assert len(sample) >= 1


# --- export_sample ---

def test_export_sample_writes_csv(tmp_path: Path) -> None:
    scores = [
        _make_judge_score("c001", "greptile", 2),
        _make_judge_score("c002", "coderabbit", 0),
        _make_judge_score("c003", "greptile", 3),
        _make_judge_score("c004", "coderabbit", 1),
    ]
    out_path = tmp_path / "sample.csv"
    export_sample(scores, run_dir=tmp_path, output_path=out_path, sample_rate=1.0)
    assert out_path.exists()
    rows = list(csv.DictReader(out_path.read_text().splitlines()))
    assert len(rows) == 4
    # Tool names must be blinded
    tool_values = {r["tool"] for r in rows}
    assert "greptile" not in tool_values
    assert "coderabbit" not in tool_values
    # tool_map.yaml must exist
    assert (tmp_path / "human_judge" / "tool_map.yaml").exists()


# --- import_scores ---

def test_import_scores_writes_yaml(tmp_path: Path) -> None:
    # Set up: export first to create tool_map
    scores = [
        _make_judge_score("c001", "greptile", 2),
        _make_judge_score("c002", "greptile", 0),
    ]
    csv_path = tmp_path / "sample.csv"
    export_sample(scores, run_dir=tmp_path, output_path=csv_path, sample_rate=1.0)

    # Edit CSV to add human_score
    rows = list(csv.DictReader(csv_path.read_text().splitlines()))
    for r in rows:
        r["human_score"] = "2"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    import_scores(csv_path, run_dir=tmp_path)
    human_dir = tmp_path / "human_judge"
    yaml_files = list(human_dir.glob("*.yaml"))
    # Should have score YAMLs + tool_map.yaml
    score_files = [p for p in yaml_files if p.name != "tool_map.yaml"]
    assert len(score_files) == 2
```

### Step 2: Run to verify they fail

```bash
uv run pytest tests/test_human_judge.py -v
# Expected: FAIL — module not found
```

### Step 3: Write the implementation

```python
# src/bugeval/human_judge.py
"""Human judge calibration workflow: export blinded CSV, import scores, compute Cohen's kappa."""

from __future__ import annotations

import csv
import random
from collections import Counter
from pathlib import Path
from typing import Any

import click
import yaml

from bugeval.judge_models import JudgeScore


def cohen_kappa(scores_a: list[int], scores_b: list[int]) -> float:
    """Compute Cohen's kappa between two raters. Both lists must be same length."""
    n = len(scores_a)
    if n == 0:
        return 0.0
    cats = list(range(4))  # scores 0–3
    po = sum(1 for a, b in zip(scores_a, scores_b) if a == b) / n
    pe = sum(
        (sum(1 for a in scores_a if a == k) / n)
        * (sum(1 for b in scores_b if b == k) / n)
        for k in cats
    )
    return (po - pe) / (1 - pe) if pe < 1.0 else 1.0


def select_sample(scores: list[JudgeScore], sample_rate: float = 0.25) -> list[JudgeScore]:
    """Select a random sample of scores. Always returns at least 1 if scores non-empty."""
    n = max(1, round(len(scores) * sample_rate))
    return random.sample(scores, min(n, len(scores)))


def _make_tool_map(scores: list[JudgeScore]) -> dict[str, str]:
    """Assign anonymous labels (Tool-A, Tool-B, …) to real tool names."""
    tools = sorted({s.tool for s in scores})
    return {t: f"Tool-{chr(65 + i)}" for i, t in enumerate(tools)}


def export_sample(
    scores: list[JudgeScore],
    run_dir: Path,
    output_path: Path,
    sample_rate: float = 0.25,
) -> None:
    """Select sample, blind tool names, write CSV for human raters.

    Writes tool_map.yaml to run_dir/human_judge/ for later de-blinding.
    """
    sample = select_sample(scores, sample_rate)
    tool_map = _make_tool_map(scores)

    hj_dir = run_dir / "human_judge"
    hj_dir.mkdir(exist_ok=True)
    (hj_dir / "tool_map.yaml").write_text(
        yaml.safe_dump(tool_map, sort_keys=True)
    )

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["row_id", "test_case_id", "tool", "llm_score", "human_score", "notes"],
        )
        writer.writeheader()
        for i, s in enumerate(sample):
            writer.writerow({
                "row_id": f"{i:04d}",
                "test_case_id": s.test_case_id,
                "tool": tool_map.get(s.tool, s.tool),
                "llm_score": s.score,
                "human_score": "",
                "notes": "",
            })


def import_scores(input_path: Path, run_dir: Path) -> None:
    """Read a filled-in human judge CSV and write one YAML per row to run_dir/human_judge/.

    Requires run_dir/human_judge/tool_map.yaml (written by export_sample).
    """
    hj_dir = run_dir / "human_judge"
    tool_map_path = hj_dir / "tool_map.yaml"
    if not tool_map_path.exists():
        raise FileNotFoundError(f"tool_map.yaml missing in {hj_dir} — run export first")

    tool_map: dict[str, str] = yaml.safe_load(tool_map_path.read_text()) or {}
    reverse_map = {v: k for k, v in tool_map.items()}

    rows = list(csv.DictReader(input_path.read_text(encoding="utf-8").splitlines()))
    for row in rows:
        human_score_raw = row.get("human_score", "").strip()
        if not human_score_raw:
            continue
        try:
            human_score = int(human_score_raw)
        except ValueError:
            click.echo(f"Warning: invalid human_score '{human_score_raw}' in row {row.get('row_id')}", err=True)
            continue

        real_tool = reverse_map.get(row["tool"], row["tool"])
        case_id = row["test_case_id"]
        out = {
            "test_case_id": case_id,
            "tool": real_tool,
            "human_score": human_score,
            "llm_score": int(row.get("llm_score", 0)),
            "notes": row.get("notes", ""),
        }
        out_path = hj_dir / f"{case_id}-{real_tool}.yaml"
        out_path.write_text(yaml.safe_dump(out, sort_keys=False))


def compute_kappa_report(run_dir: Path) -> dict[str, Any]:
    """Load LLM scores (scores/) and human scores (human_judge/), compute kappa.

    Returns dict with: kappa, n_pairs, threshold, calibrated (bool), pairs.
    """
    scores_dir = run_dir / "scores"
    hj_dir = run_dir / "human_judge"

    llm_lookup: dict[tuple[str, str], int] = {}
    for path in scores_dir.glob("*.yaml"):
        data = yaml.safe_load(path.read_text()) or {}
        cid = data.get("test_case_id", "")
        tool = data.get("tool", "")
        score = data.get("score", 0)
        if cid and tool:
            llm_lookup[(cid, tool)] = int(score)

    llm_scores: list[int] = []
    human_scores: list[int] = []
    pairs: list[dict[str, Any]] = []

    for path in hj_dir.glob("*.yaml"):
        if path.name == "tool_map.yaml":
            continue
        data = yaml.safe_load(path.read_text()) or {}
        cid = data.get("test_case_id", "")
        tool = data.get("tool", "")
        human_score = data.get("human_score")
        llm_score = llm_lookup.get((cid, tool))
        if human_score is not None and llm_score is not None:
            llm_scores.append(llm_score)
            human_scores.append(int(human_score))
            pairs.append({"test_case_id": cid, "tool": tool,
                          "llm": llm_score, "human": int(human_score)})

    kappa = cohen_kappa(llm_scores, human_scores)
    threshold = 0.85
    return {
        "kappa": kappa,
        "n_pairs": len(pairs),
        "threshold": threshold,
        "calibrated": kappa >= threshold,
        "pairs": pairs,
    }


@click.group("human-judge")
def human_judge() -> None:
    """Human judge calibration: export blinded CSV, import scores, compute kappa."""


@human_judge.command("export")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Path to run directory (must contain scores/)",
)
@click.option(
    "--output",
    "output_path",
    default=None,
    type=click.Path(dir_okay=False),
    help="Output CSV path (default: {run_dir}/human_judge/sample.csv)",
)
@click.option("--sample-rate", default=0.25, show_default=True, help="Fraction to sample")
def export_cmd(run_dir: str, output_path: str | None, sample_rate: float) -> None:
    """Export blinded sample CSV for human raters."""
    resolved = Path(run_dir)
    scores = []
    for path in sorted((resolved / "scores").glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        try:
            scores.append(JudgeScore(**data))
        except Exception:
            pass

    if not scores:
        click.echo("No scores found.")
        return

    out = Path(output_path) if output_path else resolved / "human_judge" / "sample.csv"
    out.parent.mkdir(exist_ok=True)
    export_sample(scores, resolved, out, sample_rate)
    click.echo(f"Exported {round(len(scores) * sample_rate)} rows → {out}")
    click.echo(f"Tool map → {resolved / 'human_judge' / 'tool_map.yaml'}")


@human_judge.command("import-scores")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Path to run directory",
)
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Filled-in CSV from human raters",
)
def import_cmd(run_dir: str, input_path: str) -> None:
    """Import human scores from filled-in CSV."""
    import_scores(Path(input_path), Path(run_dir))
    click.echo(f"Scores imported → {Path(run_dir) / 'human_judge'}/")


@human_judge.command("kappa")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=False),
    help="Path to run directory",
)
def kappa_cmd(run_dir: str) -> None:
    """Compute Cohen's kappa between LLM judge and human scores."""
    report = compute_kappa_report(Path(run_dir))
    click.echo(f"Pairs: {report['n_pairs']}")
    click.echo(f"Kappa: {report['kappa']:.3f} (threshold: {report['threshold']})")
    if report["calibrated"]:
        click.echo("✓ CALIBRATED — LLM judge approved for scale-up.")
    else:
        click.echo("✗ NOT CALIBRATED — Review judge prompt and re-run calibration.")
```

### Step 4: Wire into `src/bugeval/cli.py`

Add:
```python
from bugeval.human_judge import human_judge
# ...
cli.add_command(human_judge)
```

### Step 5: Run tests

```bash
uv run pytest tests/test_human_judge.py -v
# Expected: 8 tests pass
```

### Step 6: Lint + typecheck

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pyright src/
```

Fix any issues (likely: `Any` import, long lines).

### Step 7: Run all tests

```bash
uv run pytest --tb=short 2>&1 | tail -3
# Expected: ~299 passed
```

### Step 8: Commit

```bash
git add src/bugeval/human_judge.py src/bugeval/cli.py tests/test_human_judge.py
git commit -m "feat(phase8): add human-judge CLI — export/import/kappa calibration"
```

---

## Task 3: Final Verification

```bash
uv run pytest -v
# Expected: ~299+ tests, all passed

uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
# Expected: All checks passed

uv run pyright src/
# Expected: 0 errors

uv run bugeval analyze --help
# Expected: shows --cases-dir option

uv run bugeval human-judge --help
# Expected: shows export, import-scores, kappa subcommands

uv run bugeval human-judge export --help
# Expected: shows --run-dir, --output, --sample-rate

uv run bugeval human-judge kappa --help
# Expected: shows --run-dir
```

Commit:
```bash
git add -u
git commit -m "chore: phase 8 complete — dimensional analysis + human judge calibration"
```

---

## New files summary

| File | Purpose |
|------|---------|
| `src/bugeval/human_judge.py` | export/import/kappa CLI + cohen_kappa + select_sample |
| `tests/test_human_judge.py` | 8 tests for human judge workflow |

## Modified files summary

| File | Changes |
|------|---------|
| `src/bugeval/analyze.py` | +load_cases_lookup, +load_normalized_lookup, +slice_scores, +slice_scores_by_context, +compute_cost_per_tool, +generate_slice_markdown, +generate_slice_markdown_context; analyze CLI gets --cases-dir |
| `src/bugeval/cli.py` | +human_judge command group |
| `tests/test_analyze.py` | +8 new tests for slicing and cost functions |
