"""Build ground truth via diff intersection."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from bugeval.blame import parse_diff_deleted_lines
from bugeval.git_utils import GitError, run_git
from bugeval.io import load_cases, load_checkpoint, save_case, save_checkpoint
from bugeval.models import BugCategory, BuggyLine, GroundTruth, TestCase

log = logging.getLogger(__name__)

_checkpoint_lock = threading.Lock()

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

_LINE_DRIFT_TOLERANCE = 3

# Extensions used for test expectation / golden output files.
# These contain expected compiler output, not source code a reviewer should flag.
_TEST_EXPECTATION_EXTS = {
    ".out",
    ".expected",
    ".golden",
    ".snapshot",
    ".stderr",
    ".stdout",
}

_NON_SOURCE_FILES = {"Cargo.lock", "package-lock.json", "yarn.lock", "poetry.lock"}
_NON_SOURCE_EXTS = {".lock", ".sum"}  # go.sum, etc.
_CI_PREFIXES = (".github/workflows/", ".circleci/")


def _is_non_source_file(path: str) -> bool:
    """Return True if *path* is a non-source file (lock file, CI config, etc.)."""
    p = Path(path)
    if p.name in _NON_SOURCE_FILES:
        return True
    if p.suffix.lower() in _NON_SOURCE_EXTS:
        return True
    normalized = path.replace("\\", "/")
    if any(normalized.startswith(prefix) for prefix in _CI_PREFIXES):
        return True
    return False


def _is_test_expectation_file(path: str) -> bool:
    """Return True if *path* is a test expectation file, not real source."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in _TEST_EXPECTATION_EXTS:
        return True
    # Files under tests/ or test/ with expectation-like extensions
    parts_lower = [part.lower() for part in p.parts]
    if ("tests" in parts_lower or "test" in parts_lower) and ext in {
        ".out",
        ".expected",
        ".golden",
        ".snapshot",
        ".stderr",
        ".stdout",
    }:
        return True
    return False


_COMMENT_LINE_RE = re.compile(r"^\s*(//|///|/\*|\*|#\s)")
_IMPORT_LINE_RE = re.compile(
    r"^\s*(use |mod |pub use |pub mod |import |from .+ import )",
)
_CONFIG_LINE_RE = re.compile(
    r"^\s*(version\s*=|name\s*=|edition\s*=|\w[\w-]*\s*=\s*\{)",
)
_ATTRIBUTE_LINE_RE = re.compile(r"^\s*#\[")
_BLANK_LINE_RE = re.compile(r"^\s*$")


def classify_line_content(content: str) -> str:
    """Classify a line of code by its content type."""
    if _BLANK_LINE_RE.match(content):
        return "blank"
    if _COMMENT_LINE_RE.match(content):
        return "comment"
    if _IMPORT_LINE_RE.match(content):
        return "import"
    if _CONFIG_LINE_RE.match(content):
        return "config"
    if _ATTRIBUTE_LINE_RE.match(content):
        return "attribute"
    return "code"


def parse_diff_added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """Parse unified diff to extract added lines with new-file line numbers."""
    result: dict[str, list[tuple[int, str]]] = {}
    current_file: str | None = None
    new_line = 0

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+++ /dev/null"):
            current_file = None
        elif line.startswith("--- "):
            pass  # skip old-file header
        else:
            hunk_match = _HUNK_RE.match(line)
            if hunk_match:
                new_line = int(hunk_match.group(1))
            elif current_file is not None:
                if line.startswith("+"):
                    content = line[1:]
                    result.setdefault(current_file, []).append((new_line, content))
                    new_line += 1
                elif line.startswith("-"):
                    pass  # deletions don't move new-file counter
                else:
                    new_line += 1

    return result


