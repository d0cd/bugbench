"""Local Flask dashboard for experiment management."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import yaml
from flask import Flask, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError

from bugeval.analyze import aggregate_scores, compute_catch_rate, compute_snr
from bugeval.dashboard_helpers import (
    classify_runner_type,
    compute_comparison_data,
    compute_dataset_stats,
    group_agg_by_runner,
    load_alignment_for_cases,
    md_to_html,
)
from bugeval.dashboard_models import (
    Experiment,
    HumanScore,
    add_run_note,
    current_date_iso,
    load_experiments,
    load_golden_set,
    load_human_score,
    load_run_notes,
    save_experiments,
    save_human_score,
    set_golden_status,
    slugify,
)
from bugeval.human_judge import compute_kappa_report
from bugeval.judge_models import JudgeScore
from bugeval.models import (
    Category,
    Difficulty,
    ExpectedFinding,
    PRSize,
    Severity,
    TestCase,
    Visibility,
)
from bugeval.pr_eval_models import default_scoring
from bugeval.result_models import DxAssessment, NormalizedResult
from bugeval.validate_cases import AlignmentStatus, validate_case_alignment

# ---------------------------------------------------------------------------
# TTL cache for expensive loads
# ---------------------------------------------------------------------------

_TTL_SECONDS = 300  # cache invalidates after 5 minutes (cases are immutable during browsing)


class _CachedResult:
    def __init__(self, value: Any, expires: float):
        self.value = value
        self.expires = expires

    @property
    def valid(self) -> bool:
        return time.monotonic() < self.expires


_cache: dict[str, _CachedResult] = {}


def _cached(key: str, loader: Any) -> Any:
    entry = _cache.get(key)
    if entry and entry.valid:
        return entry.value
    value = loader()
    _cache[key] = _CachedResult(value, time.monotonic() + _TTL_SECONDS)
    return value


def _invalidate_cache(prefix: str = "") -> None:
    keys = [k for k in _cache if k.startswith(prefix)] if prefix else list(_cache)
    for k in keys:
        del _cache[k]


# ---------------------------------------------------------------------------
# Sidecar review-state helpers
# ---------------------------------------------------------------------------


def _sidecar_path(cases_dir: Path, repo: str) -> Path:
    return cases_dir / repo / ".review_state.json"


def load_review_state(cases_dir: Path, repo: str) -> dict[str, Any]:
    path = _sidecar_path(cases_dir, repo)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_review_state(cases_dir: Path, repo: str, state: dict[str, Any]) -> None:
    path = _sidecar_path(cases_dir, repo)
    path.write_text(json.dumps(state, indent=2, default=str))


def mark_reviewed(cases_dir: Path, case: TestCase, reviewer: str = "human") -> None:
    state = load_review_state(cases_dir, case.repo)
    state[case.id] = {
        "reviewed": True,
        "reviewer": reviewer,
        "timestamp": datetime.utcnow().isoformat(),
    }
    save_review_state(cases_dir, case.repo, state)


def is_reviewed(cases_dir: Path, case_id: str, repo: str) -> bool:
    state = load_review_state(cases_dir, repo)
    return bool(state.get(case_id, {}).get("reviewed", False))


def _batch_reviewed_ids(cases_dir: Path, cases: list[TestCase]) -> set[str]:
    """Load all review states once per repo, return set of reviewed case IDs."""
    loaded: dict[str, dict[str, Any]] = {}
    reviewed: set[str] = set()
    for case in cases:
        if case.repo not in loaded:
            loaded[case.repo] = load_review_state(cases_dir, case.repo)
        if loaded[case.repo].get(case.id, {}).get("reviewed", False):
            reviewed.add(case.id)
    return reviewed


# ---------------------------------------------------------------------------
# Case YAML helpers
# ---------------------------------------------------------------------------


def _find_case_yaml(cases_dir: Path, case_id: str) -> Path | None:
    # Try direct path construction first (case_id has repo prefix like "leo-042")
    for repo_dir in cases_dir.iterdir():
        if not repo_dir.is_dir():
            continue
        candidate = repo_dir / f"{case_id}.yaml"
        if candidate.exists():
            return candidate
    return None


def load_all_cases(cases_dir: Path) -> list[TestCase]:
    if not cases_dir.exists():
        return []
    cases = []
    for repo_dir in sorted(cases_dir.iterdir()):
        if not repo_dir.is_dir():
            continue
        for yaml_path in sorted(repo_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_path.read_text()) or {}
                cases.append(TestCase(**data))
            except (ValidationError, TypeError, yaml.YAMLError):
                pass
    return cases


def save_case(cases_dir: Path, case: TestCase) -> None:
    yaml_path = _find_case_yaml(cases_dir, case.id)
    if yaml_path is None:
        return
    yaml_path.write_text(yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False))


# ---------------------------------------------------------------------------
# Diff fetching
# ---------------------------------------------------------------------------


def fetch_diff(repo: str, head_commit: str) -> str:
    """Fetch commit diff via gh CLI. Returns diff text or error message."""
    owner_repo = repo if "/" in repo else f"provable-labs/{repo}"
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/commits/{head_commit}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return f"Error fetching diff: {result.stderr.strip()}"
        data = json.loads(result.stdout)
        files = data.get("files", [])
        parts = []
        for f in files:
            patch = f.get("patch", "")
            if patch:
                parts.append(f"--- {f['filename']}\n{patch}")
        return "\n\n".join(parts) or "(no diff available)"
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Pipeline progress helpers
# ---------------------------------------------------------------------------


def _run_dirs(results_dir: Path) -> list[Path]:
    if not results_dir.exists():
        return []
    return sorted([d for d in results_dir.iterdir() if d.is_dir() and d.name.startswith("run-")])


def _pipeline_status(run_dir: Path, total_cases: int) -> dict[str, Any]:
    raw_dir = run_dir / "raw"
    done_count = 0
    tools_seen: set[str] = set()
    if raw_dir.exists():
        for case_dir in raw_dir.iterdir():
            if not case_dir.is_dir():
                continue
            has_metadata = (case_dir / "metadata.json").exists()
            has_comments = (case_dir / "comments.json").exists()
            if has_metadata or has_comments:
                done_count += 1
            # Extract tool name from dir name: {case_id}-{tool}[-{context}]
            parts = case_dir.name.split("-")
            if len(parts) >= 2:
                # The tool name is everything after the case-id prefix
                # We can't perfectly parse this, but we can collect unique dir names
                tools_seen.add(case_dir.name)

    normalized_count = sum(1 for p in run_dir.glob("*.yaml") if p.name != "checkpoint.yaml")
    scores_dir = run_dir / "scores"
    judged_count = len(list(scores_dir.glob("*.yaml"))) if scores_dir.exists() else 0
    analysis_dir = run_dir / "analysis"
    has_analysis = analysis_dir.exists()

    # Extract unique tool names from run_metadata.json if available
    tools_run: list[str] = []
    meta_path = run_dir / "run_metadata.json"
    if meta_path.exists():
        import json

        try:
            meta = json.loads(meta_path.read_text())
            tools_run = sorted(meta.get("tools", []))
        except (json.JSONDecodeError, KeyError):
            pass

    return {
        "name": run_dir.name,
        "tools_run": tools_run,
        "normalized": normalized_count,
        "judged": judged_count,
        "has_analysis": has_analysis,
        "expected": total_cases * max(len(tools_run), 1),
        "checkpoint_entries": done_count,
    }


def _run_summary(run_dir: Path) -> dict[str, Any]:
    raw_dir = run_dir / "raw"
    raw_count = sum(1 for d in raw_dir.iterdir() if d.is_dir()) if raw_dir.exists() else 0
    normalized_count = sum(1 for p in run_dir.glob("*.yaml") if p.name != "checkpoint.yaml")
    scores_dir = run_dir / "scores"
    scored_count = len(list(scores_dir.glob("*.yaml"))) if scores_dir.exists() else 0
    has_analysis = (run_dir / "analysis").exists()
    return {
        "name": run_dir.name,
        "raw_count": raw_count,
        "normalized_count": normalized_count,
        "scored_count": scored_count,
        "has_analysis": has_analysis,
    }


# ---------------------------------------------------------------------------
# Score loading helpers
# ---------------------------------------------------------------------------


def _load_scores(run_dir: Path) -> list[JudgeScore]:
    return _cached(f"scores:{run_dir}", lambda: _load_scores_uncached(run_dir))


def _load_scores_uncached(run_dir: Path) -> list[JudgeScore]:
    scores_dir = run_dir / "scores"
    scores = []
    if not scores_dir.exists():
        return scores
    for path in sorted(scores_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        try:
            scores.append(JudgeScore(**data))
        except (ValidationError, TypeError):
            pass
    return scores


def _load_normalized(run_dir: Path) -> dict[tuple[str, str], NormalizedResult]:
    return _cached(
        f"normalized:{run_dir}",
        lambda: _load_normalized_uncached(run_dir),
    )


def _load_normalized_uncached(run_dir: Path) -> dict[tuple[str, str], NormalizedResult]:
    lookup: dict[tuple[str, str], NormalizedResult] = {}
    for path in run_dir.glob("*.yaml"):
        if path.name in ("checkpoint.yaml",):
            continue
        data = yaml.safe_load(path.read_text()) or {}
        try:
            r = NormalizedResult(**data)
            lookup[(r.test_case_id, r.tool)] = r
        except (ValidationError, TypeError):
            pass
    return lookup


def _load_single_score(run_dir: Path, case_id: str, tool: str) -> JudgeScore | None:
    safe_case = case_id.replace("/", "_")
    safe_tool = tool.replace("/", "_")
    # Try both naming conventions: single-dash and double-dash
    for sep in ("-", "--"):
        path = run_dir / "scores" / f"{safe_case}{sep}{safe_tool}.yaml"
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
            try:
                return JudgeScore(**data)
            except (ValidationError, TypeError):
                return None
    return None


def _load_single_normalized(run_dir: Path, case_id: str, tool: str) -> NormalizedResult | None:
    safe_case = case_id.replace("/", "_")
    safe_tool = tool.replace("/", "_")
    path = run_dir / f"{safe_case}-{safe_tool}.yaml"
    if not path.exists():
        # Try with double dash (legacy format)
        path = run_dir / f"{safe_case}--{safe_tool}.yaml"
        if not path.exists():
            return None
    data = yaml.safe_load(path.read_text()) or {}
    try:
        return NormalizedResult(**data)
    except (ValidationError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------


def create_app(cases_dir: Path, results_dir: Path, patches_dir: Path | None = None) -> Flask:
    template_dir = Path(__file__).parent / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.config["CASES_DIR"] = cases_dir
    app.config["RESULTS_DIR"] = results_dir
    app.config["PATCHES_DIR"] = patches_dir or Path("patches")

    # ------------------------------------------------------------------
    # Dashboard home
    # ------------------------------------------------------------------

    @app.route("/")
    def index() -> str:
        c_dir: Path = app.config["CASES_DIR"]
        cases: list[TestCase] = _cached(f"cases:{c_dir}", lambda: load_all_cases(c_dir))
        total = len(cases)
        by_repo = _count_field(cases, "repo")
        by_category = _count_field(cases, "category")
        return render_template(
            "index.html",
            total=total,
            by_repo=by_repo,
            by_category=by_category,
        )

    # ------------------------------------------------------------------
    # Experiment API
    # ------------------------------------------------------------------

    @app.route("/api/experiments")
    def api_experiments():  # type: ignore[no-untyped-def]
        r_dir: Path = app.config["RESULTS_DIR"]
        store = load_experiments(r_dir)
        assigned: set[str] = set()
        for exp in store.experiments:
            assigned.update(exp.runs)

        run_dirs = _run_dirs(r_dir)
        summaries = {d.name: _run_summary(d) for d in run_dirs}

        experiments_out = []
        for exp in store.experiments:
            runs_data = [summaries[r] for r in exp.runs if r in summaries]
            experiments_out.append(
                {
                    "id": exp.id,
                    "name": exp.name,
                    "status": exp.status,
                    "notes": exp.notes,
                    "created": exp.created,
                    "runs": runs_data,
                }
            )

        ungrouped = [summaries[d.name] for d in run_dirs if d.name not in assigned]

        return jsonify(
            {
                "experiments": experiments_out,
                "ungrouped": ungrouped,
            }
        )

    @app.route("/api/experiments", methods=["POST"])
    def api_create_experiment():  # type: ignore[no-untyped-def]
        r_dir: Path = app.config["RESULTS_DIR"]
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400

        exp_id = slugify(name)
        if not exp_id:
            return jsonify({"error": "invalid name"}), 400

        store = load_experiments(r_dir)
        if any(e.id == exp_id for e in store.experiments):
            return jsonify({"error": "experiment already exists"}), 409

        exp = Experiment(
            id=exp_id,
            name=name,
            runs=data.get("runs", []),
            notes=data.get("notes", ""),
            created=current_date_iso(),
        )
        store.experiments.append(exp)
        save_experiments(r_dir, store)
        return jsonify(exp.model_dump(mode="json")), 201

    @app.route("/api/experiments/<exp_id>", methods=["PUT"])
    def api_update_experiment(exp_id: str):  # type: ignore[no-untyped-def]
        r_dir: Path = app.config["RESULTS_DIR"]
        store = load_experiments(r_dir)
        exp = next((e for e in store.experiments if e.id == exp_id), None)
        if exp is None:
            return jsonify({"error": "not found"}), 404

        data = request.get_json(silent=True) or {}
        if "name" in data:
            exp.name = data["name"]
        if "runs" in data:
            exp.runs = data["runs"]
        if "notes" in data:
            exp.notes = data["notes"]
        if "status" in data:
            exp.status = data["status"]
        save_experiments(r_dir, store)
        return jsonify(exp.model_dump(mode="json"))

    @app.route("/api/experiments/<exp_id>/archive", methods=["POST"])
    def api_archive_experiment(exp_id: str):  # type: ignore[no-untyped-def]
        r_dir: Path = app.config["RESULTS_DIR"]
        store = load_experiments(r_dir)
        exp = next((e for e in store.experiments if e.id == exp_id), None)
        if exp is None:
            return jsonify({"error": "not found"}), 404

        exp.status = "active" if exp.status == "archived" else "archived"
        save_experiments(r_dir, store)
        return jsonify(exp.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Runs list + detail
    # ------------------------------------------------------------------

    @app.route("/runs")
    def runs_page() -> str:
        return render_template("runs.html")

    @app.route("/runs/<run_id>")
    def run_detail(run_id: str) -> Any:
        r_dir: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404

        status = _run_summary(run_dir)
        notes = load_run_notes(run_dir)

        # Extract tools from run_metadata.json
        tools_run: list[str] = []
        meta_path = run_dir / "run_metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                tools_run = sorted(meta.get("tools", []))
            except (json.JSONDecodeError, KeyError):
                pass

        has_analysis = (run_dir / "analysis").exists()

        return render_template(
            "run_detail.html",
            run_id=run_id,
            status=status,
            notes=notes,
            tools_run=tools_run,
            has_analysis=has_analysis,
        )

    @app.route("/runs/<run_id>/notes", methods=["POST"])
    def add_run_note_route(run_id: str) -> Any:
        r_dir: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404
        text = request.form.get("text", "").strip()
        if text:
            add_run_note(run_dir, text)
        return redirect(url_for("run_detail", run_id=run_id))

    # ------------------------------------------------------------------
    # Case list (thin HTML shell — data loaded via /api/cases)
    # ------------------------------------------------------------------

    @app.route("/cases")
    def case_list() -> str:
        return render_template("case_list.html")

    # ------------------------------------------------------------------
    # Cases API — paginated, filtered JSON
    # ------------------------------------------------------------------

    @app.route("/api/cases")
    def api_cases():  # type: ignore[no-untyped-def]
        c_dir: Path = app.config["CASES_DIR"]
        cases: list[TestCase] = _cached(f"cases:{c_dir}", lambda: load_all_cases(c_dir))

        # --- Query parameters ---
        f_repo = request.args.get("repo", "")
        f_cat = request.args.get("category", "")
        f_diff = request.args.get("difficulty", "")
        f_sev = request.args.get("severity", "")
        f_nmr = request.args.get("needs_manual_review", "")
        f_reviewed = request.args.get("reviewed", "")
        q = request.args.get("q", "").strip().lower()
        sort_by = request.args.get("sort", "id")
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(max(1, int(request.args.get("per_page", 50))), 200)

        reviewed_ids = _batch_reviewed_ids(c_dir, cases)

        # --- Filter ---
        def _match(c: TestCase) -> bool:
            if f_repo and c.repo != f_repo:
                return False
            if f_cat and c.category.value != f_cat:
                return False
            if f_diff and c.difficulty.value != f_diff:
                return False
            if f_sev and c.severity.value != f_sev:
                return False
            if f_nmr == "true" and not c.needs_manual_review:
                return False
            if f_nmr == "false" and c.needs_manual_review:
                return False
            if f_reviewed == "true" and c.id not in reviewed_ids:
                return False
            if f_reviewed == "false" and c.id in reviewed_ids:
                return False
            if q:
                haystack = (
                    c.id
                    + " "
                    + c.description
                    + " "
                    + " ".join(ef.summary for ef in c.expected_findings)
                ).lower()
                if q not in haystack:
                    return False
            return True

        filtered = [c for c in cases if _match(c)]

        # --- Sort ---
        reverse = sort_by.startswith("-")
        sort_key = sort_by.lstrip("-")
        if sort_key in ("id", "repo", "category", "difficulty", "severity"):
            filtered.sort(
                key=lambda c: str(getattr(c, sort_key, "")),
                reverse=reverse,
            )

        # --- Paginate ---
        total = len(filtered)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start = (page - 1) * per_page
        page_cases = filtered[start : start + per_page]

        # --- Serialize ---
        items = [
            {
                "id": c.id,
                "repo": c.repo,
                "category": c.category.value,
                "difficulty": c.difficulty.value,
                "severity": c.severity.value,
                "pr_size": c.pr_size.value,
                "language": c.language,
                "description": c.description,
                "findings_count": len(c.expected_findings),
                "verified": c.verified,
                "needs_manual_review": c.needs_manual_review,
                "reviewed": c.id in reviewed_ids,
            }
            for c in page_cases
        ]

        # --- Distinct filter values ---
        filters = {
            "repos": sorted({c.repo for c in cases}),
            "categories": [e.value for e in Category],
            "difficulties": [e.value for e in Difficulty],
            "severities": [e.value for e in Severity],
        }

        return jsonify(
            cases=items,
            total=total,
            page=page,
            pages=total_pages,
            filters=filters,
        )

    # ------------------------------------------------------------------
    # Case detail + edit
    # ------------------------------------------------------------------

    @app.route("/cases/<case_id>", methods=["GET", "POST"])
    def case_detail(case_id: str) -> Any:
        c_dir: Path = app.config["CASES_DIR"]
        yaml_path = _find_case_yaml(c_dir, case_id)
        if yaml_path is None:
            return f"Case {case_id} not found", 404

        data = yaml.safe_load(yaml_path.read_text()) or {}
        try:
            case = TestCase(**data)
        except ValidationError as exc:
            return f"Invalid case YAML: {exc}", 500

        reviewed = is_reviewed(c_dir, case_id, case.repo)

        if request.method == "POST":
            action = request.form.get("action", "save")

            if action == "accept":
                mark_reviewed(c_dir, case)
                return redirect(url_for("case_detail", case_id=case_id))

            if action == "skip":
                all_cases = _cached(f"cases:{c_dir}", lambda: load_all_cases(c_dir))
                ids = [c.id for c in all_cases]
                idx = ids.index(case_id) if case_id in ids else -1
                next_id = ids[idx + 1] if idx + 1 < len(ids) else ids[0]
                return redirect(url_for("case_detail", case_id=next_id))

            # Save edits
            updated = _parse_case_form(request.form, case)
            save_case(c_dir, updated)
            _invalidate_cache("cases:")
            if request.form.get("mark_reviewed"):
                mark_reviewed(c_dir, updated)
            return redirect(url_for("case_detail", case_id=case_id))

        # Diff loaded via AJAX — don't block page render
        diff_text = ""

        # Per-finding alignment (reads one patch, fast)
        finding_alignment: list[str] = []
        p_dir_detail: Path = app.config["PATCHES_DIR"]
        patch_path = p_dir_detail / f"{case.id}.patch"
        if patch_path.exists():
            patch_text = patch_path.read_text()
            per_finding = validate_case_alignment(case, patch_text)
            finding_alignment = [s.value for _, s in per_finding]

        # Prev/next from query params (passed by case_list) or cached case list
        prev_id = request.args.get("prev")
        next_id = request.args.get("next")
        if prev_id is None and next_id is None:
            all_cases = _cached(f"cases:{c_dir}", lambda: load_all_cases(c_dir))
            ids = [c.id for c in all_cases]
            idx = ids.index(case_id) if case_id in ids else -1
            prev_id = ids[idx - 1] if idx > 0 else None
            next_id = ids[idx + 1] if idx + 1 < len(ids) else None

        return render_template(
            "case_detail.html",
            case=case,
            reviewed=reviewed,
            diff_text=diff_text,
            prev_id=prev_id,
            next_id=next_id,
            categories=[e.value for e in Category],
            difficulties=[e.value for e in Difficulty],
            severities=[e.value for e in Severity],
            pr_sizes=[e.value for e in PRSize],
            visibilities=[e.value for e in Visibility],
            finding_alignment=finding_alignment,
        )

    # ------------------------------------------------------------------
    # AJAX endpoints
    # ------------------------------------------------------------------

    @app.route("/api/diff/<case_id>")
    def api_diff(case_id: str) -> Any:
        c_dir: Path = app.config["CASES_DIR"]
        yaml_path = _find_case_yaml(c_dir, case_id)
        if yaml_path is None:
            return jsonify({"error": "Case not found"}), 404
        data = yaml.safe_load(yaml_path.read_text()) or {}
        try:
            case = TestCase(**data)
        except ValidationError:
            return jsonify({"error": "Invalid case"}), 500
        diff_text = fetch_diff(case.repo, case.head_commit)
        return jsonify({"diff": diff_text})

    # ------------------------------------------------------------------
    # Human judge calibration
    # ------------------------------------------------------------------

    @app.route("/human-judge")
    def human_judge_page() -> str:
        r_dir: Path = app.config["RESULTS_DIR"]
        run_id = request.args.get("run", "")

        runs = _run_dirs(r_dir)
        selected_run: Path | None = None
        if run_id:
            candidate = r_dir / run_id
            if candidate.exists():
                selected_run = candidate
        elif runs:
            selected_run = runs[-1]

        kappa_report: dict[str, Any] = {}
        scores: list[JudgeScore] = []
        normalized: dict[tuple[str, str], NormalizedResult] = {}
        tool_map: dict[str, str] = {}

        if selected_run is not None:
            kappa_report = compute_kappa_report(selected_run)
            scores = _load_scores(selected_run)
            normalized = _load_normalized(selected_run)

            tool_map_path = selected_run / "human_judge" / "tool_map.yaml"
            if tool_map_path.exists():
                tool_map = yaml.safe_load(tool_map_path.read_text()) or {}

        _sc = default_scoring()
        _badge_classes = {0: "badge-red", 1: "badge-yellow", 2: "badge-blue", 3: "badge-green"}
        return render_template(
            "human_judge.html",
            runs=[d.name for d in runs],
            selected_run=selected_run.name if selected_run else "",
            kappa_report=kappa_report,
            scores=scores,
            normalized=normalized,
            tool_map=tool_map,
            scoring_scale=_sc.scale,
            scoring_badge_classes=_badge_classes,
        )

    @app.route("/human-judge/score", methods=["POST"])
    def human_judge_score() -> Any:
        """Save a single human score directly to the run's human_judge/ dir."""
        r_dir: Path = app.config["RESULTS_DIR"]
        run_id = request.form.get("run_id", "")
        case_id = request.form.get("case_id", "")
        tool = request.form.get("tool", "")
        human_score_str = request.form.get("human_score", "")
        notes = request.form.get("notes", "")

        if not (run_id and case_id and tool and human_score_str):
            return "Missing fields", 400

        try:
            human_score = int(human_score_str)
        except ValueError:
            return "Invalid score", 400

        if human_score not in default_scoring().scale:
            return "Score out of range", 400

        run_dir = r_dir / run_id
        if not run_dir.exists():
            return "Run not found", 404

        hj_dir = run_dir / "human_judge"
        hj_dir.mkdir(exist_ok=True)

        # Load LLM score for kappa
        scores = _load_scores(run_dir)
        llm_score = 0
        for s in scores:
            if s.test_case_id == case_id and s.tool == tool:
                llm_score = s.score
                break

        out: dict[str, Any] = {
            "test_case_id": case_id,
            "tool": tool,
            "human_score": human_score,
            "llm_score": llm_score,
            "notes": notes,
        }
        safe_case = case_id.replace("/", "_")
        safe_tool = tool.replace("/", "_")
        out_path = hj_dir / f"{safe_case}-{safe_tool}.yaml"
        out_path.write_text(yaml.safe_dump(out, sort_keys=False))

        return redirect(url_for("human_judge_page", run=run_id))

    # ------------------------------------------------------------------
    # DxAssessment entry
    # ------------------------------------------------------------------

    @app.route("/dx")
    def dx_page() -> str:
        r_dir: Path = app.config["RESULTS_DIR"]
        run_id = request.args.get("run", "")

        runs = _run_dirs(r_dir)
        selected_run: Path | None = None
        if run_id:
            candidate = r_dir / run_id
            if candidate.exists():
                selected_run = candidate
        elif runs:
            selected_run = runs[-1]

        tools: list[str] = []
        dx_by_tool: dict[str, DxAssessment | None] = {}

        if selected_run is not None:
            normalized = _load_normalized(selected_run)
            tools = sorted({tool for (_, tool) in normalized})
            for tool in tools:
                # Find first result for tool with dx data
                dx_val: DxAssessment | None = None
                for (_, t), r in normalized.items():
                    if t == tool and r.dx is not None:
                        dx_val = r.dx
                        break
                dx_by_tool[tool] = dx_val

        return render_template(
            "dx_assessment.html",
            runs=[d.name for d in runs],
            selected_run=selected_run.name if selected_run else "",
            tools=tools,
            dx_by_tool=dx_by_tool,
        )

    @app.route("/dx/save", methods=["POST"])
    def dx_save() -> Any:
        r_dir: Path = app.config["RESULTS_DIR"]
        run_id = request.form.get("run_id", "")
        tool = request.form.get("tool", "")

        if not (run_id and tool):
            return "Missing fields", 400

        run_dir = r_dir / run_id
        if not run_dir.exists():
            return "Run not found", 404

        def _int_field(name: str) -> int:
            try:
                val = int(request.form.get(name, "3"))
                return max(1, min(5, val))
            except ValueError:
                return 3

        dx = DxAssessment(
            actionability=_int_field("actionability"),
            false_positive_burden=_int_field("false_positive_burden"),
            integration_friction=_int_field("integration_friction"),
            response_latency=_int_field("response_latency"),
            notes=request.form.get("notes", ""),
        )

        # Save to all NormalizedResult YAMLs for this tool
        for path in run_dir.glob("*.yaml"):
            if path.name in ("checkpoint.yaml",):
                continue
            data = yaml.safe_load(path.read_text()) or {}
            if data.get("tool") == tool:
                data["dx"] = dx.model_dump(mode="json")
                path.write_text(yaml.safe_dump(data, sort_keys=False))

        return redirect(url_for("dx_page", run=run_id))

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @app.route("/metrics")
    def metrics_list() -> str:
        r_dir: Path = app.config["RESULTS_DIR"]
        runs = _run_dirs(r_dir)
        _sc = default_scoring()
        return render_template(
            "metrics.html",
            runs=[d.name for d in runs],
            run_data=None,
            scoring_scale=_sc.scale,
            scoring_labels=_sc.labels,
        )

    @app.route("/metrics/<run_id>")
    def metrics_detail(run_id: str) -> Any:
        r_dir: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404

        scores = _load_scores(run_dir)
        normalized = _load_normalized(run_dir)
        agg = aggregate_scores(scores) if scores else {}
        grouped_agg = group_agg_by_runner(agg) if agg else {}
        kappa_report = compute_kappa_report(run_dir)
        has_analysis = (run_dir / "analysis").exists()

        # Per-tool DX averages
        dx_summary = _compute_dx_summary(normalized)

        # Cost data
        _scoring = default_scoring()
        cost_by_tool: dict[str, dict[str, float]] = {}
        for tool in {s.tool for s in scores}:
            tool_scores = [s for s in scores if s.tool == tool]
            total_cost = sum(
                normalized[(s.test_case_id, s.tool)].metadata.cost_usd
                for s in tool_scores
                if (s.test_case_id, s.tool) in normalized
            )
            n = len(tool_scores)
            detections = sum(1 for s in tool_scores if s.score >= _scoring.catch_threshold)
            cost_by_tool[tool] = {
                "total": total_cost,
                "per_review": total_cost / n if n else 0.0,
                "per_detection": total_cost / detections if detections else 0.0,
            }

        runs = _run_dirs(r_dir)
        _sc = default_scoring()
        return render_template(
            "metrics.html",
            runs=[d.name for d in runs],
            selected_run=run_id,
            scoring_scale=_sc.scale,
            scoring_labels=_sc.labels,
            run_data={
                "agg": agg,
                "grouped_agg": grouped_agg,
                "has_analysis": has_analysis,
                "scores": scores,
                "kappa_report": kappa_report,
                "dx_summary": dx_summary,
                "cost_by_tool": cost_by_tool,
                "catch_rate": compute_catch_rate(scores),
                "avg_snr": compute_snr(scores),
            },
        )

    # ------------------------------------------------------------------
    # Analysis report viewer
    # ------------------------------------------------------------------

    @app.route("/metrics/<run_id>/report")
    def report_view(run_id: str) -> Any:
        r_dir: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404

        analysis_dir = run_dir / "analysis"
        report_html = ""
        total_scores = 0
        tool_count = 0

        if analysis_dir.exists():
            report_md_path = analysis_dir / "report.md"
            if report_md_path.exists():
                report_html = md_to_html(report_md_path.read_text())
            # Count score files directly instead of parsing all YAMLs
            scores_dir = run_dir / "scores"
            if scores_dir.exists():
                score_files = list(scores_dir.glob("*.yaml"))
                total_scores = len(score_files)
                tool_count = len({f.stem.rsplit("-", 1)[-1] for f in score_files if "-" in f.stem})

        has_catch_rate_chart = (analysis_dir / "catch_rate.png").exists()
        has_score_dist_chart = (analysis_dir / "score_dist.png").exists()

        return render_template(
            "report.html",
            run_id=run_id,
            report_html=report_html,
            total_scores=total_scores,
            tool_count=tool_count,
            has_catch_rate_chart=has_catch_rate_chart,
            has_score_dist_chart=has_score_dist_chart,
        )

    @app.route("/metrics/<run_id>/chart/<filename>")
    def serve_chart(run_id: str, filename: str) -> Any:
        allowed = {"catch_rate.png", "score_dist.png"}
        if filename not in allowed:
            return "Not found", 404
        r_dir: Path = app.config["RESULTS_DIR"]
        chart_path = r_dir / run_id / "analysis" / filename
        if not chart_path.exists():
            return "Not found", 404
        from flask import send_file

        return send_file(str(chart_path), mimetype="image/png")

    # ------------------------------------------------------------------
    # Per-case score drill-down
    # ------------------------------------------------------------------

    @app.route("/metrics/<run_id>/scores")
    def scores_list_view(run_id: str) -> Any:
        r_dir: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404

        tool = request.args.get("tool", "")
        score_filter = request.args.get("score", "")

        scores = _load_scores(run_dir)
        normalized = _load_normalized(run_dir)

        tools = sorted({s.tool for s in scores})
        if not tool and tools:
            tool = tools[0]

        tool_scores = [s for s in scores if s.tool == tool]
        if score_filter:
            try:
                sf = int(score_filter)
                tool_scores = [s for s in tool_scores if s.score == sf]
            except ValueError:
                pass

        page = max(1, int(request.args.get("page", 1)))
        per_page = 50
        total = len(tool_scores)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start = (page - 1) * per_page
        page_scores = tool_scores[start : start + per_page]

        entries = []
        for s in page_scores:
            nr = normalized.get((s.test_case_id, s.tool))
            comment_count = len(nr.comments) if nr else 0
            tp_count = sum(1 for cj in s.comment_judgments if cj.classification.value == "TP")
            fp_count = sum(1 for cj in s.comment_judgments if cj.classification.value == "FP")
            entries.append(
                {
                    "case_id": s.test_case_id,
                    "score": s.score,
                    "reasoning": s.reasoning,
                    "comment_count": comment_count,
                    "tp_count": tp_count,
                    "fp_count": fp_count,
                }
            )

        return render_template(
            "scores_list.html",
            run_id=run_id,
            tool=tool,
            tools=tools,
            score_filter=score_filter,
            entries=entries,
            page=page,
            total=total,
            total_pages=total_pages,
        )

    @app.route("/metrics/<run_id>/scores/<case_id>/<tool>")
    def score_detail_view(run_id: str, case_id: str, tool: str) -> Any:
        r_dir: Path = app.config["RESULTS_DIR"]
        c_dir_sc: Path = app.config["CASES_DIR"]
        run_dir = r_dir / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404

        # Load case
        yaml_path = _find_case_yaml(c_dir_sc, case_id)
        if yaml_path is None:
            return f"Case {case_id} not found", 404
        data = yaml.safe_load(yaml_path.read_text()) or {}
        try:
            case = TestCase(**data)
        except ValidationError:
            return f"Invalid case YAML for {case_id}", 500

        # Load single score and normalized result (no need to load all)
        judge_score = _load_single_score(run_dir, case_id, tool)
        nr = _load_single_normalized(run_dir, case_id, tool)

        # Prev/next from cached scores (shared with scores_list_view)
        scores = _load_scores(run_dir)
        tool_case_ids = [s.test_case_id for s in scores if s.tool == tool]
        idx = tool_case_ids.index(case_id) if case_id in tool_case_ids else -1
        prev_case = tool_case_ids[idx - 1] if idx > 0 else None
        next_case = tool_case_ids[idx + 1] if 0 <= idx < len(tool_case_ids) - 1 else None

        return render_template(
            "score_detail.html",
            run_id=run_id,
            tool=tool,
            case=case,
            judge_score=judge_score,
            normalized=nr,
            prev_case=prev_case,
            next_case=next_case,
        )

    # ------------------------------------------------------------------
    # Dataset inspector
    # ------------------------------------------------------------------

    def _alignment_counts(cases: list[TestCase], p_dir: Path, c_dir: Path) -> dict[str, int] | None:
        if not p_dir.exists():
            return None

        def _compute() -> dict[str, int]:
            al_map = load_alignment_for_cases(cases, p_dir)
            counts = {"aligned": 0, "file_only": 0, "misaligned": 0}
            for status in al_map.values():
                if status == AlignmentStatus.aligned:
                    counts["aligned"] += 1
                elif status == AlignmentStatus.file_only:
                    counts["file_only"] += 1
                else:
                    counts["misaligned"] += 1
            return counts

        return _cached(f"alignment_counts:{c_dir}:{p_dir}", _compute)

    @app.route("/api/dataset/stats")
    def api_dataset_stats():  # type: ignore[no-untyped-def]
        c_dir_ds: Path = app.config["CASES_DIR"]
        p_dir_ds: Path = app.config["PATCHES_DIR"]
        cases: list[TestCase] = _cached(f"cases:{c_dir_ds}", lambda: load_all_cases(c_dir_ds))
        stats = _cached(
            f"dataset_stats:{c_dir_ds}",
            lambda: compute_dataset_stats(cases),
        )
        alignment = _alignment_counts(cases, p_dir_ds, c_dir_ds)
        flagged = sum(1 for c in cases if "groundedness-failed" in c.quality_flags)
        return jsonify(
            total=stats["total"],
            verified=stats["verified"],
            needs_review=stats["needs_review"],
            flagged=flagged,
            avg_findings=stats["avg_findings"],
            distributions=stats["distributions"],
            alignment=alignment,
        )

    @app.route("/api/dataset/findings")
    def api_dataset_findings():  # type: ignore[no-untyped-def]
        c_dir_ds: Path = app.config["CASES_DIR"]
        cases: list[TestCase] = _cached(f"cases:{c_dir_ds}", lambda: load_all_cases(c_dir_ds))
        stats = _cached(
            f"dataset_stats:{c_dir_ds}",
            lambda: compute_dataset_stats(cases),
        )
        findings: list[dict[str, str]] = stats["findings_list"]

        f_repo = request.args.get("repo", "")
        if f_repo:
            findings = [f for f in findings if f["repo"] == f_repo]

        page = max(1, int(request.args.get("page", 1)))
        per_page = min(max(1, int(request.args.get("per_page", 50))), 200)
        total = len(findings)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start = (page - 1) * per_page
        page_findings = findings[start : start + per_page]

        items = [
            {
                "case_id": f["case_id"],
                "repo": f["repo"],
                "file": f["file"],
                "line": int(f["line"]),
                "summary": f["summary"],
            }
            for f in page_findings
        ]

        return jsonify(
            findings=items,
            total=total,
            page=page,
            pages=total_pages,
        )

    @app.route("/dataset")
    def dataset_inspector() -> str:
        return render_template("dataset.html")

    # ------------------------------------------------------------------
    # Golden set manager
    # ------------------------------------------------------------------

    @app.route("/golden")
    def golden_page() -> str:
        c_dir_g: Path = app.config["CASES_DIR"]
        cases: list[TestCase] = _cached(
            f"cases:{c_dir_g}", lambda: load_all_cases(c_dir_g)
        )
        golden = load_golden_set(c_dir_g)

        filter_status = request.args.get("status", "")
        filter_repo = request.args.get("repo", "")
        page = max(1, int(request.args.get("page", 1)))
        per_page = 50

        # Build items with golden status
        items: list[dict[str, str]] = []
        for c in cases:
            entry = golden.get(c.id)
            g_status = entry.status if entry else "unreviewed"
            reviewer = entry.reviewer if entry else ""
            if filter_status and g_status != filter_status:
                continue
            if filter_repo and c.repo != filter_repo:
                continue
            items.append({
                "case_id": c.id,
                "repo": c.repo,
                "category": c.category.value,
                "severity": c.severity.value,
                "golden_status": g_status,
                "reviewer": reviewer,
            })

        total = len(items)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start = (page - 1) * per_page
        page_items = items[start : start + per_page]

        # Coverage by repo
        coverage: dict[str, dict[str, int]] = {}
        for c in cases:
            repo = c.repo
            if repo not in coverage:
                coverage[repo] = {
                    "total": 0, "confirmed": 0, "disputed": 0, "unreviewed": 0,
                }
            coverage[repo]["total"] += 1
            entry = golden.get(c.id)
            status = entry.status if entry else "unreviewed"
            if status in coverage[repo]:
                coverage[repo][status] += 1

        repos = sorted({c.repo for c in cases})

        # Stats
        confirmed = sum(1 for e in golden.values() if e.status == "confirmed")
        disputed = sum(1 for e in golden.values() if e.status == "disputed")
        unreviewed_count = len(cases) - confirmed - disputed

        # Next unreviewed
        reviewed_ids = {e.case_id for e in golden.values() if e.status != "unreviewed"}
        next_unreviewed = next((c.id for c in cases if c.id not in reviewed_ids), None)

        return render_template(
            "golden.html",
            items=page_items,
            total=len(cases),
            confirmed=confirmed,
            disputed=disputed,
            unreviewed=unreviewed_count,
            coverage=coverage,
            repos=repos,
            filter_status=filter_status,
            filter_repo=filter_repo,
            page=page,
            pages=total_pages,
            next_unreviewed=next_unreviewed,
        )

    @app.route("/golden/<case_id>", methods=["POST"])
    def golden_set_status(case_id: str) -> Any:
        c_dir_gs: Path = app.config["CASES_DIR"]
        status = request.form.get("status", "")
        if status not in ("confirmed", "disputed", "unreviewed"):
            return "Invalid status", 400
        set_golden_status(c_dir_gs, case_id, status, reviewer="dashboard")
        return redirect(url_for("golden_page"))

    @app.route("/golden/review/<case_id>")
    def golden_review(case_id: str) -> Any:
        """Redirect to case detail for review, with context."""
        return redirect(url_for("case_detail", case_id=case_id))

    # ------------------------------------------------------------------
    # Human scoring (enhanced, tool-blinded)
    # ------------------------------------------------------------------

    @app.route("/score/<run_id>", methods=["GET"])
    def human_score_page(run_id: str) -> Any:
        r_dir_hs: Path = app.config["RESULTS_DIR"]
        c_dir_hs: Path = app.config["CASES_DIR"]
        run_dir = r_dir_hs / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404

        scores = _load_scores(run_dir)

        # Build (case, tool) key list with LLM and human scores
        all_keys: list[tuple[str, str, int, int | None]] = []
        for s in scores:
            hs = load_human_score(run_dir, s.test_case_id, s.tool)
            all_keys.append((
                s.test_case_id,
                s.tool,
                s.score,
                hs.detection_score if hs else None,
            ))

        # Which case/tool to show
        req_case = request.args.get("case", "")
        req_tool = request.args.get("tool", "")

        current_case: TestCase | None = None
        current_tool = ""
        judge_score: JudgeScore | None = None
        nr: NormalizedResult | None = None
        existing_score: HumanScore | None = None
        comment_judgments: list[Any] = []
        blind_label = ""
        prev_key: tuple[str, str] | None = None
        next_key: tuple[str, str] | None = None

        if req_case and req_tool:
            yaml_path = _find_case_yaml(c_dir_hs, req_case)
            if yaml_path:
                data = yaml.safe_load(yaml_path.read_text()) or {}
                try:
                    current_case = TestCase(**data)
                except (ValidationError, TypeError):
                    pass
            current_tool = req_tool
            judge_score = _load_single_score(run_dir, req_case, req_tool)
            nr = _load_single_normalized(run_dir, req_case, req_tool)
            existing_score = load_human_score(run_dir, req_case, req_tool)
            if judge_score:
                comment_judgments = judge_score.comment_judgments

            # Blind label: sort tools alphabetically for stable ordering
            tools_sorted = sorted({s.tool for s in scores})
            tool_idx = tools_sorted.index(req_tool) if req_tool in tools_sorted else 0
            blind_label = f"Tool {chr(65 + tool_idx % 26)}"

            # Prev/next navigation
            idx = next(
                (i for i, k in enumerate(all_keys) if k[0] == req_case and k[1] == req_tool),
                -1,
            )
            if idx > 0:
                prev_key = (all_keys[idx - 1][0], all_keys[idx - 1][1])
            if 0 <= idx < len(all_keys) - 1:
                next_key = (all_keys[idx + 1][0], all_keys[idx + 1][1])
        elif all_keys:
            # Find first unscored
            for k in all_keys:
                if k[3] is None:
                    return redirect(
                        url_for("human_score_page", run_id=run_id, case=k[0], tool=k[1])
                    )

        # Build blind label map for all tools (stable alphabetical ordering)
        tools_sorted_all = sorted({k[1] for k in all_keys})
        blind_map: dict[str, str] = {
            t: f"Tool {chr(65 + i % 26)}" for i, t in enumerate(tools_sorted_all)
        }

        return render_template(
            "human_score.html",
            run_id=run_id,
            all_keys=all_keys,
            current_case=current_case,
            current_tool=current_tool,
            judge_score=judge_score,
            normalized=nr,
            existing_score=existing_score,
            comment_judgments=comment_judgments,
            blind_label=blind_label,
            blind_map=blind_map,
            prev_key=prev_key,
            next_key=next_key,
        )

    @app.route("/score/<run_id>", methods=["POST"])
    def human_score_submit(run_id: str) -> Any:
        r_dir_hss: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir_hss / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404

        case_id = request.form.get("case_id", "")
        tool = request.form.get("tool", "")
        if not (case_id and tool):
            return "Missing case_id or tool", 400

        try:
            detection = int(request.form.get("detection_score", "0"))
            quality = int(request.form.get("review_quality", "0"))
        except ValueError:
            return "Invalid score", 400

        if detection not in range(4) or quality not in range(5):
            return "Score out of range", 400

        from datetime import UTC, datetime

        score = HumanScore(
            case_id=case_id,
            tool=tool,
            detection_score=detection,
            review_quality=quality,
            notes=request.form.get("notes", ""),
            timestamp=datetime.now(UTC).isoformat(),
        )
        save_human_score(run_dir, score)

        # If "advance" was clicked, go to next
        if request.form.get("advance"):
            scores = _load_scores(run_dir)
            all_keys = [(s.test_case_id, s.tool) for s in scores]
            idx = next(
                (i for i, k in enumerate(all_keys) if k[0] == case_id and k[1] == tool),
                -1,
            )
            if 0 <= idx < len(all_keys) - 1:
                nk = all_keys[idx + 1]
                return redirect(
                    url_for("human_score_page", run_id=run_id, case=nk[0], tool=nk[1])
                )

        return redirect(
            url_for("human_score_page", run_id=run_id, case=case_id, tool=tool)
        )

    # ------------------------------------------------------------------
    # Run comparison
    # ------------------------------------------------------------------

    @app.route("/compare")
    def compare_runs() -> str:
        r_dir_cmp: Path = app.config["RESULTS_DIR"]
        all_runs = [d.name for d in _run_dirs(r_dir_cmp)]

        selected_runs_raw = request.args.getlist("runs")
        # Also handle comma-separated single param
        selected_runs: list[str] = []
        for r in selected_runs_raw:
            selected_runs.extend(r.split(","))
        selected_runs = [r for r in selected_runs if r in all_runs]

        comparison: dict[str, list[dict[str, Any]]] = {}
        # {tool: {run_name: {catch_rate, avg_score}}} for easy template lookup
        comparison_lookup: dict[str, dict[str, dict[str, Any]]] = {}
        runner_type_map: dict[str, str] = {}
        if len(selected_runs) >= 2:
            run_dir_list = [r_dir_cmp / r for r in selected_runs]
            comparison = compute_comparison_data(run_dir_list)
            for tool, runs_data in comparison.items():
                runner_type_map[tool] = classify_runner_type(tool)
                comparison_lookup[tool] = {rd["run"]: rd for rd in runs_data}

        return render_template(
            "compare.html",
            all_runs=all_runs,
            selected_runs=selected_runs,
            comparison=comparison,
            comparison_lookup=comparison_lookup,
            runner_type_map=runner_type_map,
        )

    # ------------------------------------------------------------------
    # Mode 1: Dataset Review API
    # ------------------------------------------------------------------

    @app.route("/api/cases/<case_id>/verify", methods=["POST"])
    def api_verify_case(case_id: str) -> Any:
        c_dir_v: Path = app.config["CASES_DIR"]
        yaml_path = _find_case_yaml(c_dir_v, case_id)
        if yaml_path is None:
            return jsonify({"error": "Case not found"}), 404
        data = yaml.safe_load(yaml_path.read_text()) or {}
        try:
            case = TestCase(**data)
        except (ValidationError, TypeError):
            return jsonify({"error": "Invalid case YAML"}), 500
        case = case.model_copy(update={"verified": True})
        yaml_path.write_text(
            yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False)
        )
        _invalidate_cache("cases:")
        return jsonify({"verified": True})

    @app.route("/api/cases/<case_id>/flag", methods=["POST"])
    def api_flag_case(case_id: str) -> Any:
        c_dir_f: Path = app.config["CASES_DIR"]
        body = request.get_json(silent=True) or {}
        flag = body.get("flag", "")
        if not flag:
            return jsonify({"error": "flag is required"}), 400
        yaml_path = _find_case_yaml(c_dir_f, case_id)
        if yaml_path is None:
            return jsonify({"error": "Case not found"}), 404
        data = yaml.safe_load(yaml_path.read_text()) or {}
        try:
            case = TestCase(**data)
        except (ValidationError, TypeError):
            return jsonify({"error": "Invalid case YAML"}), 500
        flags = list(case.quality_flags)
        flags.append(flag)
        notes = list(case.reviewer_notes)
        note = body.get("note", "")
        if note:
            notes.append(note)
        case = case.model_copy(
            update={"quality_flags": flags, "reviewer_notes": notes}
        )
        yaml_path.write_text(
            yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False)
        )
        _invalidate_cache("cases:")
        return jsonify({
            "quality_flags": case.quality_flags,
            "reviewer_notes": case.reviewer_notes,
        })

    @app.route("/api/cases/<case_id>/findings", methods=["PUT"])
    def api_update_findings(case_id: str) -> Any:
        c_dir_uf: Path = app.config["CASES_DIR"]
        body = request.get_json(silent=True) or {}
        if "findings" not in body:
            return jsonify({"error": "findings is required"}), 400
        yaml_path = _find_case_yaml(c_dir_uf, case_id)
        if yaml_path is None:
            return jsonify({"error": "Case not found"}), 404
        data = yaml.safe_load(yaml_path.read_text()) or {}
        try:
            case = TestCase(**data)
        except (ValidationError, TypeError):
            return jsonify({"error": "Invalid case YAML"}), 500
        new_findings = [
            ExpectedFinding(**f) for f in body["findings"]
        ]
        case = case.model_copy(
            update={"expected_findings": new_findings}
        )
        yaml_path.write_text(
            yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False)
        )
        _invalidate_cache("cases:")
        return jsonify({
            "expected_findings": [
                f.model_dump(mode="json") for f in case.expected_findings
            ],
        })

    @app.route("/api/cases/<case_id>/notes", methods=["PUT"])
    def api_update_notes(case_id: str) -> Any:
        c_dir_un: Path = app.config["CASES_DIR"]
        body = request.get_json(silent=True) or {}
        if "notes" not in body:
            return jsonify({"error": "notes is required"}), 400
        yaml_path = _find_case_yaml(c_dir_un, case_id)
        if yaml_path is None:
            return jsonify({"error": "Case not found"}), 404
        data = yaml.safe_load(yaml_path.read_text()) or {}
        try:
            case = TestCase(**data)
        except (ValidationError, TypeError):
            return jsonify({"error": "Invalid case YAML"}), 500
        case = case.model_copy(
            update={"reviewer_notes": list(body["notes"])}
        )
        yaml_path.write_text(
            yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False)
        )
        _invalidate_cache("cases:")
        return jsonify({"reviewer_notes": case.reviewer_notes})

    # ------------------------------------------------------------------
    # Mode 2: Score Review API
    # ------------------------------------------------------------------

    @app.route("/api/runs/<run_id>/scores")
    def api_run_scores(run_id: str) -> Any:
        r_dir_rs: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir_rs / run_id
        if not run_dir.exists():
            return jsonify({"error": "Run not found"}), 404

        scores = _load_scores(run_dir)
        normalized = _load_normalized(run_dir)

        # Query params
        f_tool = request.args.get("tool", "")
        f_score = request.args.get("score", "")
        f_disagree = request.args.get("disagreement", "")
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(
            max(1, int(request.args.get("per_page", 50))), 200
        )

        tools = sorted({s.tool for s in scores})

        entries = []
        for s in scores:
            if f_tool and s.tool != f_tool:
                continue
            if f_score:
                try:
                    if s.score != int(f_score):
                        continue
                except ValueError:
                    pass
            if f_disagree == "true" and s.votes:
                spread = max(s.votes) - min(s.votes)
                if spread < 2:
                    continue

            nr = normalized.get((s.test_case_id, s.tool))
            comment_count = len(nr.comments) if nr else 0
            tp = sum(
                1 for cj in s.comment_judgments
                if cj.classification.value == "TP"
            )
            fp = sum(
                1 for cj in s.comment_judgments
                if cj.classification.value == "FP"
            )
            entries.append({
                "case_id": s.test_case_id,
                "tool": s.tool,
                "score": s.score,
                "reasoning": s.reasoning,
                "votes": s.votes,
                "comment_count": comment_count,
                "tp_count": tp,
                "fp_count": fp,
            })

        total = len(entries)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start = (page - 1) * per_page
        page_entries = entries[start : start + per_page]

        return jsonify(
            scores=page_entries,
            total=total,
            page=page,
            pages=total_pages,
            tools=tools,
        )

    @app.route(
        "/api/runs/<run_id>/scores/<case_id>/<tool>/human",
        methods=["POST"],
    )
    def api_human_score_override(
        run_id: str, case_id: str, tool: str,
    ) -> Any:
        r_dir_ho: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir_ho / run_id
        if not run_dir.exists():
            return jsonify({"error": "Run not found"}), 404

        body = request.get_json(silent=True) or {}
        if "score" not in body:
            return jsonify({"error": "score is required"}), 400

        try:
            human_score = int(body["score"])
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid score"}), 400

        if human_score not in default_scoring().scale:
            return jsonify({"error": "Score out of range"}), 400

        notes = body.get("notes", "")

        hj_dir = run_dir / "human_judge"
        hj_dir.mkdir(exist_ok=True)

        safe_case = case_id.replace("/", "_")
        safe_tool = tool.replace("/", "_")
        out: dict[str, Any] = {
            "test_case_id": case_id,
            "tool": tool,
            "human_score": human_score,
            "notes": notes,
        }
        out_path = hj_dir / f"{safe_case}-{safe_tool}.yaml"
        out_path.write_text(yaml.safe_dump(out, sort_keys=False))

        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Mode 2: Score Review page
    # ------------------------------------------------------------------

    @app.route("/metrics/<run_id>/review")
    def score_review_page(run_id: str) -> Any:
        r_dir_sr: Path = app.config["RESULTS_DIR"]
        run_dir = r_dir_sr / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404
        return render_template("score_review.html", run_id=run_id)

    # ------------------------------------------------------------------
    # Mode 3: Model Comparison API
    # ------------------------------------------------------------------

    @app.route("/api/runs/<run_id>/compare/<case_id>")
    def api_compare_case(run_id: str, case_id: str) -> Any:
        r_dir_mc: Path = app.config["RESULTS_DIR"]
        c_dir_mc: Path = app.config["CASES_DIR"]
        run_dir = r_dir_mc / run_id
        if not run_dir.exists():
            return jsonify({"error": "Run not found"}), 404

        yaml_path = _find_case_yaml(c_dir_mc, case_id)
        if yaml_path is None:
            return jsonify({"error": "Case not found"}), 404
        data = yaml.safe_load(yaml_path.read_text()) or {}
        try:
            case = TestCase(**data)
        except (ValidationError, TypeError):
            return jsonify({"error": "Invalid case YAML"}), 500

        scores = _load_scores(run_dir)
        normalized = _load_normalized(run_dir)

        case_scores = [s for s in scores if s.test_case_id == case_id]
        tools_out = []
        for s in case_scores:
            nr = normalized.get((s.test_case_id, s.tool))
            findings = []
            if nr:
                for c in nr.comments:
                    findings.append({
                        "file": c.file,
                        "line": c.line,
                        "summary": c.body,
                        "confidence": c.confidence,
                    })
            tp = sum(
                1 for cj in s.comment_judgments
                if cj.classification.value == "TP"
            )
            fp = sum(
                1 for cj in s.comment_judgments
                if cj.classification.value == "FP"
            )
            tools_out.append({
                "tool": s.tool,
                "score": s.score,
                "reasoning": s.reasoning,
                "findings": findings,
                "comment_count": len(nr.comments) if nr else 0,
                "tp_count": tp,
                "fp_count": fp,
            })

        return jsonify(
            case={
                "id": case.id,
                "description": case.description,
                "expected_findings": [
                    f.model_dump(mode="json")
                    for f in case.expected_findings
                ],
            },
            tools=tools_out,
        )

    # ------------------------------------------------------------------
    # Mode 3: Model Comparison page
    # ------------------------------------------------------------------

    @app.route("/metrics/<run_id>/compare/<case_id>")
    def model_compare_page(run_id: str, case_id: str) -> Any:
        r_dir_cp: Path = app.config["RESULTS_DIR"]
        c_dir_cp: Path = app.config["CASES_DIR"]
        run_dir = r_dir_cp / run_id
        if not run_dir.exists():
            return f"Run {run_id} not found", 404
        yaml_path = _find_case_yaml(c_dir_cp, case_id)
        if yaml_path is None:
            return f"Case {case_id} not found", 404
        return render_template(
            "model_compare.html",
            run_id=run_id,
            case_id=case_id,
        )

    return app


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _count_field(cases: list[TestCase], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in cases:
        val = getattr(c, field)
        key = val.value if hasattr(val, "value") else str(val)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _parse_case_form(form: Any, original: TestCase) -> TestCase:
    """Parse POST form data into an updated TestCase."""
    # Collect expected findings from indexed form fields
    findings: list[ExpectedFinding] = []
    i = 0
    while True:
        f_file = form.get(f"ef_file_{i}", "").strip()
        f_line = form.get(f"ef_line_{i}", "").strip()
        f_summary = form.get(f"ef_summary_{i}", "").strip()
        if not (f_file or f_summary):
            break
        try:
            findings.append(
                ExpectedFinding(file=f_file, line=int(f_line or "0"), summary=f_summary)
            )
        except (ValueError, TypeError):
            pass
        i += 1

    return TestCase(
        id=original.id,
        repo=original.repo,
        base_commit=form.get("base_commit", original.base_commit).strip(),
        head_commit=form.get("head_commit", original.head_commit).strip(),
        fix_commit=original.fix_commit,
        category=Category(form.get("category", original.category.value)),
        difficulty=Difficulty(form.get("difficulty", original.difficulty.value)),
        severity=Severity(form.get("severity", original.severity.value)),
        language=original.language,
        pr_size=PRSize(form.get("pr_size", original.pr_size.value)),
        description=form.get("description", original.description).strip(),
        expected_findings=findings if findings else original.expected_findings,
        stats=original.stats,
        visibility=Visibility(form.get("visibility", original.visibility.value)),
        needs_manual_review="needs_manual_review" in form,
        verified=original.verified,
        verified_by=original.verified_by,
    )


def _compute_dx_summary(
    normalized: dict[tuple[str, str], NormalizedResult],
) -> dict[str, dict[str, float]]:
    dims = ("actionability", "false_positive_burden", "integration_friction", "response_latency")
    by_tool: dict[str, list[DxAssessment]] = {}
    for (_, tool), r in normalized.items():
        if r.dx is not None:
            by_tool.setdefault(tool, []).append(r.dx)
    result: dict[str, dict[str, float]] = {}
    for tool, dxs in sorted(by_tool.items()):
        result[tool] = {dim: sum(getattr(d, dim) for d in dxs) / len(dxs) for dim in dims}
    return result


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("dashboard")
@click.option("--port", default=5000, show_default=True, help="Port to listen on")
@click.option(
    "--cases-dir",
    default="cases/final",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Directory containing curated case YAML files",
)
@click.option(
    "--results-dir",
    default="results",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Root directory for run outputs",
)
@click.option(
    "--patches-dir",
    default="patches",
    show_default=True,
    type=click.Path(dir_okay=True, file_okay=False),
    help="Directory containing .patch files for alignment checks",
)
@click.option("--debug", is_flag=True, default=False, help="Enable Flask debug mode")
def dashboard(port: int, cases_dir: str, results_dir: str, patches_dir: str, debug: bool) -> None:
    """Launch the local review dashboard."""
    app = create_app(Path(cases_dir), Path(results_dir), Path(patches_dir))
    click.echo(f"Dashboard → http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=debug)
