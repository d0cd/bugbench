"""GitHub scraper using gh CLI to fetch bug issues and PRs."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from bugeval.models import (
    Candidate,
    CaseStats,
    ExpectedFinding,
    PRSize,
    ScrapeState,
)


class GhError(Exception):
    """Raised when a gh CLI command fails."""

    def __init__(self, command: list[str], stderr: str) -> None:
        self.command = command
        self.stderr = stderr
        super().__init__(f"gh command failed: {' '.join(command)}\n{stderr}")


def run_gh(*args: str) -> str:
    """Run a gh CLI command and return stdout. Raises GhError on failure."""
    cmd = ["gh", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GhError(cmd, result.stderr)
    return result.stdout


def fetch_bug_issues(
    repo: str,
    limit: int = 200,
    labels: list[str] | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch closed bug issues from a repo across multiple labels."""
    if labels is None:
        labels = ["bug", "fix", "regression", "defect"]

    all_issues: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()

    for label in labels:
        try:
            args = [
                "issue",
                "list",
                "--repo",
                repo,
                "--label",
                label,
                "--state",
                "closed",
                "--json",
                "number,title,body,labels,closedAt",
                "--limit",
                str(limit),
            ]
            if since:
                args.extend(["--search", f"created:>{since}"])
            output = run_gh(*args)
            issues: list[dict[str, Any]] = json.loads(output)
            for issue in issues:
                num = issue.get("number")
                if num not in seen_numbers:
                    seen_numbers.add(num)  # type: ignore[arg-type]
                    all_issues.append(issue)
        except GhError:
            continue  # label may not exist in this repo

    return all_issues


def fetch_fix_prs(repo: str, limit: int = 200, since: str | None = None) -> list[dict[str, Any]]:
    """Fetch merged PRs from a repo."""
    args = [
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "merged",
        "--json",
        "number,title,body,labels,mergeCommit,baseRefName,headRefName,files,"
        "additions,deletions,changedFiles,closedIssues",
        "--limit",
        str(limit),
    ]
    if since:
        args.extend(["--search", f"created:>{since}"])
    output = run_gh(*args)
    return json.loads(output)  # type: ignore[no-any-return]


def fetch_pr_diff(repo: str, pr_number: int) -> list[dict[str, Any]]:
    """Fetch per-file diffs for a PR via GitHub API."""
    owner, name = repo.split("/", 1)
    output = run_gh("api", f"repos/{owner}/{name}/pulls/{pr_number}/files")
    return json.loads(output)  # type: ignore[no-any-return]


def _parse_hunk_ranges(patch: str) -> list[tuple[int, int]]:
    """Parse unified diff patch to extract changed line ranges (new-file line numbers)."""
    ranges: list[tuple[int, int]] = []
    pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
    for match in pattern.finditer(patch):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) else 1
        end = start + max(count - 1, 0)
        ranges.append((start, end))
    return ranges


def extract_expected_findings(
    pr_diff_files: list[dict[str, Any]],
) -> list[ExpectedFinding]:
    """Auto-extract expected findings from PR diff files (marked [auto] for human review)."""
    findings: list[ExpectedFinding] = []
    for file_data in pr_diff_files:
        filename = str(file_data.get("filename", ""))
        patch = str(file_data.get("patch") or "")
        if not patch:
            continue

        ranges = _parse_hunk_ranges(patch)
        for start, _end in ranges[:3]:  # limit to first 3 hunks per file
            summary = f"Changed lines starting at {start}"
            for line in patch.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    summary = line[1:].strip()[:80]
                    break

            findings.append(
                ExpectedFinding(
                    file=filename,
                    line=start,
                    summary=f"[auto] {summary}",
                )
            )

    return findings


def _extract_issue_numbers(text: str) -> set[int]:
    """Extract issue numbers referenced by fix/close patterns."""
    pattern = re.compile(
        r"(?:fix(?:es)?|close[sd]?|resolve[sd]?)\s+#(\d+)",
        re.IGNORECASE,
    )
    return {int(m.group(1)) for m in pattern.finditer(text)}