def _parse_fix_deleted_content(fix_diffs: list[str]) -> dict[str, set[str]]:
    """Extract deleted line content from fix diffs, keyed by file path."""
    result: dict[str, set[str]] = {}
    for fd in fix_diffs:
        current_file: str | None = None
        for line in fd.splitlines():
            if line.startswith("--- a/"):
                current_file = line[6:]
            elif line.startswith("--- /dev/null"):
                current_file = None
            elif line.startswith("+++ "):
                pass
            elif current_file is not None and line.startswith("-"):
                text = line[1:].strip()
                if text:
                    result.setdefault(current_file, set()).add(text)
    return result


def _match_files(
    intro_files: set[str],
    fix_files: set[str],
) -> dict[str, str]:
    """Build intro_path -> fix_path mapping, using basename for renames."""
    mapping: dict[str, str] = {}
    for ifile in intro_files:
        if ifile in fix_files:
            mapping[ifile] = ifile
            continue
        # Basename fallback for directory renames
        ibase = ifile.rsplit("/", 1)[-1]
        for ffile in fix_files:
            fbase = ffile.rsplit("/", 1)[-1]
            if ibase == fbase:
                mapping[ifile] = ffile
                break
    return mapping


def compute_buggy_lines(
    introducing_diff: str,
    fix_diffs: list[str],
    fix_pr_number: int = 0,
) -> list[BuggyLine]:
    """Intersect lines added by introducing commit with lines changed by fixes.

    Uses three strategies:
    1. Line-number match within ±tolerance on same file path
    2. Line-number match with basename file matching (for renames)
    3. Content match — exact text of introduced line appears in fix deletions
    """
    added = {
        f: lines
        for f, lines in parse_diff_added_lines(introducing_diff).items()
        if not _is_non_source_file(f)
    }
    if not added:
        return []

    # Collect deleted line numbers from fix diffs
    fix_deleted: dict[str, set[int]] = {}
    for fd in fix_diffs:
        for file_path, line_nums in parse_diff_deleted_lines(fd).items():
            fix_deleted.setdefault(file_path, set()).update(line_nums)

    # Collect deleted line content for content-match fallback
    fix_deleted_content = _parse_fix_deleted_content(fix_diffs)

    # Build file mapping (handles renames via basename)
    file_mapping = _match_files(set(added.keys()), set(fix_deleted.keys()))
    content_mapping = _match_files(
        set(added.keys()),
        set(fix_deleted_content.keys()),
    )

    matched_keys: set[tuple[str, int]] = set()
    result: list[BuggyLine] = []

    # Strategy 1+2: Line-number match (exact path + basename rename)
    for intro_path, added_lines in added.items():
        fix_path = file_mapping.get(intro_path)
        if not fix_path:
            continue
        deleted_set = fix_deleted[fix_path]
        is_expectation = _is_test_expectation_file(intro_path)
        for line_num, content in added_lines:
            if line_num <= 0:
                continue
            if any(dl > 0 and abs(line_num - dl) <= _LINE_DRIFT_TOLERANCE for dl in deleted_set):
                result.append(
                    BuggyLine(
                        file=intro_path,
                        line=line_num,
                        content=content,
                        is_test_expectation=is_expectation,
                        line_type=classify_line_content(content),
                        fix_pr_number=fix_pr_number,
                    )
                )
                matched_keys.add((intro_path, line_num))

    # Strategy 3: Content match fallback for unmatched lines
    for intro_path, added_lines in added.items():
        fix_path = content_mapping.get(intro_path)
        if not fix_path:
            continue
        deleted_texts = fix_deleted_content[fix_path]
        is_expectation = _is_test_expectation_file(intro_path)
        for line_num, content in added_lines:
            if line_num <= 0:
                continue
            if (intro_path, line_num) in matched_keys:
                continue  # already matched by line number
            stripped = content.strip()
            if not stripped:
                continue  # skip blank lines
            if stripped in deleted_texts:
                log.info(
                    "content-match fallback: %s:%d matched deleted text in fix",
                    intro_path,
                    line_num,
                )
                result.append(
                    BuggyLine(
                        file=intro_path,
                        line=line_num,
                        content=content,
                        is_test_expectation=is_expectation,
                        line_type=classify_line_content(content),
                        fix_pr_number=fix_pr_number,
                    )
                )

    return result


