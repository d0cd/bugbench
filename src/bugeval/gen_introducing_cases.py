"""gen-introducing-cases CLI: generate introducing cases from existing fix cases."""

from __future__ import annotations

from pathlib import Path

import click

from bugeval.git_miner import find_introducing_commit_via_blame
from bugeval.git_utils import GitError, run_git
from bugeval.io import load_all_cases, save_case
from bugeval.models import ExpectedFinding, TestCase


def _rephrase_finding(f: ExpectedFinding) -> ExpectedFinding:
    """Rephrase a fix-side finding summary to introducing-side framing."""
    summary = f.summary
    # Simple rephrasing: prepend "This change introduces:" if not already framed
    if not summary.lower().startswith("this change"):
        summary = f"This change introduces an issue: {summary}"
    return f.model_copy(update={"summary": summary, "line_side": "post_fix"})


def generate_introducing_case(
    fix_case: TestCase,
    introducing_sha: str,
    cwd: Path,
) -> TestCase | None:
    """Generate an introducing case from a fix case + introducing commit SHA."""
    # Get the parent of the introducing commit as base
    try:
        parent = run_git("rev-parse", f"{introducing_sha}^", cwd=cwd).strip()
    except GitError:
        return None

    rephrased = [_rephrase_finding(f) for f in fix_case.expected_findings]

    return TestCase(
        id=fix_case.id.replace("-", "-intro-", 1),
        repo=fix_case.repo,
        base_commit=parent,
        head_commit=introducing_sha,
        fix_commit=fix_case.fix_commit,
        category=fix_case.category,
        difficulty=fix_case.difficulty,
        severity=fix_case.severity,
        language=fix_case.language,
        pr_size=fix_case.pr_size,
        description=f"Introducing commit for: {fix_case.description}",
        expected_findings=rephrased,
        stats=fix_case.stats,
        case_type="introducing",
        introducing_commit=introducing_sha,
        pr_number=fix_case.pr_number,
    )


@click.command("gen-introducing-cases")
@click.option("--cases-dir", required=True, type=click.Path(exists=True), help="Fix cases dir")
@click.option("--repo-dir", required=True, type=click.Path(exists=True), help="Local repo clone")
@click.option("--output-dir", required=True, type=click.Path(), help="Output dir for new cases")
@click.option("--dry-run", is_flag=True, default=False, help="Print without writing")
@click.option("--limit", default=0, type=int, help="Max cases to process (0 = no limit)")
def gen_introducing_cases(
    cases_dir: str,
    repo_dir: str,
    output_dir: str,
    dry_run: bool,
    limit: int,
) -> None:
    """Generate introducing cases from existing fix cases using git blame."""
    cases = load_all_cases(Path(cases_dir))
    fix_cases = [c for c in cases if c.case_type == "fix" and c.expected_findings]
    click.echo(f"Found {len(fix_cases)} fix cases with expected findings")

    repo = Path(repo_dir)
    out = Path(output_dir)
    if not dry_run:
        out.mkdir(parents=True, exist_ok=True)

    found = 0
    skipped = 0
    for case in fix_cases:
        if limit > 0 and found >= limit:
            break

        sha = find_introducing_commit_via_blame(
            case.fix_commit, case.expected_findings, cwd=repo,
        )
        if sha is None:
            skipped += 1
            continue

        intro_case = generate_introducing_case(case, sha, cwd=repo)
        if intro_case is None:
            skipped += 1
            continue

        if dry_run:
            click.echo(f"  [dry-run] {intro_case.id}: blame -> {sha[:12]}")
        else:
            save_case(intro_case, out / f"{intro_case.id}.yaml")
            click.echo(f"  [saved] {intro_case.id}: blame -> {sha[:12]}")
        found += 1

    click.echo(f"Generated {found} introducing cases, skipped {skipped}")
