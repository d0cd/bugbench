"""Core data models for bug-finding evaluation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class BugCategory(StrEnum):
    concurrency = "concurrency"
    security = "security"
    memory = "memory"
    parser = "parser"
    codegen = "codegen"
    compiler_pass = "compiler-pass"
    interpreter = "interpreter"
    formatter = "formatter"
    cli = "cli"
    runtime = "runtime"
    logic = "logic"
    type = "type"
    other = "other"


class Difficulty(StrEnum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class Severity(StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class BlameConfidence(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    excluded = "excluded"


class PRSize(StrEnum):
    tiny = "tiny"
    small = "small"
    medium = "medium"
    large = "large"
    xl = "xl"


class CaseStatus(StrEnum):
    draft = "draft"
    ground_truth = "ground-truth"
    curated = "curated"
    validated = "validated"
    ready = "ready"


class CaseKind(StrEnum):
    bug = "bug"
    clean = "clean"


class BuggyLine(BaseModel):
    file: str
    line: int
    content: str = ""
    is_test_expectation: bool = False
    line_type: str = ""  # "code", "comment", "import", "config", "attribute", "blank"
    fix_pr_number: int = 0  # Which fix PR this finding belongs to


class ReviewThread(BaseModel):
    path: str = ""
    line: int = 0
    is_resolved: bool = False
    comments: list[str] = []


class PRRelation(BaseModel):
    pr_number: int
    role: str  # "introducing" | "partial_fix" | "full_fix" | "revert" | "regression"
    commit: str
    title: str = ""
    merge_date: str = ""
    author: str = ""


class GroundTruth(BaseModel):
    introducing_commit: str | None = None
    blame_confidence: str | None = None  # "A" | "B" | "C" | "D"
    buggy_lines: list[BuggyLine] = []
    fix_summary: str | None = None
    fix_pr_numbers: list[int] = []
    introduction_summary: str = ""


class Validation(BaseModel):
    claude_verdict: str = ""  # "confirmed" | "disputed" | "ambiguous"
    gemini_verdict: str = ""
    openai_verdict: str = ""
    agreement: bool = False
    test_validated: bool = False


class CaseStats(BaseModel):
    lines_added: int = 0
    lines_deleted: int = 0
    files_changed: int = 0


class TestCase(BaseModel):
    __test__ = False  # Prevent pytest collection warning

    # Identity
    id: str
    repo: str
    kind: CaseKind
    language: str = "rust"

    # Git coordinates
    base_commit: str
    fix_commit: str | None = None
    fix_pr_number: int | None = None

    # Introducing PR data (what the tool sees)
    introducing_pr_number: int | None = None
    introducing_pr_title: str = ""
    introducing_pr_body: str = ""
    introducing_pr_commit_messages: list[str] = []
    introducing_pr_commit_shas: list[str] = []
    introducing_pr_author: str = ""
    introducing_pr_merge_date: str = ""
    introducing_pr_review_comments: list[str] = []
    introducing_pr_review_threads: list[ReviewThread] = []
    introducing_pr_ci_status: str = ""

    # Fix PR data (for ground truth construction)
    fix_pr_title: str = ""
    fix_pr_body: str = ""
    fix_pr_commit_messages: list[str] = []
    fix_pr_commit_shas: list[str] = []
    fix_pr_merge_date: str = ""
    fix_pr_review_comments: list[str] = []
    fix_pr_review_threads: list[ReviewThread] = []
    fix_pr_discussion_comments: list[str] = []
    fix_pr_merge_method: str = ""
    fix_pr_ci_status: str = ""

    # Issue data
    linked_issues: list[int] = []
    issue_bodies: dict[int, str] = {}
    issue_labels: list[str] = []
    referenced_issues: list[int] = []

    # PR relationship graph
    related_prs: list[PRRelation] = []

    # Ground truth (None for clean cases)
    truth: GroundTruth | None = None

    # Validation
    validation: Validation | None = None

    # Classification metadata
    category: str = ""
    difficulty: str = ""
    severity: str = ""
    pr_size: str = ""
    stats: CaseStats | None = None
    bug_description: str = ""
    bug_description_source: str = ""
    bug_summary: str = ""
    bug_latency_days: int | None = None
    same_author_fix: bool = False

    # Pipeline metadata
    source: str = ""  # "pr-mining", "issue-mining", "manual"
    status: str = "draft"  # draft -> ground-truth -> curated -> validated -> ready
    fix_pr_files: list[str] = []

    # Curation
    excluded: bool = False
    excluded_reason: str = ""
    quality_flags: list[str] = []