_BUG_KEYWORDS = {
    "bug",
    "fix",
    "error",
    "crash",
    "panic",
    "wrong",
    "incorrect",
    "broken",
    "fail",
    "issue",
    "regression",
    "mishandl",
    "overflow",
    "underflow",
    "null",
    "missing",
    "invalid",
}


def _looks_like_bug_report(text: str) -> bool:
    """Check if text contains bug-related keywords (not a feature request)."""
    lower = text.lower()
    return any(kw in lower for kw in _BUG_KEYWORDS)


def extract_bug_description(case: TestCase) -> tuple[str, str]:
    """Extract the best bug description from available metadata.

    Priority:
    1. Fix PR body (directly describes what was fixed)
    2. Fix PR title (concise summary of the fix)
    3. Issue body ONLY if it looks like a bug report (not a feature request)
    4. Fix PR commit messages
    5. Fix PR review comments that mention the bug
    """
    # Fix PR body is the most reliable source — it describes the fix
    if case.fix_pr_body and case.fix_pr_body.strip():
        body = case.fix_pr_body.strip()
        # Skip very short bodies (e.g., "LGTM" or just a link)
        if len(body) > 20:
            return body, "pr_body"

    # Fix PR title
    if case.fix_pr_title and case.fix_pr_title.strip():
        return case.fix_pr_title.strip(), "pr_title"

    # Issue bodies — only if they look like actual bug reports
    if case.issue_bodies:
        for _num, body in sorted(
            case.issue_bodies.items(),
            key=lambda x: len(x[1]),
            reverse=True,
        ):
            if body.strip() and _looks_like_bug_report(body):
                return body.strip(), "issue"

    # Commit messages
    if case.fix_pr_commit_messages:
        msg = case.fix_pr_commit_messages[0]
        if msg.strip():
            return msg.strip(), "commit_msg"

    # Review comments that mention the bug
    if case.fix_pr_review_comments:
        for comment in case.fix_pr_review_comments:
            if _looks_like_bug_report(comment) and len(comment.strip()) > 30:
                return comment.strip(), "review_comment"

    return "", ""


def compute_metadata(case: TestCase) -> dict[str, Any]:
    """Compute derived metadata (latency, authorship)."""
    meta: dict[str, Any] = {}

    # Bug latency
    if case.introducing_pr_merge_date and case.fix_pr_merge_date:
        try:
            intro_dt = _parse_date(case.introducing_pr_merge_date)
            fix_dt = _parse_date(case.fix_pr_merge_date)
            meta["bug_latency_days"] = (fix_dt - intro_dt).days
        except (ValueError, TypeError):
            pass

    # Same author fix
    fix_author = _get_fix_author(case)
    if case.introducing_pr_author and fix_author:
        meta["same_author_fix"] = case.introducing_pr_author.lower() == fix_author.lower()

    return meta


def _parse_date(date_str: str) -> datetime:
    # Try ISO format with timezone
    date_str = date_str.strip()
    if date_str.endswith("Z"):
        date_str = date_str[:-1] + "+00:00"
    return datetime.fromisoformat(date_str)


def _get_fix_author(case: TestCase) -> str:
    for pr in case.related_prs:
        if pr.role in ("full_fix", "partial_fix") and pr.author:
            return pr.author
    return ""


