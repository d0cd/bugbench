"""CLI command: mine-candidates — mine bug-fix commits from local git history."""

from __future__ import annotations

from pathlib import Path

import click

from bugeval.git_miner import (
    build_git_candidates,
    detect_fix_keywords,
    parse_fix_commits,
    score_git_candidate,
)
from bugeval.github_scraper import enrich_git_candidates_with_github
from bugeval.io import save_candidates


@click.command("mine-candidates")
@click.option(
    "--repo-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to local git repository checkout.",
)
@click.option(
    "--repo-name",
    default=None,
    help="Repository name (default: directory name).",
)
@click.option("--branch", default="main", show_default=True, help="Branch to mine.")
@click.option("--limit", default=500, show_default=True, help="Maximum commits to inspect.")
@click.option(
    "--min-confidence",
    default=0.3,
    show_default=True,
    type=float,
    help="Minimum confidence score to include a candidate.",
)
@click.option(
    "--output-dir",
    default="candidates",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write candidate YAML files.",
)
@click.option(
    "--use-llm",
    is_flag=True,
    default=False,
    help="Use LLM fallback for introducing-commit lookup.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print candidates that would be saved without writing.",
)
@click.option(
    "--enrich-from-github",
    is_flag=True,
    default=False,
    help="Cross-reference top commits with GitHub PRs to get labels, body, and diff findings.",
)
@click.option(
    "--enrich-top-n",
    default=300,
    show_default=True,
    type=int,
    help="Number of top candidates (by confidence) to enrich from GitHub.",
)
def mine_candidates(
    repo_dir: Path,
    repo_name: str | None,
    branch: str,
    limit: int,
    min_confidence: float,
    output_dir: Path,
    use_llm: bool,
    dry_run: bool,
    enrich_from_github: bool,
    enrich_top_n: int,
) -> None:
    """Mine bug-fix commits from a local git repository and save as candidates."""
    name = repo_name or repo_dir.name

    click.echo(f"Scanning {name} on branch {branch} (limit={limit})...")
    commits = parse_fix_commits(cwd=repo_dir, branch=branch, limit=limit)
    click.echo(f"Found {len(commits)} commits to evaluate.")

    # Filter by fix keywords before expensive introducing-commit search
    fix_commits = [c for c in commits if detect_fix_keywords(c["message"])]
    click.echo(f"{len(fix_commits)} commits match fix keywords.")

    candidates = build_git_candidates(repo=name, commits=fix_commits, cwd=repo_dir)

    # LLM fallback for candidates missing an introducing commit
    if use_llm:
        try:
            import anthropic

            client = anthropic.Anthropic()
            from bugeval.git_miner import llm_link_introducing_commit
            from bugeval.git_utils import run_git

            for i, cand in enumerate(candidates):
                if cand.base_commit is None:
                    try:
                        diff = run_git("show", cand.fix_commit, cwd=repo_dir)
                        git_log = run_git("log", "--format=%H %s", "-50", cwd=repo_dir)
                        introducing = llm_link_introducing_commit(
                            client, cand.fix_commit, diff, git_log
                        )
                        if introducing:
                            candidates[i] = cand.model_copy(update={"base_commit": introducing})
                            # Recompute confidence
                            commit_dict = next(
                                (c for c in fix_commits if c["sha"] == cand.fix_commit), {}
                            )
                            confidence, signals = score_git_candidate(
                                commit_dict, has_introducing=True
                            )
                            candidates[i] = candidates[i].model_copy(
                                update={"confidence": confidence, "signals": signals}
                            )
                    except Exception as exc:
                        click.echo(f"[llm] {cand.fix_commit[:12]}: {exc}", err=True)
        except ImportError:
            click.echo("LLM fallback skipped: anthropic package not available.", err=True)

    # Optionally enrich top candidates with GitHub PR metadata before confidence filter
    if enrich_from_github:
        # Sort by confidence descending so we enrich the best candidates first
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        click.echo(f"Enriching top {min(enrich_top_n, len(candidates))} candidates from GitHub...")
        candidates = enrich_git_candidates_with_github(name, candidates, top_n=enrich_top_n)

    # Filter by min confidence
    filtered = [c for c in candidates if c.confidence >= min_confidence]
    click.echo(f"{len(filtered)} candidates above min_confidence={min_confidence}.")

    if dry_run:
        for cand in filtered:
            click.echo(
                f"  [dry-run] {cand.fix_commit[:12]} confidence={cand.confidence:.2f} "
                f"signals={cand.signals}"
            )
        click.echo(f"Would save {len(filtered)} candidates to {output_dir}/")
        return

    if not filtered:
        click.echo("No candidates to save.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{name.replace('/', '-')}-git-candidates.yaml"
    save_candidates(filtered, out_path)
    click.echo(f"Saved {len(filtered)} candidates → {out_path}")
