"""gen-clean-cases CLI: mine non-fix PRs for negative control cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from bugeval.github_scraper import run_gh
from bugeval.io import save_case
from bugeval.models import (
    CaseStats,
    Category,
    Difficulty,
    PRSize,
    Severity,
    TestCase,
)

_FIX_KEYWORDS = {"fix", "bug", "patch", "correct", "resolve", "regression", "revert", "hotfix"}


def _has_fix_signal(title: str, labels: list[str]) -> bool:
    """Return True if the PR title or labels suggest it's a bug fix."""
    title_lower = title.lower()
    if any(kw in title_lower for kw in _FIX_KEYWORDS):
        return True
    label_names = {lbl.lower() if isinstance(lbl, str) else lbl.get("name", "").lower()
                   for lbl in labels}
    fix_labels = {"bug", "fix", "bugfix", "hotfix", "regression", "defect"}
    return bool(label_names & fix_labels)


def _classify_pr_size(additions: int, deletions: int) -> PRSize:
    total = additions + deletions
    if total <= 10:
        return PRSize.tiny
    if total <= 50:
        return PRSize.small
    if total <= 200:
        return PRSize.medium
    if total <= 500:
        return PRSize.large
    return PRSize.xl


def _detect_language(files: list[dict[str, Any]]) -> str:
    ext_map = {
        ".rs": "rust", ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".go": "go", ".java": "java",
        ".rb": "ruby", ".cpp": "c++", ".c": "c", ".cs": "c#",
    }
    counts: dict[str, int] = {}
    for f in files:
        path = f.get("path", "")
        for ext, lang in ext_map.items():
            if path.endswith(ext):
                counts[lang] = counts.get(lang, 0) + 1
                break
    if not counts:
        return "unknown"
    return max(counts, key=counts.get)  # type: ignore[arg-type]


def fetch_clean_prs(repo: str, limit: int = 200) -> list[dict[str, Any]]:
    """Fetch merged PRs that are NOT bug fixes (refactors, features, chores)."""
    args = [
        "pr", "list", "--repo", repo, "--state", "merged",
        "--json",
        "number,title,body,labels,mergeCommit,baseRefName,headRefName,files,"
        "additions,deletions,changedFiles",
        "--limit", str(limit),
    ]
    output = run_gh(*args)
    all_prs: list[dict[str, Any]] = json.loads(output)
    return [pr for pr in all_prs if not _has_fix_signal(pr.get("title", ""), pr.get("labels", []))]


def _pr_to_case(pr: dict[str, Any], repo: str, case_id: str) -> TestCase | None:
    """Convert a clean PR dict to a TestCase with case_type='clean'."""
    merge_commit = pr.get("mergeCommit") or {}
    head_sha = merge_commit.get("oid", "")
    if not head_sha:
        return None

    files = pr.get("files") or []
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)
    changed = pr.get("changedFiles", len(files))

    return TestCase(
        id=case_id,
        repo=repo,
        base_commit=f"{head_sha}~1",
        head_commit=head_sha,
        fix_commit=head_sha,
        category=Category.code_smell,  # placeholder for clean cases
        difficulty=Difficulty.easy,
        severity=Severity.low,
        language=_detect_language(files),
        pr_size=_classify_pr_size(additions, deletions),
        description=pr.get("title", ""),
        expected_findings=[],
        stats=CaseStats(
            lines_added=additions,
            lines_deleted=deletions,
            files_changed=changed,
            hunks=changed,
        ),
        case_type="clean",
        pr_number=pr.get("number"),
        pr_title=pr.get("title", ""),
        pr_body=(pr.get("body") or "")[:3000],
    )


@click.command("gen-clean-cases")
@click.option("--repo", required=True, help="GitHub repo (owner/name)")
@click.option("--output-dir", required=True, type=click.Path(), help="Output directory for cases")
@click.option("--limit", default=200, show_default=True, help="Max PRs to fetch from GitHub")
@click.option("--max-cases", default=30, show_default=True, help="Max cases to generate")
@click.option("--dry-run", is_flag=True, default=False, help="Print candidates without writing")
@click.option(
    "--prefix", default=None,
    help="Case ID prefix (default: derived from repo name, e.g. 'leo-clean')",
)
def gen_clean_cases(
    repo: str,
    output_dir: str,
    limit: int,
    max_cases: int,
    dry_run: bool,
    prefix: str | None,
) -> None:
    """Mine non-fix PRs from a repo for negative control (clean) cases."""
    click.echo(f"Fetching merged PRs from {repo}...")
    prs = fetch_clean_prs(repo, limit=limit)
    click.echo(f"Found {len(prs)} non-fix PRs (out of {limit} fetched)")

    if not prs:
        click.echo("No clean PRs found.")
        return

    repo_short = repo.split("/")[-1] if "/" in repo else repo
    id_prefix = prefix or f"{repo_short}-clean"
    out = Path(output_dir)

    count = 0
    for i, pr in enumerate(prs):
        if count >= max_cases:
            break
        case_id = f"{id_prefix}-{i + 1:03d}"
        case = _pr_to_case(pr, repo, case_id)
        if case is None:
            continue

        if dry_run:
            click.echo(f"  [dry-run] {case_id}: #{pr.get('number')} {pr.get('title', '')[:60]}")
        else:
            save_case(case, out / f"{case_id}.yaml")
            click.echo(f"  [saved] {case_id}: #{pr.get('number')} {pr.get('title', '')[:60]}")
        count += 1

    click.echo(f"Generated {count} clean cases" + (" (dry-run)" if dry_run else f" in {out}"))
