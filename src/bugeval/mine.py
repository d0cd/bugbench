"""Mine fix PRs from GitHub repos and build initial test cases."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bugeval.io import load_checkpoint, save_case, save_checkpoint
from bugeval.models import CaseKind, CaseStats, PRRelation, ReviewThread, TestCase

log = logging.getLogger(__name__)


class GhError(Exception):
    def __init__(self, command: list[str], stderr: str) -> None:
        self.command = command
        self.stderr = stderr
        super().__init__(f"gh command failed: {' '.join(command)}\n{stderr}")


@dataclass
class GitPRCandidate:
    """A PR candidate extracted from local git history."""

    pr_number: int
    sha: str
    title: str
    branch_name: str = ""
    commit_messages: str = ""
    author: str = ""
    date: str = ""
    lines_added: int = 0
    lines_deleted: int = 0
    files_changed: int = 0


_MERGE_PR_RE = re.compile(r"Merge pull request #(\d+) from [^/]+/(.+)")
_SQUASH_PR_RE = re.compile(r"^(.+?)\s*\(#(\d+)\)$")


_TRANSIENT_PATTERNS = (
    "rate limit",
    "500",
    "502",
    "503",
    "timed out",
    "connection",
)


def _get_commit_diff_stats(
    sha: str,
    repo_dir: Path,
) -> dict[str, int]:
    """Get lines added/deleted/files changed for a commit."""
    from bugeval.git_utils import GitError, run_git

    try:
        stat = run_git(
            "diff",
            "--shortstat",
            f"{sha}~1",
            sha,
            cwd=repo_dir,
        ).strip()
    except GitError:
        return {
            "lines_added": 0,
            "lines_deleted": 0,
            "files_changed": 0,
        }

    added = deleted = files = 0
    m = re.search(r"(\d+) file", stat)
    if m:
        files = int(m.group(1))
    m = re.search(r"(\d+) insertion", stat)
    if m:
        added = int(m.group(1))
    m = re.search(r"(\d+) deletion", stat)
    if m:
        deleted = int(m.group(1))
    return {
        "lines_added": added,
        "lines_deleted": deleted,
        "files_changed": files,
    }


def parse_git_prs(
    repo_dir: Path,
    since: str = "2023-01-01",
) -> list[GitPRCandidate]:
    """Extract PR candidates from local git history."""
    from bugeval.git_utils import GitError, run_git

    candidates: dict[int, GitPRCandidate] = {}

    # 1. Merge commits: "Merge pull request #N from org/branch"
    # Use %x01 as record separator to handle newlines in %b (body)
    try:
        merge_log = run_git(
            "log",
            "--all",
            "--merges",
            f"--since={since}",
            "--format=%x01%H%x00%s%x00%b%x00%an%x00%ai",
            cwd=repo_dir,
        )
    except GitError:
        merge_log = ""

    for entry in merge_log.strip().split("\x01"):
        if not entry.strip():
            continue
        parts = entry.split("\x00")
        if len(parts) < 5:
            continue
        sha, subject, body, author, date = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
            parts[3].strip(),
            parts[4].strip(),
        )
        m = _MERGE_PR_RE.match(subject)
        if not m:
            continue
        pr_num = int(m.group(1))
        branch = m.group(2)
        title = body.split("\n")[0] if body else ""

        # Get branch commit messages
        branch_msgs = ""
        try:
            parent1 = run_git(
                "rev-parse",
                f"{sha}^1",
                cwd=repo_dir,
            ).strip()
            branch_msgs = run_git(
                "log",
                "--format=%s",
                f"{parent1}..{sha}^2",
                cwd=repo_dir,
            ).strip()
        except GitError:
            pass

        stats = _get_commit_diff_stats(sha, repo_dir)

        candidates[pr_num] = GitPRCandidate(
            pr_number=pr_num,
            sha=sha,
            title=title,
            branch_name=branch,
            commit_messages=branch_msgs,
            author=author,
            date=date,
            **stats,
        )

    # 2. Squash merges: "Title (#N)" on first-parent
    try:
        squash_log = run_git(
            "log",
            "--first-parent",
            "--no-merges",
            f"--since={since}",
            "--format=%H%x00%s%x00%an%x00%ai",
            cwd=repo_dir,
        )
    except GitError:
        squash_log = ""

    for entry in squash_log.strip().split("\n"):
        if not entry.strip():
            continue
        parts = entry.split("\x00")
        if len(parts) < 4:
            continue
        sha, subject, author, date = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
            parts[3].strip(),
        )
        m = _SQUASH_PR_RE.match(subject)
        if not m:
            continue
        title = m.group(1)
        pr_num = int(m.group(2))
        if pr_num in candidates:
            continue  # Dedup: merge commit takes priority

        stats = _get_commit_diff_stats(sha, repo_dir)

        candidates[pr_num] = GitPRCandidate(
            pr_number=pr_num,
            sha=sha,
            title=title,
            branch_name="",
            commit_messages="",
            author=author,
            date=date,
            **stats,
        )

    return sorted(candidates.values(), key=lambda c: c.pr_number)


def _run_gh_once(*args: str, timeout: int = 60) -> str:
    """Run a single gh CLI command and return stdout."""
    cmd = ["gh", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GhError(cmd, f"Command timed out after {timeout}s")
    if result.returncode != 0:
        raise GhError(cmd, result.stderr)
    return result.stdout


def run_gh(
    *args: str,
    timeout: int = 60,
    retries: int = 3,
    backoff: float = 2.0,
) -> str:
    """Run gh CLI command with retry on transient failures."""
    last_err: GhError | None = None
    for attempt in range(retries + 1):
        try:
            return _run_gh_once(*args, timeout=timeout)
        except GhError as exc:
            last_err = exc
            stderr = exc.stderr.lower()
            if attempt < retries and any(p in stderr for p in _TRANSIENT_PATTERNS):
                wait = backoff * (2**attempt)
                log.warning(
                    "Transient gh error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    retries + 1,
                    wait,
                    str(exc)[:100],
                )
                time.sleep(wait)
            else:
                raise
    raise last_err  # type: ignore[misc]


# --- Fix-keyword detection ---

_FIX_KEYWORDS = re.compile(
    r"\b(fix(es|ed|ing)?|bug|patch|correct(ed|s|ing|ly)?"
    r"|resolve[sd]?|revert)\b",
    re.IGNORECASE,
)

_BRANCH_FIX_RE = re.compile(r"\b(fix|bug|hotfix|patch)\b", re.IGNORECASE)

_ISSUE_REF = re.compile(
    r"(close[sd]?|fix(e[sd])?|resolve[sd]?)\s*#(\d+)",
    re.IGNORECASE,
)

_REFERENCE_PATTERN = re.compile(
    r"(?:see|related\s+to|followup\s+to"
    r"|completes?\s+(?:fix\s+from)?)\s*#(\d+)",
    re.IGNORECASE,
)

_PR_CROSS_REF = re.compile(r"#(\d+)", re.IGNORECASE)

# --- Non-bug PR detection patterns ---

_CLIPPY_LINT_RE = re.compile(
    r"\b(clippy|lint|rustfmt|formatting)\b",
    re.IGNORECASE,
)

_TYPO_RE = re.compile(
    r"\b(typo|typos|spelling)\b",
    re.IGNORECASE,
)

_DOC_ONLY_RE = re.compile(
    r"^(fix|improve|update|correct)\b.*\b(doc[s.]?|documentation|help\s+messages?)\b",
    re.IGNORECASE,
)

_RELEASE_VERSION_RE = re.compile(
    r"\b(release|version\s+bump|v\d+\.\d+)\b",
    re.IGNORECASE,
)

_PERF_RE = re.compile(
    r"\b(perf|allocat\w*|optimization|reduce\s+memory)\b",
    re.IGNORECASE,
)

_DEPRECATION_RE = re.compile(
    r"\b(deprecat)\w*\b",
    re.IGNORECASE,
)

_ISSUE_NUM_RE = re.compile(r"#\d+")


def _is_non_bug_pr(title: str, body: str) -> bool:
    """Return True if the PR is clearly NOT a correctness bug fix.

    Conservative: better to miss a non-bug than to filter out a real bug.
    """
    t = title.strip()
    if not t:
        return False

    # Clippy / lint / format
    if _CLIPPY_LINT_RE.search(t):
        return True

    # Typos / spelling
    if _TYPO_RE.search(t):
        return True

    # Doc-only fixes
    if _DOC_ONLY_RE.search(t):
        return True

    # Release / version bump
    if _RELEASE_VERSION_RE.search(t):
        return True

    # Deprecation removal
    if _DEPRECATION_RE.search(t):
        return True

    # Perf optimization — only filter if no linked issue
    if _PERF_RE.search(t):
        combined = f"{t} {body}"
        if not _ISSUE_NUM_RE.search(combined):
            return True

    return False


def has_fix_signal(title: str, body: str, labels: list[str]) -> bool:
    """Check if PR has bug-fix signals."""
    bug_labels = {
        "bug",
        "fix",
        "regression",
        "defect",
        "bugfix",
        "hotfix",
    }
    if any(lbl.lower() in bug_labels for lbl in labels):
        return True
    text = f"{title} {body}"
    return bool(_FIX_KEYWORDS.search(text))


def has_local_fix_signal(candidate: GitPRCandidate) -> bool:
    """Check if a git PR candidate has fix signals from any local source."""
    if _FIX_KEYWORDS.search(candidate.title):
        return True
    if candidate.branch_name and _BRANCH_FIX_RE.search(candidate.branch_name):
        return True
    if candidate.commit_messages and _FIX_KEYWORDS.search(candidate.commit_messages):
        return True
    return False


def extract_closing_issues(text: str) -> list[int]:
    """Extract issue numbers from 'fixes #N', 'closes #N' etc."""
    return [int(m.group(3)) for m in _ISSUE_REF.finditer(text)]


def extract_referenced_issues(text: str) -> list[int]:
    """Extract issue numbers from 'see #N', 'related to #N' etc."""
    return [int(m.group(1)) for m in _REFERENCE_PATTERN.finditer(text)]


# --- GitHub API helpers ---


def fetch_fix_prs(
    repo: str,
    limit: int,
    since: str,
    seen: set[str] | None = None,
) -> tuple[list[dict[str, Any]], set[int]]:
    """Fetch merged PRs with fix signals.

    Returns (fix_prs, all_fetched_numbers). The caller should checkpoint
    all_fetched_numbers so re-runs don't re-evaluate the same PRs.
    If *seen* is provided, PRs in that set are excluded from fetching.
    """
    # Use light fields to avoid GitHub GraphQL 500k node limit.
    # Heavy fields (files, commits, statusCheckRollup) are fetched
    # per-PR later via GraphQL if needed.
    light_fields = (
        "number,title,body,labels,mergeCommit,baseRefName,headRefName,"
        "additions,deletions,changedFiles,mergedAt,author,reviewDecision"
    )
    args = [
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "merged",
        "--json",
        light_fields,
        "--limit",
        str(limit),
    ]
    if since:
        args.extend(["--search", f"merged:>{since}"])
    output = run_gh(*args)
    all_prs: list[dict[str, Any]] = json.loads(output)
    all_numbers = {int(pr["number"]) for pr in all_prs}

    results: list[dict[str, Any]] = []
    for pr in all_prs:
        if seen and str(pr["number"]) in seen:
            continue
        title = str(pr.get("title") or "")
        body = str(pr.get("body") or "")
        labels = [str(lbl.get("name", "")) for lbl in (pr.get("labels") or [])]
        additions = int(pr.get("additions") or 0)
        deletions = int(pr.get("deletions") or 0)
        total_lines = additions + deletions

        if total_lines < 1 or total_lines > 1000:
            continue
        pr_files = pr.get("files") or []
        file_names = [str(f.get("path", "")) for f in pr_files]
        if file_names and _is_non_code_only(file_names):
            continue

        # Skip dependency bump PRs (dependabot, renovate, version bumps)
        title_lower = title.lower()
        if title_lower.startswith("bump ") or title_lower.startswith("chore(deps"):
            continue
        author = str((pr.get("author") or {}).get("login", "")).lower()
        if author in ("dependabot", "dependabot[bot]", "renovate", "renovate[bot]"):
            continue

        if has_fix_signal(title, body, labels):
            if _is_non_bug_pr(title, body):
                continue
            results.append(pr)

    return results, all_numbers


def _is_non_code_only(files: list[str]) -> bool:
    non_code_patterns = {
        ".md",
        ".txt",
        ".yml",
        ".yaml",
        ".toml",
        ".lock",
        ".json",
    }
    non_code_dirs = {
        "docs/",
        ".github/",
        ".circleci/",
        ".gitlab-ci",
    }
    for f in files:
        ext = Path(f).suffix.lower()
        if ext not in non_code_patterns and not any(f.startswith(d) for d in non_code_dirs):
            return False
    return True


def fetch_pr_details_graphql(
    owner: str,
    name: str,
    pr_numbers: list[int],
) -> dict[int, dict[str, Any]]:
    """Batch-fetch rich PR details via GraphQL."""
    if not pr_numbers:
        return {}

    batch_size = 20
    all_results: dict[int, dict[str, Any]] = {}

    for i in range(0, len(pr_numbers), batch_size):
        batch = pr_numbers[i : i + batch_size]
        fragments = []
        for num in batch:
            fragments.append(f"""
  pr_{num}: pullRequest(number: {num}) {{
    number
    title
    body
    createdAt
    mergedAt
    mergeCommit {{ oid }}
    mergeMethod
    statusCheckRollup {{ state }}
    author {{ login }}
    commits(first: 100) {{
      nodes {{ commit {{ oid message }} }}
    }}
    reviews(first: 100) {{
      nodes {{ body state author {{ login }} }}
    }}
    reviewThreads(first: 100) {{
      nodes {{
        path line originalLine isResolved
        comments(first: 20) {{
          nodes {{ body author {{ login }} diffHunk }}
        }}
      }}
    }}
    comments(first: 100) {{
      nodes {{ body author {{ login }} }}
    }}
    closingIssuesReferences(first: 10) {{
      nodes {{
        number title body
        labels(first: 10) {{ nodes {{ name }} }}
      }}
    }}
  }}""")

        joined = "".join(fragments)
        query = f'query {{\n  repository(owner: "{owner}", name: "{name}") {{{joined}\n  }}\n}}'
        try:
            output = run_gh("api", "graphql", "-f", f"query={query}")
            data = json.loads(output)
            repo_data = data.get("data", {}).get("repository", {})
        except (GhError, json.JSONDecodeError):
            continue

        for num in batch:
            pr_data = repo_data.get(f"pr_{num}")
            if pr_data:
                all_results[num] = pr_data

    return all_results


def fetch_issue_details(
    repo: str,
    issue_numbers: list[int],
) -> dict[int, dict[str, Any]]:
    """Fetch issue bodies and labels."""
    results: dict[int, dict[str, Any]] = {}
    for num in issue_numbers:
        try:
            output = run_gh(
                "issue",
                "view",
                str(num),
                "--repo",
                repo,
                "--json",
                "number,title,body,labels",
            )
            results[num] = json.loads(output)
        except (GhError, json.JSONDecodeError):
            continue
    return results


def fetch_bug_issues(
    repo: str,
    limit: int,
    since: str,
) -> list[dict[str, Any]]:
    """Fetch closed issues with 'bug' label."""
    args = [
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "closed",
        "--label",
        "bug",
        "--json",
        "number,title,body,closedAt,labels",
        "--limit",
        str(limit),
    ]
    if since:
        args.extend(["--search", f"closed:>{since}"])
    output = run_gh(*args)
    return json.loads(output)


def fetch_closing_prs(
    owner: str,
    name: str,
    issue_numbers: list[int],
) -> dict[int, list[int]]:
    """For each issue, find PR numbers that closed it.

    Returns {issue: [pr_nums]}.
    """
    if not issue_numbers:
        return {}

    result: dict[int, list[int]] = {}
    batch_size = 20

    for i in range(0, len(issue_numbers), batch_size):
        batch = issue_numbers[i : i + batch_size]
        fragments = []
        for num in batch:
            fragments.append(f"""
  issue_{num}: issue(number: {num}) {{
    number
    timelineItems(itemTypes: [CLOSED_EVENT], first: 5) {{
      nodes {{
        ... on ClosedEvent {{
          closer {{
            ... on PullRequest {{
              number
              merged
            }}
          }}
        }}
      }}
    }}
  }}""")

        joined = "".join(fragments)
        query = f'query {{\n  repository(owner: "{owner}", name: "{name}") {{{joined}\n  }}\n}}'
        try:
            output = run_gh("api", "graphql", "-f", f"query={query}")
            data = json.loads(output)
            repo_data = data.get("data", {}).get("repository", {})
        except (GhError, json.JSONDecodeError):
            continue

        for num in batch:
            issue_data = repo_data.get(f"issue_{num}")
            if not issue_data:
                continue
            pr_nums: list[int] = []
            nodes = (issue_data.get("timelineItems") or {}).get("nodes") or []
            for node in nodes:
                closer = node.get("closer") or {}
                pr_num = closer.get("number")
                merged = closer.get("merged")
                if pr_num and merged:
                    pr_nums.append(pr_num)
            if pr_nums:
                result[num] = pr_nums

    return result


# --- PR relationship graph ---


def detect_cross_references(
    prs: list[dict[str, Any]],
) -> dict[int, list[int]]:
    """Detect PR cross-references (mentions of other PRs)."""
    refs: dict[int, list[int]] = {}
    all_numbers = {int(pr["number"]) for pr in prs}
    for pr in prs:
        num = int(pr["number"])
        text = f"{pr.get('title', '')} {pr.get('body', '')}"
        mentioned = {
            int(m) for m in _PR_CROSS_REF.findall(text) if int(m) in all_numbers and int(m) != num
        }
        if mentioned:
            refs[num] = sorted(mentioned)
    return refs


def detect_reverts(prs: list[dict[str, Any]]) -> dict[int, int]:
    """Detect revert PRs. Returns {reverting_pr: reverted_pr}."""
    reverts: dict[int, int] = {}
    revert_pattern = re.compile(r"revert.*?#(\d+)", re.IGNORECASE)
    for pr in prs:
        title = str(pr.get("title") or "")
        if "revert" in title.lower():
            match = revert_pattern.search(title)
            if match:
                reverts[int(pr["number"])] = int(match.group(1))
    return reverts


def build_pr_relations(
    fix_pr_number: int,
    all_prs_by_number: dict[int, dict[str, Any]],
    cross_refs: dict[int, list[int]],
    reverts: dict[int, int],
) -> list[PRRelation]:
    """Build relationship graph for a fix PR."""
    relations: list[PRRelation] = []
    fix_pr = all_prs_by_number.get(fix_pr_number)
    if not fix_pr:
        return relations

    for ref_num in cross_refs.get(fix_pr_number, []):
        ref_pr = all_prs_by_number.get(ref_num)
        if not ref_pr:
            continue
        role = "revert" if reverts.get(fix_pr_number) == ref_num else "related"
        relations.append(
            PRRelation(
                pr_number=ref_num,
                role=role,
                commit=str((ref_pr.get("mergeCommit") or {}).get("oid", "")),
                title=str(ref_pr.get("title", "")),
                merge_date=str(ref_pr.get("mergedAt") or ""),
                author=str((ref_pr.get("author") or {}).get("login", "")),
            )
        )

    return relations


# --- TestCase construction ---


def _compute_pr_size(additions: int, deletions: int) -> str:
    total = additions + deletions
    if total < 10:
        return "tiny"
    if total < 50:
        return "small"
    if total < 200:
        return "medium"
    if total < 500:
        return "large"
    return "xl"


def _detect_language(files: list[str]) -> str:
    ext_map = {
        ".rs": "rust",
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".go": "go",
        ".java": "java",
        ".leo": "leo",
    }
    counts: dict[str, int] = {}
    for f in files:
        lang = ext_map.get(Path(f).suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else "unknown"


def build_case_from_pr(
    repo: str,
    pr: dict[str, Any],
    case_id: str,
    graphql_data: dict[str, Any] | None = None,
    issue_data: dict[int, dict[str, Any]] | None = None,
    relations: list[PRRelation] | None = None,
    repo_language: str = "",
) -> TestCase:
    """Build a TestCase from a fix PR and optional enrichment data."""
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")
    merge_commit = str((pr.get("mergeCommit") or {}).get("oid", ""))
    additions = int(pr.get("additions") or 0)
    deletions = int(pr.get("deletions") or 0)
    files_count = int(pr.get("changedFiles") or 0)
    pr_files = pr.get("files") or []
    file_names = [str(f.get("path", "")) for f in pr_files]
    labels = [str(lbl.get("name", "")) for lbl in (pr.get("labels") or [])]
    pr_number = int(pr["number"])

    commit_messages: list[str] = []
    commit_shas: list[str] = []
    review_comments: list[str] = []
    review_threads: list[ReviewThread] = []
    discussion_comments: list[str] = []
    merge_method = ""
    ci_status = ""

    if graphql_data:
        for node in (graphql_data.get("commits") or {}).get("nodes") or []:
            sha = (node.get("commit") or {}).get("oid", "")
            if sha:
                commit_shas.append(sha)
            msg = (node.get("commit") or {}).get("message", "")
            if msg:
                commit_messages.append(msg)

        merge_method = str(graphql_data.get("mergeMethod") or "")
        status = graphql_data.get("statusCheckRollup") or {}
        ci_status = str(status.get("state") or "")

        for review in (graphql_data.get("reviews") or {}).get("nodes") or []:
            rb = str(review.get("body") or "").strip()
            state = str(review.get("state") or "")
            author = str((review.get("author") or {}).get("login", ""))
            if rb:
                prefix = f"[{author}:{state}] " if author or state else ""
                review_comments.append(f"{prefix}{rb}")
        for thread in (graphql_data.get("reviewThreads") or {}).get("nodes") or []:
            thread_comments: list[str] = []
            for comment in (thread.get("comments") or {}).get("nodes") or []:
                cb = str(comment.get("body") or "").strip()
                if cb:
                    review_comments.append(cb)
                    thread_comments.append(cb)
            review_threads.append(
                ReviewThread(
                    path=str(thread.get("path") or ""),
                    line=int(thread.get("line") or thread.get("originalLine") or 0),
                    is_resolved=bool(thread.get("isResolved", False)),
                    comments=thread_comments,
                )
            )

        for comment in (graphql_data.get("comments") or {}).get("nodes") or []:
            db = str(comment.get("body") or "").strip()
            if db:
                discussion_comments.append(db)

    linked_issues: list[int] = extract_closing_issues(f"{title} {body}")
    referenced_issues: list[int] = extract_referenced_issues(f"{title} {body}")
    issue_bodies: dict[int, str] = {}
    issue_labels_all: list[str] = list(labels)

    if graphql_data:
        closing_refs = (graphql_data.get("closingIssuesReferences") or {}).get("nodes") or []
        for node in closing_refs:
            inum = node.get("number")
            if inum and inum not in linked_issues:
                linked_issues.append(inum)
            ibody = str(node.get("body") or "")
            if inum and ibody:
                issue_bodies[inum] = ibody
            for lbl in (node.get("labels") or {}).get("nodes") or []:
                ln = str(lbl.get("name", ""))
                if ln and ln not in issue_labels_all:
                    issue_labels_all.append(ln)

    if issue_data:
        for inum, idata in issue_data.items():
            if inum in linked_issues and inum not in issue_bodies:
                issue_bodies[inum] = str(idata.get("body") or "")
            if inum in linked_issues:
                for lbl in idata.get("labels") or []:
                    ln = str(lbl.get("name", ""))
                    if ln and ln not in issue_labels_all:
                        issue_labels_all.append(ln)

    # Add the fix PR itself to relations with role="full_fix"
    fix_relation = PRRelation(
        pr_number=pr_number,
        role="full_fix",
        commit=merge_commit,
        title=title,
        merge_date=str(pr.get("mergedAt") or ""),
        author=str((pr.get("author") or {}).get("login", "")),
    )
    all_relations = [fix_relation] + (relations or [])

    detected_lang = _detect_language(file_names)
    language = detected_lang if detected_lang != "unknown" else (repo_language or "unknown")

    return TestCase(
        id=case_id,
        repo=repo,
        kind=CaseKind.bug,
        language=language,
        base_commit="",
        fix_commit=merge_commit,
        fix_pr_number=pr_number,
        fix_pr_title=title,
        fix_pr_body=body,
        fix_pr_commit_messages=commit_messages,
        fix_pr_commit_shas=commit_shas,
        fix_pr_merge_date=str(pr.get("mergedAt") or ""),
        fix_pr_review_comments=review_comments,
        fix_pr_review_threads=review_threads,
        fix_pr_discussion_comments=discussion_comments,
        fix_pr_merge_method=merge_method,
        fix_pr_ci_status=ci_status,
        linked_issues=linked_issues,
        issue_bodies=issue_bodies,
        issue_labels=issue_labels_all,
        referenced_issues=referenced_issues,
        related_prs=all_relations,
        stats=CaseStats(
            lines_added=additions,
            lines_deleted=deletions,
            files_changed=files_count or len(file_names),
        ),
        pr_size=_compute_pr_size(additions, deletions),
        source="pr-mining",
        fix_pr_files=file_names,
    )


def build_dedup_index(cases_dir: Path) -> dict[int, str]:
    """Scan all YAMLs once, return {fix_pr_number: case_id}."""
    index: dict[int, str] = {}
    for p in sorted(cases_dir.rglob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text())
            pr_num = data.get("fix_pr_number") if data else None
            if pr_num is not None:
                index[pr_num] = str(data.get("id", p.stem))
        except Exception:
            continue
    return index


def find_duplicate(
    cases_dir: Path,
    fix_pr_number: int,
    *,
    index: dict[int, str] | None = None,
) -> str | None:
    """Return case_id if a case with this fix_pr_number already exists, else None."""
    if index is not None:
        return index.get(fix_pr_number)
    # Fallback: scan (for single-call use in add_case.py)
    idx = build_dedup_index(cases_dir)
    return idx.get(fix_pr_number)


# --- Orchestration ---


def mine_repo(
    repo: str,
    limit: int,
    since: str,
    output_dir: Path,
    concurrency: int = 1,
) -> list[TestCase]:
    """Mine fix PRs from a repo and write TestCase YAMLs."""
    owner, name = repo.split("/", 1)
    repo_slug = name
    repo_dir = output_dir / repo_slug
    repo_dir.mkdir(parents=True, exist_ok=True)

    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
    repo_lang = ""
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text()) or {}
        repo_lang = (cfg.get("repos") or {}).get(repo, {}).get("language", "")

    checkpoint_path = repo_dir / ".mine_checkpoint.json"
    done = load_checkpoint(checkpoint_path)

    log.info(
        "Fetching fix PRs from %s (limit=%d, since=%s)",
        repo,
        limit,
        since,
    )
    prs, all_fetched = fetch_fix_prs(repo, limit, since, seen=done)
    log.info("Found %d fix PRs (%d total fetched)", len(prs), len(all_fetched))

    # Checkpoint all fetched PR numbers so re-runs skip them
    for n in all_fetched:
        done.add(str(n))
    save_checkpoint(done, checkpoint_path)

    prs_by_number = {int(pr["number"]): pr for pr in prs}
    cross_refs = detect_cross_references(prs)
    reverts = detect_reverts(prs)

    # prs already filtered to unseen in fetch_fix_prs
    pending_prs = prs
    log.info(
        "Processing %d pending PRs (%d already done)",
        len(pending_prs),
        len(done),
    )

    pending_numbers = [int(pr["number"]) for pr in pending_prs]
    graphql_details = fetch_pr_details_graphql(
        owner,
        name,
        pending_numbers,
    )

    all_issue_nums: set[int] = set()
    for pr in pending_prs:
        text = f"{pr.get('title', '')} {pr.get('body', '')}"
        all_issue_nums.update(extract_closing_issues(text))
    for _num, gql in graphql_details.items():
        closing_refs = (gql.get("closingIssuesReferences") or {}).get("nodes") or []
        for node in closing_refs:
            inum = node.get("number")
            if inum:
                all_issue_nums.add(inum)

    issue_details = fetch_issue_details(repo, sorted(all_issue_nums)) if all_issue_nums else {}

    existing = sorted(repo_dir.glob(f"{repo_slug}-*.yaml"))
    next_num = len(existing) + 1

    dedup_index = build_dedup_index(repo_dir)

    cases: list[TestCase] = []
    for pr in pending_prs:
        pr_num = int(pr["number"])

        dup = find_duplicate(repo_dir, pr_num, index=dedup_index)
        if dup:
            log.info("Skipping PR #%d: duplicate of %s", pr_num, dup)
            done.add(str(pr_num))
            save_checkpoint(done, checkpoint_path)
            continue

        case_id = f"{repo_slug}-{next_num:03d}"
        relations = build_pr_relations(
            pr_num,
            prs_by_number,
            cross_refs,
            reverts,
        )
        gql = graphql_details.get(pr_num)

        case = build_case_from_pr(
            repo=repo,
            pr=pr,
            case_id=case_id,
            graphql_data=gql,
            issue_data=issue_details,
            relations=relations,
            repo_language=repo_lang,
        )
        save_case(case, repo_dir / f"{case_id}.yaml")
        cases.append(case)
        dedup_index[pr_num] = case_id

        done.add(str(pr_num))
        save_checkpoint(done, checkpoint_path)
        next_num += 1

    log.info("Wrote %d new cases to %s", len(cases), repo_dir)
    return cases


def mine_from_issues(
    repo: str,
    limit: int,
    since: str,
    output_dir: Path,
) -> list[TestCase]:
    """Mine cases starting from bug-labeled issues and their closing PRs."""
    owner, name = repo.split("/", 1)
    repo_slug = name
    repo_dir = output_dir / repo_slug
    repo_dir.mkdir(parents=True, exist_ok=True)

    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
    repo_lang = ""
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text()) or {}
        repo_lang = (cfg.get("repos") or {}).get(repo, {}).get("language", "")

    # Use separate checkpoint for issue mining
    checkpoint_path = repo_dir / ".mine_issues_checkpoint.json"
    done = load_checkpoint(checkpoint_path)

    log.info(
        "Fetching bug-labeled issues from %s (limit=%d, since=%s)",
        repo,
        limit,
        since,
    )
    issues = fetch_bug_issues(repo, limit, since)
    log.info("Found %d bug-labeled issues", len(issues))

    # Skip already-processed issues
    pending = [iss for iss in issues if str(iss["number"]) not in done]
    if not pending:
        log.info("All issues already processed")
        return []

    # Find closing PRs for each issue
    issue_numbers = [int(iss["number"]) for iss in pending]
    closing_map = fetch_closing_prs(owner, name, issue_numbers)
    log.info("Found closing PRs for %d issues", len(closing_map))

    # Dedup against existing cases
    dedup_index = build_dedup_index(repo_dir)

    # Collect unique PR numbers to fetch details for
    pr_nums_to_fetch: list[int] = []
    for pr_nums in closing_map.values():
        for pr_num in pr_nums:
            if pr_num not in dedup_index and pr_num not in pr_nums_to_fetch:
                pr_nums_to_fetch.append(pr_num)

    if not pr_nums_to_fetch:
        log.info("All closing PRs already in dataset")
        # Checkpoint all issues as processed
        for iss in pending:
            done.add(str(iss["number"]))
        save_checkpoint(done, checkpoint_path)
        return []

    log.info("Fetching details for %d new PRs", len(pr_nums_to_fetch))

    # Fetch PR details via GraphQL
    graphql_details = fetch_pr_details_graphql(
        owner,
        name,
        pr_nums_to_fetch,
    )

    # Also need light PR data — fetch via REST
    pr_light: dict[int, dict[str, Any]] = {}
    for pr_num in pr_nums_to_fetch:
        try:
            output = run_gh(
                "pr",
                "view",
                str(pr_num),
                "--repo",
                repo,
                "--json",
                "number,title,body,labels,mergeCommit,"
                "additions,deletions,"
                "changedFiles,mergedAt,author,reviewDecision",
            )
            pr_light[pr_num] = json.loads(output)
        except (GhError, json.JSONDecodeError):
            log.warning("Failed to fetch PR #%d", pr_num)
            continue

    # Build issue body lookup
    issue_body_map: dict[int, dict[str, Any]] = {}
    for iss in pending:
        inum = int(iss["number"])
        issue_body_map[inum] = {
            "body": iss.get("body") or "",
            "labels": iss.get("labels") or [],
        }

    # Find next case number
    existing = sorted(repo_dir.glob(f"{repo_slug}-*.yaml"))
    next_num = len(existing) + 1

    cases: list[TestCase] = []
    for iss in pending:
        iss_num = int(iss["number"])
        pr_nums = closing_map.get(iss_num, [])

        for pr_num in pr_nums:
            dup = find_duplicate(
                repo_dir,
                pr_num,
                index=dedup_index,
            )
            if dup:
                log.info(
                    "Skipping PR #%d (issue #%d): duplicate of %s",
                    pr_num,
                    iss_num,
                    dup,
                )
                continue

            pr = pr_light.get(pr_num)
            if not pr:
                continue

            # Relaxed size filter for bug-labeled PRs (3000 vs 1000)
            additions = int(pr.get("additions") or 0)
            deletions = int(pr.get("deletions") or 0)
            total = additions + deletions
            if total < 3 or total > 3000:
                continue

            title = str(pr.get("title") or "")
            body = str(pr.get("body") or "")
            if _is_non_bug_pr(title, body):
                continue

            case_id = f"{repo_slug}-{next_num:03d}"
            gql = graphql_details.get(pr_num)

            # Only pass the issue that triggered this case
            relevant_issue_data = {
                iss_num: issue_body_map[iss_num],
            }

            case = build_case_from_pr(
                repo=repo,
                pr=pr,
                case_id=case_id,
                graphql_data=gql,
                issue_data=relevant_issue_data,
                repo_language=repo_lang,
            )
            case.source = "issue-mining"

            save_case(case, repo_dir / f"{case_id}.yaml")
            cases.append(case)
            dedup_index[pr_num] = case_id
            next_num += 1

        done.add(str(iss_num))
        save_checkpoint(done, checkpoint_path)

    log.info(
        "Wrote %d new cases from issue mining to %s",
        len(cases),
        repo_dir,
    )
    return cases


def mine_from_git(
    repo: str,
    repo_dir: Path,
    since: str,
    output_dir: Path,
) -> list[TestCase]:
    """Mine cases from local git history, enrich selectively from API."""
    owner, name = repo.split("/", 1)
    repo_slug = name
    cases_dir = output_dir / repo_slug
    cases_dir.mkdir(parents=True, exist_ok=True)

    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
    repo_lang = ""
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text()) or {}
        repo_lang = (cfg.get("repos") or {}).get(repo, {}).get("language", "")

    # Phase 1: Local git scan
    log.info(
        "Scanning local git history at %s (since %s)",
        repo_dir,
        since,
    )
    all_candidates = parse_git_prs(repo_dir, since=since)
    log.info("Found %d PRs in git history", len(all_candidates))

    # Filter by fix signals
    fix_candidates = [
        c
        for c in all_candidates
        if has_local_fix_signal(c) and not _is_non_bug_pr(c.title, c.commit_messages)
    ]
    log.info("%d candidates have fix signals", len(fix_candidates))

    # Size filter (relaxed: 1-3000)
    sized = [c for c in fix_candidates if 1 <= (c.lines_added + c.lines_deleted) <= 3000]
    log.info("%d candidates pass size filter", len(sized))

    # Dedup against existing cases
    dedup_index = build_dedup_index(cases_dir)
    new_candidates = [c for c in sized if c.pr_number not in dedup_index]
    log.info("%d new candidates after dedup", len(new_candidates))

    if not new_candidates:
        return []

    # Phase 2: Selective API enrichment
    pr_nums = [c.pr_number for c in new_candidates]
    log.info("Fetching API details for %d PRs", len(pr_nums))
    graphql_details = fetch_pr_details_graphql(owner, name, pr_nums)

    # Build light PR dicts from local git data + GraphQL enrichment
    pr_light: dict[int, dict[str, Any]] = {}
    for c in new_candidates:
        pr_data: dict[str, Any] = {
            "number": c.pr_number,
            "title": c.title,
            "body": "",
            "mergeCommit": {"oid": c.sha},
            "additions": c.lines_added,
            "deletions": c.lines_deleted,
            "changedFiles": c.files_changed,
            "mergedAt": c.date,
            "author": {"login": c.author},
            "labels": [],
            "files": [],
        }
        # Enrich from GraphQL
        gql = graphql_details.get(c.pr_number, {})
        if gql:
            pr_data["body"] = gql.get("body") or ""
            pr_data["mergedAt"] = gql.get("mergedAt") or c.date
            mc = gql.get("mergeCommit") or {}
            if mc.get("oid"):
                pr_data["mergeCommit"]["oid"] = mc["oid"]
        pr_light[c.pr_number] = pr_data

    # Collect linked issues
    all_issue_nums: set[int] = set()
    for c in new_candidates:
        pr = pr_light[c.pr_number]
        text = f"{pr['title']} {pr['body']}"
        all_issue_nums.update(extract_closing_issues(text))
    for gql in graphql_details.values():
        nodes = (gql.get("closingIssuesReferences") or {}).get("nodes") or []
        for node in nodes:
            inum = node.get("number")
            if inum:
                all_issue_nums.add(inum)

    issue_details = fetch_issue_details(repo, sorted(all_issue_nums)) if all_issue_nums else {}

    # Build cases
    existing = sorted(cases_dir.glob(f"{repo_slug}-*.yaml"))
    next_num = len(existing) + 1

    cases: list[TestCase] = []
    for c in new_candidates:
        pr = pr_light.get(c.pr_number)
        if not pr:
            continue

        title = str(pr.get("title") or "")
        body = str(pr.get("body") or "")

        # Re-check with enriched body
        if _is_non_bug_pr(title, body):
            continue

        case_id = f"{repo_slug}-{next_num:03d}"
        gql = graphql_details.get(c.pr_number)

        # Filter issue_data to only linked issues for this case
        linked = set(extract_closing_issues(f"{title} {body}"))
        if gql:
            for node in (gql.get("closingIssuesReferences") or {}).get("nodes") or []:
                inum = node.get("number")
                if inum:
                    linked.add(inum)
        relevant_issues = {k: v for k, v in issue_details.items() if k in linked}

        case = build_case_from_pr(
            repo=repo,
            pr=pr,
            case_id=case_id,
            graphql_data=gql,
            issue_data=relevant_issues,
            repo_language=repo_lang,
        )
        case.source = "git-mining"

        save_case(case, cases_dir / f"{case_id}.yaml")
        cases.append(case)
        dedup_index[c.pr_number] = case_id
        next_num += 1

    log.info(
        "Wrote %d new cases from git mining to %s",
        len(cases),
        cases_dir,
    )
    return cases