_PARSER_RE = re.compile(
    r"\b(pars(e[sd]?|er|ing)|tokeniz\w*|lexer|syntax\s+error|grammar)\b",
    re.IGNORECASE,
)
_CODEGEN_RE = re.compile(
    r"\b(code\s*gen\w*|emit\w*|lowering|monomorphiz\w*|flatten\w*"
    r"|destructur\w*|inlin\w+|\bssa\b|code\s+generation)\b",
    re.IGNORECASE,
)
_COMPILER_PASS_RE = re.compile(
    r"\b(pass\b|transform\w*|visitor|traversal|unroll\w*"
    r"|const\s+prop\w*|write\s*transform\w*)\b",
    re.IGNORECASE,
)
_INTERPRETER_RE = re.compile(
    r"\b(interpret\w*|debugger)\b",
    re.IGNORECASE,
)
_FORMATTER_RE = re.compile(
    r"\b(leo[\s-]?fmt|format(?:t?er)|pretty[\s-]?print)\b",
    re.IGNORECASE,
)
_CLI_RE = re.compile(
    r"\b(cli\b|command[\s-]?line|clap\b"
    r"|leo\s+(?:run|build|deploy|execute|test|clean|add|update|devn))\b",
    re.IGNORECASE,
)
_CONCURRENCY_RE = re.compile(
    r"\b(race\s+condition|concurren\w*|deadlock|(?<![b.])lock(?:ing)?(?!file)|mutex|atomic)\b",
    re.IGNORECASE,
)
_RUNTIME_RE = re.compile(
    r"\b(overflow|underflow|panic|crash|abort)\b",
    re.IGNORECASE,
)
_MEMORY_RE = re.compile(
    r"\b(memory|leak|use-after|dangling|null)\b",
    re.IGNORECASE,
)
_LOGIC_RE = re.compile(
    r"\b(logic|incorrect|wrong|should\s+be|off-by)\b",
    re.IGNORECASE,
)
_SECURITY_RE = re.compile(
    r"\b(security|vuln\w*|inject\w*|xss|auth)\b",
    re.IGNORECASE,
)
_TYPE_RE = re.compile(
    r"\b(type|cast|convert|serializ\w*|deserializ\w*)\b",
    re.IGNORECASE,
)


def classify_bug(case: TestCase) -> dict[str, str]:
    """Heuristically classify bug category, difficulty, and severity."""
    category = ""
    difficulty = ""
    severity = ""

    # Category from keywords in bug_description + PR title
    text = f"{case.bug_description} {case.fix_pr_title}"
    if _CONCURRENCY_RE.search(text):
        category = BugCategory.concurrency
    elif _SECURITY_RE.search(text):
        category = BugCategory.security
    elif _MEMORY_RE.search(text):
        category = BugCategory.memory
    elif _PARSER_RE.search(text):
        category = BugCategory.parser
    elif _CODEGEN_RE.search(text):
        category = BugCategory.codegen
    elif _COMPILER_PASS_RE.search(text):
        category = BugCategory.compiler_pass
    elif _INTERPRETER_RE.search(text):
        category = BugCategory.interpreter
    elif _FORMATTER_RE.search(text):
        category = BugCategory.formatter
    elif _CLI_RE.search(text):
        category = BugCategory.cli
    elif _RUNTIME_RE.search(text):
        category = BugCategory.runtime
    elif _LOGIC_RE.search(text):
        category = BugCategory.logic
    elif _TYPE_RE.search(text):
        category = BugCategory.type
    else:
        category = BugCategory.other

    # Difficulty from stats
    if case.stats:
        total = case.stats.lines_added + case.stats.lines_deleted
        if total < 10:
            difficulty = "easy"
        elif total < 50:
            difficulty = "medium"
        else:
            difficulty = "hard"

    # Severity from issue labels + keywords
    labels_text = " ".join(case.issue_labels).lower()
    text_lower = text.lower()
    if any(w in labels_text for w in ("critical", "p0", "blocker", "security")):
        severity = "critical"
    elif any(w in labels_text for w in ("high", "p1", "important")):
        severity = "high"
    elif any(w in text_lower for w in ("panic", "crash", "data loss", "corrupt")):
        severity = "high"
    elif any(w in labels_text for w in ("low", "p3", "minor")):
        severity = "low"
    else:
        severity = "medium"

    # Downgrade to low for trivial issues
    low_keywords = (
        "typo",
        "cosmetic",
        "nit",
        "style",
        "format",
        "warning",
        "doc fix",
        "spelling",
    )
    if any(w in text_lower for w in low_keywords):
        severity = "low"

    return {"category": category, "difficulty": difficulty, "severity": severity}


