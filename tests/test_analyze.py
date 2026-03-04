# tests/test_analyze.py
"""Tests for analyze module."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugeval.analyze import (
    aggregate_scores,
    compute_catch_rate,
    compute_snr,
    generate_csv,
    generate_markdown,
)
from bugeval.judge_models import JudgeScore, NoiseStats


def _make_scores(data: list[tuple[str, str, int, float]]) -> list[JudgeScore]:
    """Helper: (case_id, tool, score, snr) → list[JudgeScore]."""
    return [
        JudgeScore(
            test_case_id=cid,
            tool=tool,
            score=score,
            votes=[score, score, score],
            reasoning="test",
            noise=NoiseStats(total_comments=4, true_positives=int(snr * 4), snr=snr),
        )
        for cid, tool, score, snr in data
    ]


def test_compute_catch_rate_basic() -> None:
    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 0, 0.0),
            ("c3", "greptile", 3, 1.0),
        ]
    )
    rate = compute_catch_rate(scores)
    assert rate == pytest.approx(2 / 3)


def test_compute_catch_rate_empty() -> None:
    assert compute_catch_rate([]) == 0.0


def test_compute_snr_average() -> None:
    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "greptile", 1, 0.25),
        ]
    )
    assert compute_snr(scores) == pytest.approx(0.375)


def test_aggregate_scores_groups_by_tool() -> None:
    scores = _make_scores(
        [
            ("c1", "greptile", 2, 0.5),
            ("c2", "coderabbit", 3, 1.0),
            ("c3", "greptile", 0, 0.0),
        ]
    )
    agg = aggregate_scores(scores)
    assert set(agg.keys()) == {"greptile", "coderabbit"}
    assert agg["greptile"]["catch_rate"] == pytest.approx(0.5)
    assert agg["coderabbit"]["catch_rate"] == pytest.approx(1.0)
    assert agg["greptile"]["count"] == 2


def test_generate_csv(tmp_path: Path) -> None:
    scores = _make_scores([("c1", "greptile", 2, 0.5)])
    agg = aggregate_scores(scores)
    path = tmp_path / "scores.csv"
    generate_csv(agg, path)
    content = path.read_text()
    assert "greptile" in content
    assert "catch_rate" in content


def test_generate_markdown(tmp_path: Path) -> None:
    scores = _make_scores([("c1", "greptile", 2, 0.5)])
    agg = aggregate_scores(scores)
    md = generate_markdown(agg)
    assert "| Tool |" in md
    assert "greptile" in md
