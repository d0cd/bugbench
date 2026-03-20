"""Tests for the dashboard Flask app."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from bugeval.dashboard import (
    _invalidate_cache,
    _parse_case_form,
    create_app,
    is_reviewed,
    load_all_cases,
    load_review_state,
    mark_reviewed,
    save_review_state,
)
from bugeval.models import Category, Difficulty, ExpectedFinding, PRSize, Severity, TestCase

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_case(case_id: str = "leo-001", repo: str = "leo") -> TestCase:
    return TestCase(
        id=case_id,
        repo=repo,
        base_commit="abc123",
        head_commit="def456",
        fix_commit="ghi789",
        category=Category.logic,
        difficulty=Difficulty.medium,
        severity=Severity.high,
        language="rust",
        pr_size=PRSize.small,
        description="A test case",
        expected_findings=[ExpectedFinding(file="src/main.rs", line=42, summary="off-by-one")],
        needs_manual_review=True,
    )


@pytest.fixture()
def cases_dir(tmp_path: Path) -> Path:
    c_dir = tmp_path / "cases" / "final"
    repo_dir = c_dir / "leo"
    repo_dir.mkdir(parents=True)
    case = _make_case()
    (repo_dir / "leo-001.yaml").write_text(
        yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False)
    )
    return c_dir


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    r_dir = tmp_path / "results"
    r_dir.mkdir()
    return r_dir


@pytest.fixture()
def app(cases_dir: Path, results_dir: Path):  # type: ignore[no-untyped-def]
    app = create_app(cases_dir, results_dir)
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(app):  # type: ignore[no-untyped-def]
    return app.test_client()


# ---------------------------------------------------------------------------
# Sidecar review-state
# ---------------------------------------------------------------------------


def test_load_review_state_empty(cases_dir: Path) -> None:
    state = load_review_state(cases_dir, "leo")
    assert state == {}


def test_save_and_load_review_state(cases_dir: Path) -> None:
    save_review_state(cases_dir, "leo", {"leo-001": {"reviewed": True}})
    state = load_review_state(cases_dir, "leo")
    assert state["leo-001"]["reviewed"] is True


def test_mark_reviewed(cases_dir: Path) -> None:
    case = _make_case()
    mark_reviewed(cases_dir, case, reviewer="tester")
    assert is_reviewed(cases_dir, "leo-001", "leo")


def test_is_reviewed_false_initially(cases_dir: Path) -> None:
    assert not is_reviewed(cases_dir, "leo-001", "leo")


def test_is_reviewed_missing_repo(cases_dir: Path) -> None:
    assert not is_reviewed(cases_dir, "leo-001", "nonexistent-repo")


# ---------------------------------------------------------------------------
# load_all_cases
# ---------------------------------------------------------------------------


def test_load_all_cases(cases_dir: Path) -> None:
    cases = load_all_cases(cases_dir)
    assert len(cases) == 1
    assert cases[0].id == "leo-001"


def test_load_all_cases_empty_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert load_all_cases(empty) == []


def test_load_all_cases_skips_invalid_yaml(cases_dir: Path) -> None:
    (cases_dir / "leo" / "bad.yaml").write_text("not: valid: yaml: ::::")
    # Should not raise; just skip bad file
    cases = load_all_cases(cases_dir)
    assert len(cases) == 1  # bad file skipped


# ---------------------------------------------------------------------------
# Flask routes — GET returns 200
# ---------------------------------------------------------------------------


def test_index_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_case_list_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/cases")
    assert resp.status_code == 200
    assert b"Cases" in resp.data


def test_case_detail_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    with patch("bugeval.dashboard.fetch_diff", return_value="--- a/src/main.rs\n+diff"):
        resp = client.get("/cases/leo-001")
    assert resp.status_code == 200
    assert b"leo-001" in resp.data


def test_case_detail_404_for_unknown(client) -> None:  # type: ignore[no-untyped-def]
    with patch("bugeval.dashboard.fetch_diff", return_value=""):
        resp = client.get("/cases/does-not-exist")
    assert resp.status_code == 404


def test_human_judge_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/human-judge")
    assert resp.status_code == 200
    assert b"Human Judge" in resp.data


def test_dx_page_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/dx")
    assert resp.status_code == 200
    assert b"DX Assessment" in resp.data


def test_metrics_list_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"Metrics" in resp.data


def test_metrics_detail_404_for_unknown(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/metrics/nonexistent-run")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Case edit POST
# ---------------------------------------------------------------------------


def test_case_edit_post_saves(client, cases_dir: Path) -> None:  # type: ignore[no-untyped-def]
    with patch("bugeval.dashboard.fetch_diff", return_value=""):
        resp = client.post(
            "/cases/leo-001",
            data={
                "action": "save",
                "category": "memory",
                "difficulty": "hard",
                "severity": "critical",
                "pr_size": "large",
                "visibility": "public",
                "base_commit": "abc123",
                "head_commit": "def456",
                "description": "Updated description",
                "ef_file_0": "src/lib.rs",
                "ef_line_0": "10",
                "ef_summary_0": "overflow",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 302  # redirect after save

    # Verify YAML was updated
    yaml_path = cases_dir / "leo" / "leo-001.yaml"
    data = yaml.safe_load(yaml_path.read_text())
    assert data["category"] == "memory"
    assert data["difficulty"] == "hard"
    assert data["description"] == "Updated description"
    assert data["expected_findings"][0]["file"] == "src/lib.rs"


def test_parse_case_form_preserves_verified_fields() -> None:
    original = _make_case()
    original = original.model_copy(update={"verified": True, "verified_by": "alice"})
    form = {
        "base_commit": original.base_commit,
        "head_commit": original.head_commit,
        "category": original.category.value,
        "difficulty": original.difficulty.value,
        "severity": original.severity.value,
        "pr_size": original.pr_size.value,
        "visibility": original.visibility.value,
        "description": original.description,
    }
    result = _parse_case_form(form, original)
    assert result.verified is True
    assert result.verified_by == "alice"


def test_case_accept_marks_reviewed(client, cases_dir: Path) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(
        "/cases/leo-001",
        data={"action": "accept"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert is_reviewed(cases_dir, "leo-001", "leo")


def test_case_skip_redirects_to_next(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(
        "/cases/leo-001",
        data={"action": "skip"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Case list filtering
# ---------------------------------------------------------------------------


def test_case_list_filter_by_repo(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?repo=leo")
    assert resp.status_code == 200
    data = resp.get_json()
    assert any(c["id"] == "leo-001" for c in data["cases"])


def test_case_list_filter_no_match(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?repo=nonexistent")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 0
    assert data["cases"] == []


# ---------------------------------------------------------------------------
# Diff fetching (mocked)
# ---------------------------------------------------------------------------


def test_fetch_diff_ajax(client) -> None:  # type: ignore[no-untyped-def]
    """Diff is loaded via AJAX endpoint, not inlined in the page."""
    resp = client.get("/cases/leo-001")
    assert resp.status_code == 200
    assert b"Loading diff" in resp.data

    with patch("bugeval.dashboard.fetch_diff", return_value="+added line\n-removed line") as mock:
        resp = client.get("/api/diff/leo-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "+added line" in data["diff"]
    mock.assert_called_once()


# ---------------------------------------------------------------------------
# Human judge scoring POST
# ---------------------------------------------------------------------------


def test_human_judge_score_post(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    resp = client.post(
        "/human-judge/score",
        data={
            "run_id": "run-2026-03-07",
            "case_id": "leo-001",
            "tool": "greptile",
            "human_score": "2",
            "notes": "looks right",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    saved = yaml.safe_load((run_dir / "human_judge" / "leo-001-greptile.yaml").read_text())
    assert saved["human_score"] == 2
    assert saved["notes"] == "looks right"


def test_human_judge_score_invalid_score(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    resp = client.post(
        "/human-judge/score",
        data={
            "run_id": "run-2026-03-07",
            "case_id": "leo-001",
            "tool": "greptile",
            "human_score": "5",  # out of range
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DX Assessment POST
# ---------------------------------------------------------------------------


def test_dx_save(client, results_dir: Path, cases_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    # Write a fake normalized result
    nr = {
        "test_case_id": "leo-001",
        "tool": "greptile",
        "context_level": "diff-only",
        "comments": [],
        "metadata": {"tokens": 0, "cost_usd": 0.0, "time_seconds": 0.0},
        "dx": None,
    }
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(nr, sort_keys=False))

    resp = client.post(
        "/dx/save",
        data={
            "run_id": "run-2026-03-07",
            "tool": "greptile",
            "actionability": "4",
            "false_positive_burden": "3",
            "integration_friction": "2",
            "response_latency": "5",
            "notes": "decent",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # Verify dx saved in YAML
    data = yaml.safe_load((run_dir / "leo-001--greptile.yaml").read_text())
    assert data["dx"]["actionability"] == 4
    assert data["dx"]["notes"] == "decent"


# ---------------------------------------------------------------------------
# CLI command help
# ---------------------------------------------------------------------------


def test_dashboard_cli_help() -> None:
    from click.testing import CliRunner

    from bugeval.dashboard import dashboard

    runner = CliRunner()
    result = runner.invoke(dashboard, ["--help"])
    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--cases-dir" in result.output
    assert "--results-dir" in result.output


# ---------------------------------------------------------------------------
# Index page stats
# ---------------------------------------------------------------------------


def test_index_shows_counts(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"1" in resp.data  # total cases


def test_index_shows_nav_links(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/")
    # Index should have navigation links to key pages
    assert b"Dataset Inspector" in resp.data or b"/dataset" in resp.data


# ---------------------------------------------------------------------------
# Metrics with run data
# ---------------------------------------------------------------------------


def test_metrics_detail_with_run(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    scores_dir = run_dir / "scores"
    scores_dir.mkdir(parents=True)

    score = {
        "test_case_id": "leo-001",
        "tool": "greptile",
        "score": 2,
        "votes": [2, 2, 2],
        "reasoning": "correct",
        "noise": {"total_comments": 1, "relevant_comments": 1, "snr": 1.0},
    }
    (scores_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(score, sort_keys=False))

    resp = client.get("/metrics/run-2026-03-07")
    assert resp.status_code == 200
    assert b"greptile" in resp.data


# ---------------------------------------------------------------------------
# Case list — sorting
# ---------------------------------------------------------------------------


def test_api_cases_sort_by_id(client, cases_dir: Path) -> None:
    """Adding a second case and sorting by ID returns both in order."""
    (cases_dir / "leo" / "leo-002.yaml").write_text(
        yaml.safe_dump(_make_case("leo-002").model_dump(mode="json"), sort_keys=False)
    )
    resp = client.get("/api/cases?sort=id")
    data = resp.get_json()
    ids = [c["id"] for c in data["cases"]]
    assert ids.index("leo-001") < ids.index("leo-002")


def test_api_cases_sort_reverse(client, cases_dir: Path) -> None:
    """Reverse sort (-id) puts leo-002 before leo-001."""
    (cases_dir / "leo" / "leo-002.yaml").write_text(
        yaml.safe_dump(_make_case("leo-002").model_dump(mode="json"), sort_keys=False)
    )
    resp = client.get("/api/cases?sort=-id")
    data = resp.get_json()
    ids = [c["id"] for c in data["cases"]]
    assert ids.index("leo-002") < ids.index("leo-001")


# ---------------------------------------------------------------------------
# Case list — pagination
# ---------------------------------------------------------------------------


def test_api_cases_page_2_empty(client) -> None:
    """Requesting page 2 with fewer than per_page cases returns empty list."""
    resp = client.get("/api/cases?page=2")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["cases"] == []
    assert data["page"] == 2


def test_api_cases_pagination_multiple_pages(client, cases_dir: Path) -> None:
    """Pagination metadata is correct when cases exceed per_page."""
    from unittest.mock import patch as mock_patch

    many = [_make_case(f"leo-{i:03d}") for i in range(51)]
    with mock_patch("bugeval.dashboard.load_all_cases", return_value=many):
        resp = client.get("/api/cases?per_page=50")
    data = resp.get_json()
    assert data["total"] == 51
    assert data["pages"] == 2
    assert len(data["cases"]) == 50


def test_api_cases_pagination_page_2(client, cases_dir: Path) -> None:
    """Page 2 returns remaining cases."""
    from unittest.mock import patch as mock_patch

    many = [_make_case(f"leo-{i:03d}") for i in range(51)]
    with mock_patch("bugeval.dashboard.load_all_cases", return_value=many):
        resp = client.get("/api/cases?page=2&per_page=50")
    data = resp.get_json()
    assert len(data["cases"]) == 1
    assert data["page"] == 2


# ---------------------------------------------------------------------------
# Metrics — cost data
# ---------------------------------------------------------------------------

_SCORE_YAML = {
    "test_case_id": "leo-001",
    "tool": "greptile",
    "score": 2,
    "votes": [2, 2, 2],
    "reasoning": "correct",
    "noise": {"total_comments": 1, "relevant_comments": 1, "snr": 1.0},
}


def _write_score(run_dir: Path, data: dict = _SCORE_YAML) -> None:
    scores_dir = run_dir / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def test_metrics_detail_with_cost_data(client, results_dir: Path) -> None:
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    nr = {
        "test_case_id": "leo-001",
        "tool": "greptile",
        "context_level": "diff-only",
        "comments": [],
        "metadata": {"tokens": 500, "cost_usd": 0.0500, "time_seconds": 1.5},
        "dx": None,
    }
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(nr, sort_keys=False))

    resp = client.get("/metrics/run-2026-03-07")
    assert resp.status_code == 200
    assert b"Cost" in resp.data
    assert b"0.05" in resp.data


def test_metrics_detail_with_zero_cost_hides_cost_table(client, results_dir: Path) -> None:
    """Cost table is suppressed when all tools have zero cost."""
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    nr = {
        "test_case_id": "leo-001",
        "tool": "greptile",
        "context_level": "diff-only",
        "comments": [],
        "metadata": {"tokens": 0, "cost_usd": 0.0, "time_seconds": 0.0},
        "dx": None,
    }
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(nr, sort_keys=False))

    resp = client.get("/metrics/run-2026-03-07")
    assert resp.status_code == 200
    # Cost section is only shown if any_cost is truthy
    assert b"Per Review" not in resp.data


# ---------------------------------------------------------------------------
# Metrics — DX summary
# ---------------------------------------------------------------------------


def test_metrics_detail_with_dx_summary(client, results_dir: Path) -> None:
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    nr = {
        "test_case_id": "leo-001",
        "tool": "greptile",
        "context_level": "diff-only",
        "comments": [],
        "metadata": {"tokens": 0, "cost_usd": 0.0, "time_seconds": 0.0},
        "dx": {
            "actionability": 4,
            "false_positive_burden": 3,
            "integration_friction": 2,
            "response_latency": 5,
            "notes": "solid",
        },
    }
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(nr, sort_keys=False))

    resp = client.get("/metrics/run-2026-03-07")
    assert resp.status_code == 200
    assert b"DX Assessment" in resp.data
    assert b"greptile" in resp.data


# ---------------------------------------------------------------------------
# Index — pipeline status with run dir
# ---------------------------------------------------------------------------


def test_index_shows_pipeline_run(client, results_dir: Path) -> None:
    import json as _json

    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()
    (run_dir / "run_metadata.json").write_text(
        _json.dumps({"tools": ["greptile"], "context_level": "diff-only"})
    )
    raw_dir = run_dir / "raw" / "leo-001-greptile-diff-only"
    raw_dir.mkdir(parents=True)
    (raw_dir / "metadata.json").write_text(_json.dumps({"time_seconds": 1.0}))
    nr = {
        "test_case_id": "leo-001",
        "tool": "greptile",
        "context_level": "diff-only",
        "comments": [],
        "metadata": {"tokens": 0, "cost_usd": 0.0, "time_seconds": 0.0},
        "dx": None,
    }
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(nr, sort_keys=False))

    # Index is now a JS shell; verify run appears via the API
    resp = client.get("/api/experiments")
    assert resp.status_code == 200
    data = resp.get_json()
    names = [r["name"] for r in data["ungrouped"]]
    assert "run-2026-03-07" in names
    r = [r for r in data["ungrouped"] if r["name"] == "run-2026-03-07"][0]
    assert r["normalized_count"] == 1


def test_index_pipeline_status_normalize_count(client, results_dir: Path) -> None:
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()
    nr = {
        "test_case_id": "leo-001",
        "tool": "greptile",
        "context_level": "diff-only",
        "comments": [],
        "metadata": {"tokens": 0, "cost_usd": 0.0, "time_seconds": 0.0},
        "dx": None,
    }
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(nr, sort_keys=False))

    # Verify via API instead of server-rendered HTML
    resp = client.get("/api/experiments")
    data = resp.get_json()
    r = data["ungrouped"][0]
    assert r["normalized_count"] == 1


# ---------------------------------------------------------------------------
# Human judge — page with scores
# ---------------------------------------------------------------------------


def test_human_judge_page_with_run_scores(client, results_dir: Path) -> None:
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)

    resp = client.get("/human-judge?run=run-2026-03-07")
    assert resp.status_code == 200
    assert b"run-2026-03-07" in resp.data
    assert b"leo-001" in resp.data
    assert b"greptile" in resp.data


def test_human_judge_page_no_scores_shows_message(client, results_dir: Path) -> None:
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    resp = client.get("/human-judge?run=run-2026-03-07")
    assert resp.status_code == 200
    assert b"No judge scores found" in resp.data


# ---------------------------------------------------------------------------
# Metrics — run selector renders runs
# ---------------------------------------------------------------------------


def test_metrics_run_selector_lists_runs(client, results_dir: Path) -> None:
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"run-2026-03-07" in resp.data


# ---------------------------------------------------------------------------
# Report viewer
# ---------------------------------------------------------------------------


def test_report_view_404_for_unknown_run(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/metrics/nonexistent-run/report")
    assert resp.status_code == 404


def test_report_view_no_analysis_shows_help(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    resp = client.get("/metrics/run-2026-03-07/report")
    assert resp.status_code == 200
    assert b"No analysis report found" in resp.data
    assert b"uv run bugeval analyze" in resp.data


def test_report_view_renders_markdown(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "report.md").write_text("## Results\n\n**bold** text here")

    resp = client.get("/metrics/run-2026-03-07/report")
    assert resp.status_code == 200
    assert b"<h2>Results</h2>" in resp.data
    assert b"<strong>bold</strong>" in resp.data


# ---------------------------------------------------------------------------
# Chart serving
# ---------------------------------------------------------------------------


def test_serve_chart_returns_png(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "catch_rate.png").write_bytes(b"\x89PNG fake")

    resp = client.get("/metrics/run-2026-03-07/chart/catch_rate.png")
    assert resp.status_code == 200
    assert resp.content_type == "image/png"


def test_serve_chart_404_for_disallowed_file(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "secret.txt").write_text("nope")

    resp = client.get("/metrics/run-2026-03-07/chart/secret.txt")
    assert resp.status_code == 404


def test_serve_chart_404_for_missing_file(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    (run_dir / "analysis").mkdir(parents=True)

    resp = client.get("/metrics/run-2026-03-07/chart/catch_rate.png")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Scores list view
# ---------------------------------------------------------------------------

_NR_YAML = {
    "test_case_id": "leo-001",
    "tool": "greptile",
    "context_level": "diff-only",
    "comments": [{"body": "looks buggy", "file": "src/main.rs", "line": 42}],
    "metadata": {"tokens": 0, "cost_usd": 0.0, "time_seconds": 0.0},
    "dx": None,
}


def test_scores_list_view_404_for_unknown_run(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/metrics/nonexistent/scores?tool=greptile")
    assert resp.status_code == 404


def test_scores_list_view_renders(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(_NR_YAML, sort_keys=False))

    resp = client.get("/metrics/run-2026-03-07/scores?tool=greptile")
    assert resp.status_code == 200
    assert b"leo-001" in resp.data
    assert b"greptile" in resp.data


def test_scores_list_view_filter_by_score(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(_NR_YAML, sort_keys=False))

    # Score=2 matches
    resp = client.get("/metrics/run-2026-03-07/scores?tool=greptile&score=2")
    assert resp.status_code == 200
    assert b"leo-001" in resp.data

    # Score=0 filters it out
    resp = client.get("/metrics/run-2026-03-07/scores?tool=greptile&score=0")
    assert resp.status_code == 200
    assert b"leo-001" not in resp.data


def test_scores_list_view_defaults_to_first_tool(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)

    resp = client.get("/metrics/run-2026-03-07/scores")
    assert resp.status_code == 200
    assert b"greptile" in resp.data


# ---------------------------------------------------------------------------
# Score detail view
# ---------------------------------------------------------------------------


def test_score_detail_view_renders(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(_NR_YAML, sort_keys=False))

    resp = client.get("/metrics/run-2026-03-07/scores/leo-001/greptile")
    assert resp.status_code == 200
    assert b"leo-001" in resp.data
    assert b"greptile" in resp.data
    assert b"correct" in resp.data  # reasoning from score


def test_score_detail_view_404_for_unknown_case(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    resp = client.get("/metrics/run-2026-03-07/scores/nonexistent/greptile")
    assert resp.status_code == 404


def test_score_detail_view_404_for_unknown_run(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/metrics/nonexistent/scores/leo-001/greptile")
    assert resp.status_code == 404


def test_score_detail_shows_expected_findings(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(_NR_YAML, sort_keys=False))

    resp = client.get("/metrics/run-2026-03-07/scores/leo-001/greptile")
    assert resp.status_code == 200
    assert b"off-by-one" in resp.data  # expected finding from _make_case
    assert b"src/main.rs" in resp.data


# ---------------------------------------------------------------------------
# Dataset inspector
# ---------------------------------------------------------------------------


def test_dataset_inspector_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/dataset")
    assert resp.status_code == 200
    assert b"Dataset Inspector" in resp.data
    # Now a thin HTML shell — stats loaded via JS
    assert b"/api/dataset/stats" in resp.data


def test_dataset_inspector_references_findings_api(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/dataset")
    assert resp.status_code == 200
    assert b"/api/dataset/findings" in resp.data


# ---------------------------------------------------------------------------
# Compare runs
# ---------------------------------------------------------------------------


def test_compare_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/compare")
    assert resp.status_code == 200
    assert b"Compare Runs" in resp.data


def test_compare_shows_selection_form(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    (results_dir / "run-a").mkdir()
    (results_dir / "run-b").mkdir()

    resp = client.get("/compare")
    assert resp.status_code == 200
    assert b"run-a" in resp.data
    assert b"run-b" in resp.data


def test_compare_with_two_runs(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    for run_name in ("run-a", "run-b"):
        run_dir = results_dir / run_name
        _write_score(run_dir)

    resp = client.get("/compare?runs=run-a&runs=run-b")
    assert resp.status_code == 200
    assert b"Detection Rate" in resp.data
    assert b"greptile" in resp.data


def test_compare_with_one_run_shows_message(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    (results_dir / "run-a").mkdir()
    (results_dir / "run-b").mkdir()

    resp = client.get("/compare?runs=run-a")
    assert resp.status_code == 200
    assert b"Select at least 2 runs" in resp.data


def test_compare_comma_separated_runs(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    for run_name in ("run-a", "run-b"):
        run_dir = results_dir / run_name
        _write_score(run_dir)

    resp = client.get("/compare?runs=run-a,run-b")
    assert resp.status_code == 200
    assert b"Detection Rate" in resp.data


# ---------------------------------------------------------------------------
# Alignment in case list
# ---------------------------------------------------------------------------


def test_case_list_returns_html_shell(client, cases_dir: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/cases")
    assert resp.status_code == 200
    assert b"Cases" in resp.data
    assert b"/api/cases" in resp.data


# ---------------------------------------------------------------------------
# Alignment in case detail
# ---------------------------------------------------------------------------


def test_case_detail_shows_alignment_column(client) -> None:  # type: ignore[no-untyped-def]
    with patch("bugeval.dashboard.fetch_diff", return_value="--- test\n+diff"):
        resp = client.get("/cases/leo-001")
    assert resp.status_code == 200
    assert b"Alignment" in resp.data  # table header


# ---------------------------------------------------------------------------
# Alignment with patches_dir
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_with_patches(cases_dir: Path, results_dir: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    # Write a patch that aligns with the expected finding (src/main.rs line 42)
    patch_text = "diff --git a/src/main.rs b/src/main.rs\n@@ -40,3 +40,4 @@\n context\n+added\n"
    (patches_dir / "leo-001.patch").write_text(patch_text)

    app = create_app(cases_dir, results_dir, patches_dir)
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client_with_patches(app_with_patches):  # type: ignore[no-untyped-def]
    return app_with_patches.test_client()


def test_index_shows_total_cases(client_with_patches) -> None:  # type: ignore[no-untyped-def]
    resp = client_with_patches.get("/")
    assert resp.status_code == 200
    assert b"Total Cases" in resp.data


def test_case_list_html_from_patches(client_with_patches) -> None:  # type: ignore[no-untyped-def]
    resp = client_with_patches.get("/cases")
    assert resp.status_code == 200
    assert b"Cases" in resp.data


def test_case_detail_finding_alignment_from_patches(client_with_patches) -> None:  # type: ignore[no-untyped-def]
    with patch("bugeval.dashboard.fetch_diff", return_value="--- test\n+diff"):
        resp = client_with_patches.get("/cases/leo-001")
    assert resp.status_code == 200
    assert b"badge-aligned" in resp.data


def test_dataset_alignment_from_patches(client_with_patches) -> None:  # type: ignore[no-untyped-def]
    # Alignment is now returned via the stats API, not server-rendered
    data = client_with_patches.get("/api/dataset/stats").get_json()
    assert data["alignment"] is not None
    assert data["alignment"]["aligned"] >= 0


# ---------------------------------------------------------------------------
# CLI --patches-dir option
# ---------------------------------------------------------------------------


def test_dashboard_cli_has_patches_dir_option() -> None:
    from click.testing import CliRunner

    from bugeval.dashboard import dashboard

    runner = CliRunner()
    result = runner.invoke(dashboard, ["--help"])
    assert result.exit_code == 0
    assert "--patches-dir" in result.output


# ---------------------------------------------------------------------------
# Metrics grouped agg
# ---------------------------------------------------------------------------


def test_metrics_detail_shows_runner_groups(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    nr = {
        "test_case_id": "leo-001",
        "tool": "greptile",
        "context_level": "diff-only",
        "comments": [],
        "metadata": {"tokens": 0, "cost_usd": 0.0, "time_seconds": 0.0},
        "dx": None,
    }
    (run_dir / "leo-001--greptile.yaml").write_text(yaml.safe_dump(nr, sort_keys=False))

    resp = client.get("/metrics/run-2026-03-07")
    assert resp.status_code == 200
    assert b"Commercial" in resp.data  # greptile is classified as Commercial


def test_metrics_detail_has_report_button_when_analysis_exists(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    (run_dir / "analysis").mkdir()

    resp = client.get("/metrics/run-2026-03-07")
    assert resp.status_code == 200
    assert b"View Report" in resp.data


def test_metrics_detail_no_report_button_without_analysis(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    _write_score(run_dir)
    # No analysis dir

    resp = client.get("/metrics/run-2026-03-07")
    assert resp.status_code == 200
    assert b"View Report" not in resp.data


# ---------------------------------------------------------------------------
# /api/cases endpoint
# ---------------------------------------------------------------------------


def test_api_cases_returns_json(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "cases" in data
    assert "total" in data
    assert "page" in data
    assert "pages" in data
    assert "filters" in data
    assert data["total"] == 1
    assert data["cases"][0]["id"] == "leo-001"


def test_api_cases_response_shape(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases")
    data = resp.get_json()
    c = data["cases"][0]
    assert "id" in c
    assert "repo" in c
    assert "category" in c
    assert "difficulty" in c
    assert "severity" in c
    assert "pr_size" in c
    assert "language" in c
    assert "description" in c
    assert "findings_count" in c
    assert "verified" in c
    assert "needs_manual_review" in c
    assert "reviewed" in c


def test_api_cases_filters_field(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases")
    data = resp.get_json()
    f = data["filters"]
    assert "repos" in f
    assert "categories" in f
    assert "difficulties" in f
    assert "severities" in f
    assert "leo" in f["repos"]


def test_api_cases_filter_by_category(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?category=logic")
    data = resp.get_json()
    assert data["total"] == 1

    resp = client.get("/api/cases?category=memory")
    data = resp.get_json()
    assert data["total"] == 0


def test_api_cases_filter_by_difficulty(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?difficulty=medium")
    data = resp.get_json()
    assert data["total"] == 1

    resp = client.get("/api/cases?difficulty=hard")
    data = resp.get_json()
    assert data["total"] == 0


def test_api_cases_filter_by_severity(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?severity=high")
    data = resp.get_json()
    assert data["total"] == 1

    resp = client.get("/api/cases?severity=low")
    data = resp.get_json()
    assert data["total"] == 0


def test_api_cases_filter_needs_manual_review(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?needs_manual_review=true")
    data = resp.get_json()
    assert data["total"] == 1

    resp = client.get("/api/cases?needs_manual_review=false")
    data = resp.get_json()
    assert data["total"] == 0


def test_api_cases_filter_reviewed(client, cases_dir: Path) -> None:  # type: ignore[no-untyped-def]
    # Initially not reviewed
    resp = client.get("/api/cases?reviewed=false")
    data = resp.get_json()
    assert data["total"] == 1

    resp = client.get("/api/cases?reviewed=true")
    data = resp.get_json()
    assert data["total"] == 0

    # Mark reviewed
    mark_reviewed(cases_dir, _make_case(), reviewer="tester")
    resp = client.get("/api/cases?reviewed=true")
    data = resp.get_json()
    assert data["total"] == 1


def test_api_cases_text_search(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?q=test+case")
    data = resp.get_json()
    assert data["total"] == 1  # description contains "A test case"

    resp = client.get("/api/cases?q=off-by-one")
    data = resp.get_json()
    assert data["total"] == 1  # expected_findings summary

    resp = client.get("/api/cases?q=nonexistent+query")
    data = resp.get_json()
    assert data["total"] == 0


def test_api_cases_text_search_case_insensitive(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?q=TEST+CASE")
    data = resp.get_json()
    assert data["total"] == 1


def test_api_cases_per_page(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?per_page=1")
    data = resp.get_json()
    assert len(data["cases"]) == 1
    assert data["pages"] == 1  # only 1 case total


def test_api_cases_per_page_clamped(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?per_page=999")
    data = resp.get_json()
    # per_page is clamped to 200; still returns all 1 case
    assert data["total"] == 1


def test_api_cases_combined_filters(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/cases?repo=leo&category=logic&difficulty=medium")
    data = resp.get_json()
    assert data["total"] == 1

    resp = client.get("/api/cases?repo=leo&category=memory")
    data = resp.get_json()
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/dataset/stats
# ---------------------------------------------------------------------------


def test_api_dataset_stats_returns_json(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/dataset/stats")
    assert resp.status_code == 200
    assert resp.content_type == "application/json"


def test_api_dataset_stats_shape(client) -> None:  # type: ignore[no-untyped-def]
    data = client.get("/api/dataset/stats").get_json()
    assert data["total"] == 1
    assert data["verified"] == 0
    assert data["needs_review"] == 1
    assert isinstance(data["avg_findings"], float)
    assert "distributions" in data
    dists = data["distributions"]
    for field in (
        "category",
        "difficulty",
        "severity",
        "repo",
        "pr_size",
        "language",
        "visibility",
    ):
        assert field in dists, f"missing distribution: {field}"
    assert "alignment" in data


def test_api_dataset_stats_distribution_values(client) -> None:  # type: ignore[no-untyped-def]
    data = client.get("/api/dataset/stats").get_json()
    assert data["distributions"]["category"]["logic"] == 1
    assert data["distributions"]["difficulty"]["medium"] == 1
    assert data["distributions"]["severity"]["high"] == 1


def test_api_dataset_stats_alignment_null_without_patches(
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    # Use an explicit non-existent patches dir to guarantee null alignment
    c_dir = tmp_path / "cases" / "final" / "leo"
    c_dir.mkdir(parents=True)
    case = _make_case()
    (c_dir / "leo-001.yaml").write_text(
        yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False)
    )
    r_dir = tmp_path / "results"
    r_dir.mkdir()
    p_dir = tmp_path / "no_patches"  # does not exist
    a = create_app(c_dir.parent, r_dir, p_dir)
    a.config["TESTING"] = True
    data = a.test_client().get("/api/dataset/stats").get_json()
    assert data["alignment"] is None


def test_api_dataset_stats_alignment_with_patches(
    client_with_patches,
) -> None:  # type: ignore[no-untyped-def]
    data = client_with_patches.get("/api/dataset/stats").get_json()
    assert data["alignment"] is not None
    assert "aligned" in data["alignment"]
    assert "file_only" in data["alignment"]
    assert "misaligned" in data["alignment"]


def test_api_dataset_stats_flagged_count(client) -> None:  # type: ignore[no-untyped-def]
    data = client.get("/api/dataset/stats").get_json()
    assert "flagged" in data
    assert isinstance(data["flagged"], int)


# ---------------------------------------------------------------------------
# GET /api/dataset/findings
# ---------------------------------------------------------------------------


def test_api_dataset_findings_returns_json(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/dataset/findings")
    assert resp.status_code == 200
    assert resp.content_type == "application/json"


def test_api_dataset_findings_shape(client) -> None:  # type: ignore[no-untyped-def]
    data = client.get("/api/dataset/findings").get_json()
    assert "findings" in data
    assert "total" in data
    assert "page" in data
    assert "pages" in data
    assert data["total"] == 1
    f = data["findings"][0]
    assert f["case_id"] == "leo-001"
    assert f["file"] == "src/main.rs"
    assert f["line"] == 42
    assert "summary" in f
    assert "repo" in f


def test_api_dataset_findings_pagination(
    client,
    cases_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    # Add a second case with 2 findings
    repo_dir = cases_dir / "leo"
    case2 = _make_case(case_id="leo-002")
    (repo_dir / "leo-002.yaml").write_text(
        yaml.safe_dump(case2.model_dump(mode="json"), sort_keys=False)
    )
    _invalidate_cache()
    data = client.get("/api/dataset/findings?per_page=1&page=1").get_json()
    assert data["page"] == 1
    assert data["pages"] == 2
    assert len(data["findings"]) == 1

    data2 = client.get("/api/dataset/findings?per_page=1&page=2").get_json()
    assert data2["page"] == 2
    assert len(data2["findings"]) == 1


def test_api_dataset_findings_filter_by_repo(client) -> None:  # type: ignore[no-untyped-def]
    data = client.get("/api/dataset/findings?repo=leo").get_json()
    assert data["total"] == 1

    data2 = client.get("/api/dataset/findings?repo=nonexistent").get_json()
    assert data2["total"] == 0


# ---------------------------------------------------------------------------
# /dataset page (thin HTML shell)
# ---------------------------------------------------------------------------


def test_dataset_page_returns_html_shell(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/dataset")
    assert resp.status_code == 200
    assert b"Dataset Inspector" in resp.data
    assert b"/api/dataset/stats" in resp.data
    assert b"/api/dataset/findings" in resp.data


# ---------------------------------------------------------------------------
# Experiment API
# ---------------------------------------------------------------------------


def test_api_experiments_returns_json(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/experiments")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "experiments" in data
    assert "ungrouped" in data


def test_api_experiments_ungrouped_lists_runs(
    client,
    results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-test-1"
    run_dir.mkdir()
    resp = client.get("/api/experiments")
    data = resp.get_json()
    names = [r["name"] for r in data["ungrouped"]]
    assert "run-test-1" in names


def test_api_create_experiment(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(
        "/api/experiments",
        json={"name": "Baseline v3", "notes": "First run"},
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["id"] == "baseline-v3"
    assert data["name"] == "Baseline v3"
    assert data["notes"] == "First run"
    assert data["created"] != ""


def test_api_create_experiment_missing_name(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post("/api/experiments", json={})
    assert resp.status_code == 400


def test_api_create_experiment_duplicate(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/experiments", json={"name": "Dup"})
    resp = client.post("/api/experiments", json={"name": "Dup"})
    assert resp.status_code == 409


def test_api_update_experiment(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/experiments", json={"name": "Update Me"})
    resp = client.put(
        "/api/experiments/update-me",
        json={"name": "Updated", "notes": "new notes"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Updated"
    assert data["notes"] == "new notes"


def test_api_update_experiment_not_found(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.put(
        "/api/experiments/nonexistent",
        json={"name": "X"},
    )
    assert resp.status_code == 404


def test_api_archive_experiment(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/experiments", json={"name": "Archive Me"})
    resp = client.post("/api/experiments/archive-me/archive")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "archived"

    # Toggle back
    resp = client.post("/api/experiments/archive-me/archive")
    data = resp.get_json()
    assert data["status"] == "active"


def test_api_archive_experiment_not_found(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post("/api/experiments/nonexistent/archive")
    assert resp.status_code == 404


def test_api_experiments_with_runs(
    client,
    results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-abc"
    run_dir.mkdir()
    (run_dir / "raw").mkdir()
    client.post(
        "/api/experiments",
        json={"name": "With Runs", "runs": ["run-abc"]},
    )
    resp = client.get("/api/experiments")
    data = resp.get_json()
    exp = data["experiments"][0]
    assert len(exp["runs"]) == 1
    assert exp["runs"][0]["name"] == "run-abc"
    # run-abc should not appear in ungrouped
    ungrouped_names = [r["name"] for r in data["ungrouped"]]
    assert "run-abc" not in ungrouped_names


def test_api_experiments_run_summary_counts(
    client,
    results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-counts"
    run_dir.mkdir()
    raw = run_dir / "raw"
    raw.mkdir()
    (raw / "case-a").mkdir()
    (raw / "case-b").mkdir()
    # Write a normalized yaml
    (run_dir / "leo-001-greptile.yaml").write_text("test_case_id: leo-001\n")
    # Write a scored yaml
    scores = run_dir / "scores"
    scores.mkdir()
    (scores / "leo-001-greptile.yaml").write_text("score: 2\n")
    # Create analysis dir
    (run_dir / "analysis").mkdir()

    resp = client.get("/api/experiments")
    data = resp.get_json()
    r = data["ungrouped"][0]
    assert r["name"] == "run-counts"
    assert r["raw_count"] == 2
    assert r["normalized_count"] == 1
    assert r["scored_count"] == 1
    assert r["has_analysis"] is True


# ---------------------------------------------------------------------------
# Mode 1: Dataset Review — verify, flag, findings, notes
# ---------------------------------------------------------------------------


def test_verify_case_sets_verified(client, cases_dir: Path) -> None:
    _invalidate_cache()
    resp = client.post("/api/cases/leo-001/verify", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["verified"] is True

    # Check YAML was updated
    saved = yaml.safe_load((cases_dir / "leo" / "leo-001.yaml").read_text())
    assert saved["verified"] is True


def test_verify_case_404_for_unknown(client) -> None:
    resp = client.post("/api/cases/nonexistent/verify", json={})
    assert resp.status_code == 404


def test_flag_case_appends_flag(client, cases_dir: Path) -> None:
    _invalidate_cache()
    resp = client.post(
        "/api/cases/leo-001/flag",
        json={"flag": "weak-ground-truth", "note": "findings seem weak"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "weak-ground-truth" in data["quality_flags"]
    assert "findings seem weak" in data["reviewer_notes"]

    saved = yaml.safe_load((cases_dir / "leo" / "leo-001.yaml").read_text())
    assert "weak-ground-truth" in saved["quality_flags"]
    assert "findings seem weak" in saved["reviewer_notes"]


def test_flag_case_without_note(client, cases_dir: Path) -> None:
    _invalidate_cache()
    resp = client.post(
        "/api/cases/leo-001/flag",
        json={"flag": "unclear-description"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "unclear-description" in data["quality_flags"]


def test_flag_case_missing_flag_field(client) -> None:
    resp = client.post("/api/cases/leo-001/flag", json={})
    assert resp.status_code == 400


def test_flag_case_404_for_unknown(client) -> None:
    resp = client.post("/api/cases/nonexistent/flag", json={"flag": "x"})
    assert resp.status_code == 404


def test_update_findings(client, cases_dir: Path) -> None:
    _invalidate_cache()
    new_findings = [
        {"file": "src/lib.rs", "line": 10, "summary": "null deref"},
    ]
    resp = client.put(
        "/api/cases/leo-001/findings",
        json={"findings": new_findings},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["expected_findings"]) == 1
    assert data["expected_findings"][0]["file"] == "src/lib.rs"

    saved = yaml.safe_load((cases_dir / "leo" / "leo-001.yaml").read_text())
    assert saved["expected_findings"][0]["file"] == "src/lib.rs"


def test_update_findings_missing_field(client) -> None:
    resp = client.put("/api/cases/leo-001/findings", json={})
    assert resp.status_code == 400


def test_update_findings_404_for_unknown(client) -> None:
    resp = client.put(
        "/api/cases/nonexistent/findings",
        json={"findings": []},
    )
    assert resp.status_code == 404


def test_update_notes(client, cases_dir: Path) -> None:
    _invalidate_cache()
    resp = client.put(
        "/api/cases/leo-001/notes",
        json={"notes": ["note 1", "note 2"]},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reviewer_notes"] == ["note 1", "note 2"]

    saved = yaml.safe_load((cases_dir / "leo" / "leo-001.yaml").read_text())
    assert saved["reviewer_notes"] == ["note 1", "note 2"]


def test_update_notes_missing_field(client) -> None:
    resp = client.put("/api/cases/leo-001/notes", json={})
    assert resp.status_code == 400


def test_update_notes_404_for_unknown(client) -> None:
    resp = client.put(
        "/api/cases/nonexistent/notes",
        json={"notes": []},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Mode 2: Score Review API
# ---------------------------------------------------------------------------


def _setup_run_with_scores(results_dir: Path) -> Path:
    """Create a run with scores and normalized results for testing."""
    run_dir = results_dir / "run-2026-03-07"
    scores_dir = run_dir / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)

    for tool in ("claude-cli-sonnet", "claude-cli-haiku"):
        score = {
            "test_case_id": "leo-001",
            "tool": tool,
            "score": 2 if tool == "claude-cli-sonnet" else 0,
            "votes": [2, 2, 3] if tool == "claude-cli-sonnet" else [0, 0, 0],
            "reasoning": f"Analysis by {tool}",
            "comment_judgments": [
                {"id": 1, "classification": "TP", "relevance": "direct"},
                {"id": 2, "classification": "FP", "relevance": "unrelated"},
            ],
            "noise": {"total_comments": 2, "true_positives": 1, "snr": 0.5},
        }
        (scores_dir / f"leo-001--{tool}.yaml").write_text(
            yaml.safe_dump(score, sort_keys=False)
        )

        nr = {
            "test_case_id": "leo-001",
            "tool": tool,
            "context_level": "diff-only",
            "comments": [
                {"body": "found bug", "file": "src/main.rs", "line": 42},
                {"body": "style nit", "file": "src/main.rs", "line": 100},
            ],
            "metadata": {"tokens": 500, "cost_usd": 0.05, "time_seconds": 1.5},
            "dx": None,
        }
        (run_dir / f"leo-001--{tool}.yaml").write_text(
            yaml.safe_dump(nr, sort_keys=False)
        )
    return run_dir


def test_api_scores_returns_paginated_list(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get("/api/runs/run-2026-03-07/scores")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "scores" in data
    assert "total" in data
    assert "page" in data
    assert "pages" in data
    assert "tools" in data
    assert data["total"] >= 1


def test_api_scores_filter_by_tool(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get(
        "/api/runs/run-2026-03-07/scores?tool=claude-cli-sonnet"
    )
    data = resp.get_json()
    for s in data["scores"]:
        assert s["tool"] == "claude-cli-sonnet"


def test_api_scores_filter_by_score(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get("/api/runs/run-2026-03-07/scores?score=0")
    data = resp.get_json()
    for s in data["scores"]:
        assert s["score"] == 0


def test_api_scores_filter_disagreement(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get(
        "/api/runs/run-2026-03-07/scores?disagreement=true"
    )
    data = resp.get_json()
    # Only sonnet has vote spread (3-2=1), which is < 2, so none match
    assert data["total"] == 0


def test_api_scores_pagination(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get(
        "/api/runs/run-2026-03-07/scores?per_page=1&page=1"
    )
    data = resp.get_json()
    assert len(data["scores"]) == 1
    assert data["pages"] >= 2


def test_api_scores_404_for_unknown_run(client) -> None:
    resp = client.get("/api/runs/nonexistent/scores")
    assert resp.status_code == 404


def test_api_scores_entry_shape(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get("/api/runs/run-2026-03-07/scores")
    data = resp.get_json()
    s = data["scores"][0]
    assert "case_id" in s
    assert "tool" in s
    assert "score" in s
    assert "reasoning" in s
    assert "votes" in s
    assert "comment_count" in s
    assert "tp_count" in s
    assert "fp_count" in s


def test_human_score_override(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _setup_run_with_scores(results_dir)
    resp = client.post(
        "/api/runs/run-2026-03-07/scores/leo-001/claude-cli-sonnet/human",
        json={"score": 3, "notes": "Judge was wrong"},
    )
    assert resp.status_code == 200

    hj_path = (
        results_dir / "run-2026-03-07" / "human_judge"
        / "leo-001-claude-cli-sonnet.yaml"
    )
    assert hj_path.exists()
    saved = yaml.safe_load(hj_path.read_text())
    assert saved["human_score"] == 3
    assert saved["notes"] == "Judge was wrong"


def test_human_score_override_invalid_score(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _setup_run_with_scores(results_dir)
    resp = client.post(
        "/api/runs/run-2026-03-07/scores/leo-001/claude-cli-sonnet/human",
        json={"score": 5},
    )
    assert resp.status_code == 400


def test_human_score_override_missing_score(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _setup_run_with_scores(results_dir)
    resp = client.post(
        "/api/runs/run-2026-03-07/scores/leo-001/claude-cli-sonnet/human",
        json={},
    )
    assert resp.status_code == 400


def test_human_score_override_404_for_unknown_run(client) -> None:
    resp = client.post(
        "/api/runs/nonexistent/scores/leo-001/tool/human",
        json={"score": 2},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Mode 2: Score Review page
# ---------------------------------------------------------------------------


def test_score_review_page_returns_200(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _setup_run_with_scores(results_dir)
    resp = client.get("/metrics/run-2026-03-07/review")
    assert resp.status_code == 200
    assert b"Score Review" in resp.data


def test_score_review_page_404_for_unknown_run(client) -> None:
    resp = client.get("/metrics/nonexistent/review")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Mode 3: Model Comparison API
# ---------------------------------------------------------------------------


def test_api_compare_case_returns_tools(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get(
        "/api/runs/run-2026-03-07/compare/leo-001"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "case" in data
    assert "tools" in data
    assert data["case"]["id"] == "leo-001"
    tool_names = [t["tool"] for t in data["tools"]]
    assert "claude-cli-sonnet" in tool_names
    assert "claude-cli-haiku" in tool_names


def test_api_compare_case_tool_shape(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get(
        "/api/runs/run-2026-03-07/compare/leo-001"
    )
    data = resp.get_json()
    t = data["tools"][0]
    assert "tool" in t
    assert "score" in t
    assert "reasoning" in t
    assert "findings" in t
    assert "comment_count" in t
    assert "tp_count" in t
    assert "fp_count" in t


def test_api_compare_case_includes_expected_findings(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get(
        "/api/runs/run-2026-03-07/compare/leo-001"
    )
    data = resp.get_json()
    ef = data["case"]["expected_findings"]
    assert len(ef) >= 1
    assert ef[0]["file"] == "src/main.rs"


def test_api_compare_case_404_for_unknown_run(client) -> None:
    resp = client.get("/api/runs/nonexistent/compare/leo-001")
    assert resp.status_code == 404


def test_api_compare_case_404_for_unknown_case(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _setup_run_with_scores(results_dir)
    resp = client.get(
        "/api/runs/run-2026-03-07/compare/nonexistent"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Mode 3: Model Comparison page
# ---------------------------------------------------------------------------


def test_model_compare_page_returns_200(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    _setup_run_with_scores(results_dir)
    resp = client.get("/metrics/run-2026-03-07/compare/leo-001")
    assert resp.status_code == 200
    assert b"Model Comparison" in resp.data


def test_model_compare_page_404_for_unknown_run(client) -> None:
    resp = client.get("/metrics/nonexistent/compare/leo-001")
    assert resp.status_code == 404


def test_model_compare_page_404_for_unknown_case(
    client, results_dir: Path,
) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir(exist_ok=True)
    resp = client.get(
        "/metrics/run-2026-03-07/compare/nonexistent"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Runs page
# ---------------------------------------------------------------------------


def test_runs_page_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert b"Runs" in resp.data


def test_run_detail_returns_200(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()
    resp = client.get("/runs/run-2026-03-07")
    assert resp.status_code == 200
    assert b"run-2026-03-07" in resp.data


def test_run_detail_404_for_unknown(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/runs/nonexistent")
    assert resp.status_code == 404


def test_run_detail_shows_notes(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    from bugeval.dashboard_models import add_run_note

    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()
    add_run_note(run_dir, "Test note content")

    resp = client.get("/runs/run-2026-03-07")
    assert resp.status_code == 200
    assert b"Test note content" in resp.data


def test_add_run_note_post(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    resp = client.post(
        "/runs/run-2026-03-07/notes",
        data={"text": "New note"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    from bugeval.dashboard_models import load_run_notes

    notes = load_run_notes(run_dir)
    assert len(notes) == 1
    assert notes[0].text == "New note"


def test_add_run_note_empty_text_ignored(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir()

    resp = client.post(
        "/runs/run-2026-03-07/notes",
        data={"text": "  "},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    from bugeval.dashboard_models import load_run_notes

    notes = load_run_notes(run_dir)
    assert len(notes) == 0


# ---------------------------------------------------------------------------
# Golden set page
# ---------------------------------------------------------------------------


def test_golden_page_returns_200(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/golden")
    assert resp.status_code == 200
    assert b"Golden Set" in resp.data


def test_golden_page_shows_cases(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/golden")
    assert resp.status_code == 200
    assert b"leo-001" in resp.data
    assert b"unreviewed" in resp.data


def test_golden_set_status_confirm(client, cases_dir: Path) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(
        "/golden/leo-001",
        data={"status": "confirmed"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    from bugeval.dashboard_models import load_golden_set

    golden = load_golden_set(cases_dir)
    assert golden["leo-001"].status == "confirmed"


def test_golden_set_status_dispute(client, cases_dir: Path) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(
        "/golden/leo-001",
        data={"status": "disputed"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    from bugeval.dashboard_models import load_golden_set

    golden = load_golden_set(cases_dir)
    assert golden["leo-001"].status == "disputed"


def test_golden_set_invalid_status(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(
        "/golden/leo-001",
        data={"status": "invalid"},
    )
    assert resp.status_code == 400


def test_golden_filter_by_status(client, cases_dir: Path) -> None:  # type: ignore[no-untyped-def]
    from bugeval.dashboard_models import set_golden_status

    set_golden_status(cases_dir, "leo-001", "confirmed")

    resp = client.get("/golden?status=confirmed")
    assert resp.status_code == 200
    assert b"leo-001" in resp.data

    resp = client.get("/golden?status=disputed")
    assert resp.status_code == 200
    assert b"leo-001" not in resp.data


def test_golden_filter_by_repo(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/golden?repo=leo")
    assert resp.status_code == 200
    # Case appears in the table
    assert b"/cases/leo-001" in resp.data

    resp = client.get("/golden?repo=nonexistent")
    assert resp.status_code == 200
    # Case should not appear in the table (but may appear in nav/links)
    assert b"/cases/leo-001" not in resp.data


def test_golden_coverage_stats(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/golden")
    assert resp.status_code == 200
    assert b"Coverage" in resp.data


# ---------------------------------------------------------------------------
# Human scoring page
# ---------------------------------------------------------------------------


def test_human_score_page_returns_200(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    # Without case/tool, redirects to first unscored
    resp = client.get("/score/run-2026-03-07", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Human Scoring" in resp.data


def test_human_score_page_404_for_unknown_run(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/score/nonexistent")
    assert resp.status_code == 404


def test_human_score_page_shows_case(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.get("/score/run-2026-03-07?case=leo-001&tool=claude-cli-sonnet")
    assert resp.status_code == 200
    assert b"leo-001" in resp.data
    assert b"Detection" in resp.data
    assert b"Quality" in resp.data


def test_human_score_submit(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    _setup_run_with_scores(results_dir)
    resp = client.post(
        "/score/run-2026-03-07",
        data={
            "case_id": "leo-001",
            "tool": "claude-cli-sonnet",
            "detection_score": "2",
            "review_quality": "3",
            "notes": "Good catch",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    from bugeval.dashboard_models import load_human_score

    score = load_human_score(results_dir / "run-2026-03-07", "leo-001", "claude-cli-sonnet")
    assert score is not None
    assert score.detection_score == 2
    assert score.review_quality == 3
    assert score.notes == "Good catch"


def test_human_score_submit_invalid_detection(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    _setup_run_with_scores(results_dir)
    resp = client.post(
        "/score/run-2026-03-07",
        data={
            "case_id": "leo-001",
            "tool": "claude-cli-sonnet",
            "detection_score": "5",
            "review_quality": "0",
        },
    )
    assert resp.status_code == 400


def test_human_score_submit_and_advance(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    _invalidate_cache()
    _setup_run_with_scores(results_dir)
    resp = client.post(
        "/score/run-2026-03-07",
        data={
            "case_id": "leo-001",
            "tool": "claude-cli-haiku",
            "detection_score": "1",
            "review_quality": "2",
            "advance": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Should redirect to next case/tool pair
    assert "score/run-2026-03-07" in resp.headers["Location"]


def test_human_score_page_no_scores_shows_message(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir = results_dir / "run-2026-03-07"
    run_dir.mkdir(exist_ok=True)

    resp = client.get("/score/run-2026-03-07")
    assert resp.status_code == 200
    assert b"No scored case/tool pairs" in resp.data


def test_human_score_blinding(client, results_dir: Path) -> None:  # type: ignore[no-untyped-def]
    """Blind labels are stable across page loads and shown in the pairs table."""
    _invalidate_cache()
    _setup_run_with_scores(results_dir)

    # Load the page twice — labels must be identical (stable ordering)
    resp1 = client.get("/score/run-2026-03-07?case=leo-001&tool=claude-cli-sonnet")
    assert resp1.status_code == 200
    resp2 = client.get("/score/run-2026-03-07?case=leo-001&tool=claude-cli-sonnet")
    assert resp2.status_code == 200

    # Alphabetical sort: claude-cli-haiku < claude-cli-sonnet → A, B
    assert b"Tool B" in resp1.data  # sonnet is second alphabetically
    assert b"Tool B" in resp2.data  # same label on second load

    # Verify the pairs table uses blind labels (not raw tool names)
    resp_haiku = client.get("/score/run-2026-03-07?case=leo-001&tool=claude-cli-haiku")
    assert resp_haiku.status_code == 200
    assert b"Tool A" in resp_haiku.data  # haiku is first alphabetically