def populate_ground_truth(
    case: TestCase,
    repo_dir: Path,
    sibling_cases: list[TestCase] | None = None,
) -> TestCase:
    """Compute ground truth for a case: buggy lines, description, metadata."""
    if case.truth is None:
        case.truth = GroundTruth()

    # Always extract description and classify (don't need introducing commit)
    desc, source = extract_bug_description(case)
    if desc:
        case.bug_description = desc
        case.bug_description_source = source

    # Metadata
    meta = compute_metadata(case)
    if "bug_latency_days" in meta:
        case.bug_latency_days = meta["bug_latency_days"]
    if "same_author_fix" in meta:
        case.same_author_fix = meta["same_author_fix"]

    # Classification metadata
    classification = classify_bug(case)
    if not case.category:
        case.category = classification["category"]
    if not case.difficulty:
        case.difficulty = classification["difficulty"]
    if not case.severity:
        case.severity = classification["severity"]

    if case.status == "draft":
        case.status = "ground-truth"

    # Early return if no introducing commit (can't compute buggy lines)
    intro_sha = case.truth.introducing_commit
    if not intro_sha:
        return case

    # Get introducing diff
    try:
        introducing_diff = run_git("diff", f"{intro_sha}~1", intro_sha, cwd=repo_dir)
    except GitError:
        log.warning("Cannot get introducing diff for %s", case.id)
        return case

    # Get fix diffs from THIS case
    fix_diffs: list[str] = []
    fix_pr_map: dict[str, int] = {}  # diff -> fix_pr_number

    for pr_num in case.truth.fix_pr_numbers:
        diff = _get_fix_pr_diff(pr_num, case, repo_dir)
        if diff:
            fix_diffs.append(diff)
            fix_pr_map[diff] = pr_num

    # If no fix PR diffs, try the fix_commit directly
    if not fix_diffs and case.fix_commit:
        try:
            diff = run_git(
                "diff",
                f"{case.fix_commit}~1",
                case.fix_commit,
                cwd=repo_dir,
            )
            if diff:
                fix_diffs.append(diff)
                fix_pr_map[diff] = case.fix_pr_number or 0
        except GitError:
            pass

    # Get fix diffs from SIBLING cases (same introducing PR, different fix PRs)
    all_fix_pr_numbers = list(case.truth.fix_pr_numbers)
    if sibling_cases:
        for sib in sibling_cases:
            sib_pr = sib.fix_pr_number
            if not sib_pr or sib_pr in all_fix_pr_numbers:
                continue
            # Try to get diff from sibling's fix commit
            sib_commit = sib.fix_commit
            if sib_commit:
                try:
                    diff = run_git(
                        "diff",
                        f"{sib_commit}~1",
                        sib_commit,
                        cwd=repo_dir,
                    )
                    if diff:
                        fix_diffs.append(diff)
                        fix_pr_map[diff] = sib_pr
                        all_fix_pr_numbers.append(sib_pr)
                except GitError:
                    pass
            # Also try sibling's related_prs
            for pr_rel in sib.related_prs:
                if pr_rel.role in ("full_fix", "partial_fix") and pr_rel.commit:
                    if pr_rel.pr_number not in all_fix_pr_numbers:
                        try:
                            diff = run_git(
                                "diff",
                                f"{pr_rel.commit}~1",
                                pr_rel.commit,
                                cwd=repo_dir,
                            )
                            if diff:
                                fix_diffs.append(diff)
                                fix_pr_map[diff] = pr_rel.pr_number
                                all_fix_pr_numbers.append(pr_rel.pr_number)
                        except GitError:
                            pass

    # Compute buggy lines from ALL fix diffs, tagging each with its fix PR
    if fix_diffs:
        all_buggy: list[BuggyLine] = []
        for diff in fix_diffs:
            pr_num = fix_pr_map.get(diff, 0)
            lines = compute_buggy_lines(
                introducing_diff,
                [diff],
                fix_pr_number=pr_num,
            )
            all_buggy.extend(lines)

        # Dedup by (file, line) -- keep the first occurrence
        seen: set[tuple[str, int]] = set()
        deduped: list[BuggyLine] = []
        for bl in all_buggy:
            key = (bl.file, bl.line)
            if key not in seen:
                seen.add(key)
                deduped.append(bl)

        case.truth.buggy_lines = deduped
        case.truth.fix_pr_numbers = all_fix_pr_numbers

    return case