def link_issues_to_prs(
    issues: list[dict[str, Any]], prs: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Match issues to PRs via closedIssues field, PR body refs, or PR title refs."""
    issue_by_number: dict[int, dict[str, Any]] = {
        issue["number"]: issue
        for issue in issues  # type: ignore[index]
    }
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    matched_issue_numbers: set[int] = set()

    for pr in prs:
        linked_issue: dict[str, Any] | None = None

        # Method 1: closedIssues field (most reliable)
        closed_issues = pr.get("closedIssues") or []
        for ci in closed_issues:
            num = ci.get("number")
            if num in issue_by_number and num not in matched_issue_numbers:
                linked_issue = issue_by_number[num]
                matched_issue_numbers.add(num)
                break

        # Method 2: Parse PR body + title for fixes/closes references
        if linked_issue is None:
            body = str(pr.get("body") or "")
            title = str(pr.get("title") or "")
            referenced = _extract_issue_numbers(body) | _extract_issue_numbers(title)
            for num in sorted(referenced):
                if num in issue_by_number and num not in matched_issue_numbers:
                    linked_issue = issue_by_number[num]
                    matched_issue_numbers.add(num)
                    break

        if linked_issue is not None:
            pairs.append((linked_issue, pr))

    return pairs


def score_candidate(issue: dict[str, Any], pr: dict[str, Any]) -> tuple[float, list[str]]:
    """Score a candidate (issue+PR pair). Returns (confidence, signals)."""
    confidence = 0.0
    signals: list[str] = []

    # Has bug label
    labels = [str(lbl.get("name", "")) for lbl in (issue.get("labels") or [])]
    bug_labels = {"bug", "fix", "regression", "defect", "bugfix"}
    if any(lbl.lower() in bug_labels for lbl in labels):
        confidence += 0.3
        signals.append("has_bug_label")

    # PR references this issue
    closed_issues = pr.get("closedIssues") or []
    issue_num = issue.get("number")
    body = str(pr.get("body") or "")
    title = str(pr.get("title") or "")
    referenced = _extract_issue_numbers(body) | _extract_issue_numbers(title)
    if any(ci.get("number") == issue_num for ci in closed_issues) or issue_num in referenced:
        confidence += 0.2
        signals.append("pr_references_issue")

    # Small diff
    additions = int(pr.get("additions") or 0)
    deletions = int(pr.get("deletions") or 0)
    if additions + deletions < 200:
        confidence += 0.1
        signals.append("small_diff")

    # Fix keywords in PR title
    pr_title_lower = title.lower()
    fix_keywords = {"fix", "bug", "patch", "correct", "resolve", "regression"}
    if any(kw in pr_title_lower for kw in fix_keywords):
        confidence += 0.1
        signals.append("fix_keywords_in_title")

    # Has linked issue (any issue, not necessarily this one)
    # Skip if pr_references_issue already credited for this specific issue
    if "pr_references_issue" not in signals and (closed_issues or referenced):
        confidence += 0.2
        signals.append("has_linked_issue")

    return min(confidence, 1.0), signals


_EXTENSION_MAP: dict[str, str] = {
    ".rs": "rust",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".java": "java",
    ".cpp": "c++",
    ".cc": "c++",
    ".c": "c",
    ".h": "c",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin",
    ".sol": "solidity",
    ".leo": "leo",
}


def detect_language(files: list[str]) -> str:
    """Detect dominant language from file list based on extensions."""
    counts: dict[str, int] = {}
    for f in files:
        suffix = Path(f).suffix.lower()
        lang = _EXTENSION_MAP.get(suffix)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1

    if not counts:
        return "unknown"
    return max(counts, key=lambda k: counts[k])


def compute_pr_size(additions: int, deletions: int) -> PRSize:
    """Compute PR size bucket from total line change count."""
    total = additions + deletions
    if total < 10:
        return PRSize.tiny
    if total < 50:
        return PRSize.small
    if total < 200:
        return PRSize.medium
    if total < 500:
        return PRSize.large
    return PRSize.xl


def build_candidates(
    repo: str, issues: list[dict[str, Any]], prs: list[dict[str, Any]]
) -> list[Candidate]:
    """Build Candidate objects from linked issue/PR pairs."""
    pairs = link_issues_to_prs(issues, prs)
    candidates: list[Candidate] = []

    for issue, pr in pairs:
        confidence, signals = score_candidate(issue, pr)

        merge_commit = str((pr.get("mergeCommit") or {}).get("oid", ""))
        additions = int(pr.get("additions") or 0)
        deletions = int(pr.get("deletions") or 0)
        changed_files_count = int(pr.get("changedFiles") or 0)

        pr_files: list[dict[str, Any]] = pr.get("files") or []
        file_names = [str(f.get("path", "")) for f in pr_files]

        language = detect_language(file_names)
        pr_size = compute_pr_size(additions, deletions)

        candidates.append(
            Candidate(
                repo=repo,
                pr_number=int(pr["number"]),
                fix_commit=merge_commit,
                base_commit=None,
                head_commit=None,
                confidence=confidence,
                signals=signals,
                title=str(pr.get("title", "")),
                body=str(pr.get("body") or ""),
                labels=[str(lbl.get("name", "")) for lbl in (pr.get("labels") or [])],
                files_changed=file_names,
                diff_stats=CaseStats(
                    lines_added=additions,
                    lines_deleted=deletions,
                    files_changed=changed_files_count,
                    hunks=0,
                ),
                expected_findings=[],
                language=language,
                pr_size=pr_size,
            )
        )

    return candidates


def load_scrape_state(path: Path) -> ScrapeState | None:
    """Load scrape state from a YAML file. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    with open(path) as f:
        data = yaml.safe_load(f)
    return ScrapeState(**data)


def save_scrape_state(state: ScrapeState, path: Path) -> None:
    """Save scrape state to a YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(state.model_dump(mode="json"), f, sort_keys=False)


def filter_already_processed(
    prs: list[dict[str, Any]], state: ScrapeState | None
) -> list[dict[str, Any]]:
    """Filter out PRs that have already been processed."""
    if state is None:
        return prs
    processed = set(state.processed_pr_numbers)
    return [pr for pr in prs if pr.get("number") not in processed]