def _get_fix_pr_diff(pr_number: int, case: TestCase, repo_dir: Path) -> str:
    # Find the merge commit for this PR from related_prs
    merge_sha: str | None = None
    for pr in case.related_prs:
        if pr.pr_number == pr_number and pr.commit:
            merge_sha = pr.commit
            break

    if not merge_sha and case.fix_commit:
        merge_sha = case.fix_commit

    if not merge_sha:
        return ""

    try:
        return run_git("diff", f"{merge_sha}~1", merge_sha, cwd=repo_dir)
    except GitError:
        return ""


def build_ground_truth(cases_dir: Path, repo_dir: Path, concurrency: int) -> None:
    """Load cases, compute ground truth, checkpoint progress."""
    cases = load_cases(cases_dir)
    checkpoint_path = cases_dir / ".ground_truth_checkpoint.json"
    done = load_checkpoint(checkpoint_path)

    # Build sibling map: introducing_pr_number -> list of cases
    sibling_map: dict[int, list[TestCase]] = {}
    all_loaded = load_cases(cases_dir, include_excluded=True)
    for c in all_loaded:
        ipn = c.introducing_pr_number
        if ipn:
            sibling_map.setdefault(ipn, []).append(c)

    pending = [c for c in cases if c.id not in done]

    log.info(
        "Ground truth: %d pending, %d done, %d total",
        len(pending),
        len(done),
        len(cases),
    )

    def process(case: TestCase) -> TestCase:
        ipn = case.introducing_pr_number
        siblings = [s for s in sibling_map.get(ipn, []) if s.id != case.id] if ipn else []
        return populate_ground_truth(case, repo_dir, sibling_cases=siblings)

    total = len(pending)
    completed = 0

    if concurrency <= 1:
        for case in pending:
            updated = process(case)
            case_path = _find_case_path(cases_dir, case.id)
            if case_path:
                save_case(updated, case_path)
            with _checkpoint_lock:
                done.add(case.id)
                save_checkpoint(done, checkpoint_path)
            completed += 1
            log.info("Ground truth %d/%d: %s", completed, total, case.id)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(process, c): c for c in pending}
            for future in futures:
                case = futures[future]
                try:
                    updated = future.result()
                    case_path = _find_case_path(cases_dir, case.id)
                    if case_path:
                        save_case(updated, case_path)
                    with _checkpoint_lock:
                        done.add(case.id)
                        save_checkpoint(done, checkpoint_path)
                    completed += 1
                    log.info(
                        "Ground truth %d/%d: %s",
                        completed,
                        total,
                        case.id,
                    )
                except Exception as exc:
                    log.warning(
                        "Ground truth failed for %s: %s",
                        case.id,
                        exc,
                    )

    log.info(
        "Ground truth complete: %d/%d cases processed",
        completed,
        total,
    )


def _find_case_path(cases_dir: Path, case_id: str) -> Path | None:
    for p in cases_dir.rglob("*.yaml"):
        if p.stem == case_id:
            return p
    return None
