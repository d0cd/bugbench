"""Tests for the mine module."""

from __future__ import annotations

import json
import os
import subprocess
import subprocess as _subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bugeval.io import load_case
from bugeval.mine import (
    GhError,
    GitPRCandidate,
    _compute_pr_size,
    _detect_language,
    _is_non_bug_pr,
    _is_non_code_only,
    build_case_from_pr,
    build_pr_relations,
    detect_cross_references,
    detect_reverts,
    extract_closing_issues,
    extract_referenced_issues,
    fetch_bug_issues,
    fetch_closing_prs,
    has_fix_signal,
    has_local_fix_signal,
    mine_from_git,
    mine_from_issues,
    mine_repo,
    parse_git_prs,
    run_gh,
)
from bugeval.models import CaseKind, PRRelation


class TestHasFixSignal:
    def test_fix_in_title(self) -> None:
        assert has_fix_signal("Fix overflow bug", "", []) is True

    def test_bug_label(self) -> None:
        assert has_fix_signal("Add feature", "", ["bug"]) is True

    def test_hotfix_label(self) -> None:
        assert has_fix_signal("Update", "", ["hotfix"]) is True

    def test_no_signal(self) -> None:
        assert has_fix_signal("Add new feature", "Nice stuff", []) is False

    def test_fix_in_body(self) -> None:
        assert has_fix_signal("Update", "This fixes the issue", []) is True

    def test_revert_keyword(self) -> None:
        assert has_fix_signal("Revert bad change", "", []) is True

    def test_case_insensitive(self) -> None:
        assert has_fix_signal("BUG in parser", "", []) is True

    def test_regression_label(self) -> None:
        assert has_fix_signal("", "", ["regression"]) is True


class TestExtractClosingIssues:
    def test_fixes_hash(self) -> None:
        assert extract_closing_issues("Fixes #42") == [42]

    def test_closes_hash(self) -> None:
        assert extract_closing_issues("closes #100") == [100]

    def test_resolves_hash(self) -> None:
        assert extract_closing_issues("Resolves #7") == [7]

    def test_multiple(self) -> None:
        text = "Fixes #1 and closes #2"
        assert extract_closing_issues(text) == [1, 2]

    def test_no_match(self) -> None:
        assert extract_closing_issues("No issues here") == []

    def test_fixed_variant(self) -> None:
        assert extract_closing_issues("Fixed #99") == [99]


class TestExtractReferencedIssues:
    def test_see_hash(self) -> None:
        assert extract_referenced_issues("see #42") == [42]

    def test_related_to(self) -> None:
        assert extract_referenced_issues("related to #10") == [10]

    def test_followup_to(self) -> None:
        assert extract_referenced_issues("followup to #5") == [5]

    def test_no_match(self) -> None:
        assert extract_referenced_issues("Nothing here") == []


class TestIsNonCodeOnly:
    def test_all_docs(self) -> None:
        assert _is_non_code_only(["README.md", "CHANGELOG.md"]) is True

    def test_ci_files(self) -> None:
        assert _is_non_code_only([".github/workflows/ci.yml"]) is True

    def test_code_files(self) -> None:
        assert _is_non_code_only(["src/main.rs"]) is False

    def test_mixed(self) -> None:
        assert _is_non_code_only(["README.md", "src/lib.rs"]) is False

    def test_empty(self) -> None:
        assert _is_non_code_only([]) is True

    def test_toml_only(self) -> None:
        assert _is_non_code_only(["Cargo.toml"]) is True

    def test_lock_only(self) -> None:
        assert _is_non_code_only(["Cargo.lock"]) is True


class TestComputePrSize:
    def test_tiny(self) -> None:
        assert _compute_pr_size(3, 2) == "tiny"

    def test_small(self) -> None:
        assert _compute_pr_size(20, 10) == "small"

    def test_medium(self) -> None:
        assert _compute_pr_size(100, 50) == "medium"

    def test_large(self) -> None:
        assert _compute_pr_size(200, 100) == "large"

    def test_xl(self) -> None:
        assert _compute_pr_size(400, 200) == "xl"

    def test_boundary_tiny_small(self) -> None:
        assert _compute_pr_size(5, 4) == "tiny"
        assert _compute_pr_size(5, 5) == "small"

    def test_boundary_small_medium(self) -> None:
        assert _compute_pr_size(25, 24) == "small"
        assert _compute_pr_size(25, 25) == "medium"


class TestDetectLanguage:
    def test_rust(self) -> None:
        assert _detect_language(["src/main.rs", "src/lib.rs"]) == "rust"

    def test_python(self) -> None:
        assert _detect_language(["app.py", "tests/test.py"]) == "python"

    def test_mixed_majority_wins(self) -> None:
        files = ["a.rs", "b.rs", "c.py"]
        assert _detect_language(files) == "rust"

    def test_unknown_for_no_code(self) -> None:
        assert _detect_language(["README.md"]) == "unknown"

    def test_empty(self) -> None:
        assert _detect_language([]) == "unknown"

    def test_typescript(self) -> None:
        assert _detect_language(["app.ts", "component.tsx"]) == "typescript"

    def test_leo(self) -> None:
        assert _detect_language(["main.leo"]) == "leo"


class TestDetectCrossReferences:
    def test_finds_references(self) -> None:
        prs = [
            {"number": 1, "title": "Fix for #2", "body": ""},
            {"number": 2, "title": "Original", "body": ""},
        ]
        refs = detect_cross_references(prs)
        assert refs == {1: [2]}

    def test_ignores_self_reference(self) -> None:
        prs = [
            {"number": 1, "title": "See #1", "body": ""},
        ]
        assert detect_cross_references(prs) == {}

    def test_ignores_external_numbers(self) -> None:
        prs = [
            {"number": 1, "title": "See #999", "body": ""},
        ]
        assert detect_cross_references(prs) == {}

    def test_body_references(self) -> None:
        prs = [
            {"number": 10, "title": "Fix", "body": "Related to #20"},
            {"number": 20, "title": "Original", "body": ""},
        ]
        refs = detect_cross_references(prs)
        assert refs == {10: [20]}


class TestDetectReverts:
    def test_finds_revert(self) -> None:
        prs = [
            {"number": 5, "title": "Revert #3"},
            {"number": 3, "title": "Bad change"},
        ]
        assert detect_reverts(prs) == {5: 3}

    def test_no_reverts(self) -> None:
        prs = [{"number": 1, "title": "Normal PR"}]
        assert detect_reverts(prs) == {}

    def test_case_insensitive(self) -> None:
        prs = [{"number": 10, "title": "REVERT PR #7"}]
        assert detect_reverts(prs) == {10: 7}


class TestBuildPrRelations:
    def test_builds_related(self) -> None:
        prs_by_num: dict[int, dict[str, Any]] = {
            1: {
                "number": 1,
                "title": "Fix",
                "mergeCommit": {"oid": "aaa"},
                "mergedAt": "2024-01-01",
                "author": {"login": "alice"},
            },
            2: {
                "number": 2,
                "title": "Original",
                "mergeCommit": {"oid": "bbb"},
                "mergedAt": "2024-01-02",
                "author": {"login": "bob"},
            },
        }
        cross = {1: [2]}
        reverts: dict[int, int] = {}
        rels = build_pr_relations(1, prs_by_num, cross, reverts)
        assert len(rels) == 1
        assert rels[0].pr_number == 2
        assert rels[0].role == "related"
        assert rels[0].commit == "bbb"

    def test_revert_role(self) -> None:
        prs_by_num: dict[int, dict[str, Any]] = {
            5: {
                "number": 5,
                "title": "Revert #3",
                "mergeCommit": {"oid": "eee"},
                "mergedAt": "",
                "author": {"login": "x"},
            },
            3: {
                "number": 3,
                "title": "Bad",
                "mergeCommit": {"oid": "ccc"},
                "mergedAt": "",
                "author": {"login": "y"},
            },
        }
        cross = {5: [3]}
        reverts = {5: 3}
        rels = build_pr_relations(5, prs_by_num, cross, reverts)
        assert rels[0].role == "revert"

    def test_missing_pr_returns_empty(self) -> None:
        rels = build_pr_relations(999, {}, {}, {})
        assert rels == []


class TestFixPrInRelatedPrs:
    def test_fix_pr_in_related_prs(self) -> None:
        """Verify fix PR is added to related_prs with role='full_fix'."""
        pr: dict[str, Any] = {
            "number": 42,
            "title": "Fix overflow",
            "body": "",
            "mergeCommit": {"oid": "abc123"},
            "additions": 15,
            "deletions": 3,
            "changedFiles": 1,
            "files": [{"path": "src/main.rs"}],
            "labels": [],
            "mergedAt": "2024-07-10",
            "author": {"login": "alice"},
        }
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="r-001",
        )
        fix_rels = [r for r in case.related_prs if r.role == "full_fix"]
        assert len(fix_rels) == 1
        assert fix_rels[0].pr_number == 42
        assert fix_rels[0].commit == "abc123"
        assert fix_rels[0].author == "alice"

    def test_fix_pr_is_first_relation(self) -> None:
        """Fix PR relation should be first in the list."""
        pr: dict[str, Any] = {
            "number": 10,
            "title": "Fix",
            "body": "",
            "mergeCommit": {"oid": "sha1"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "files": [{"path": "x.rs"}],
            "labels": [],
            "mergedAt": "",
            "author": {"login": "dev"},
        }
        existing_rel = PRRelation(
            pr_number=5,
            role="related",
            commit="other",
        )
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="r-001",
            relations=[existing_rel],
        )
        assert case.related_prs[0].role == "full_fix"
        assert case.related_prs[0].pr_number == 10
        assert case.related_prs[1].pr_number == 5


class TestBuildCaseFromPr:
    def test_basic_construction(self) -> None:
        pr: dict[str, Any] = {
            "number": 42,
            "title": "Fix overflow",
            "body": "Fixes #10",
            "mergeCommit": {"oid": "abc123"},
            "additions": 15,
            "deletions": 3,
            "changedFiles": 2,
            "files": [
                {"path": "src/main.rs"},
                {"path": "src/lib.rs"},
            ],
            "labels": [{"name": "bug"}],
            "mergedAt": "2024-07-10",
            "author": {"login": "alice"},
        }
        case = build_case_from_pr(
            repo="ProvableHQ/snarkVM",
            pr=pr,
            case_id="snarkVM-001",
        )
        assert case.id == "snarkVM-001"
        assert case.repo == "ProvableHQ/snarkVM"
        assert case.kind == CaseKind.bug
        assert case.language == "rust"
        assert case.fix_commit == "abc123"
        assert case.fix_pr_number == 42
        assert case.fix_pr_title == "Fix overflow"
        assert case.linked_issues == [10]
        assert case.issue_labels == ["bug"]
        assert case.pr_size == "small"
        assert case.stats is not None
        assert case.stats.lines_added == 15

    def test_with_graphql_data(self) -> None:
        pr: dict[str, Any] = {
            "number": 1,
            "title": "Fix",
            "body": "",
            "mergeCommit": {"oid": "sha1"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "files": [{"path": "a.py"}],
            "labels": [],
            "mergedAt": "2024-01-01",
            "author": {"login": "dev"},
        }
        gql: dict[str, Any] = {
            "commits": {
                "nodes": [{"commit": {"oid": "c1", "message": "fix: thing"}}],
            },
            "reviews": {
                "nodes": [{"body": "LGTM", "state": "APPROVED"}],
            },
            "reviewThreads": {
                "nodes": [
                    {
                        "comments": {
                            "nodes": [{"body": "nit: spacing"}],
                        },
                    },
                ],
            },
            "comments": {
                "nodes": [{"body": "Thanks!"}],
            },
            "closingIssuesReferences": {
                "nodes": [
                    {
                        "number": 99,
                        "body": "Bug report",
                        "labels": {"nodes": [{"name": "critical"}]},
                    },
                ],
            },
        }
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="repo-001",
            graphql_data=gql,
        )
        assert case.fix_pr_commit_messages == ["fix: thing"]
        assert any("LGTM" in c for c in case.fix_pr_review_comments)
        assert any("APPROVED" in c for c in case.fix_pr_review_comments)
        assert "nit: spacing" in case.fix_pr_review_comments
        assert case.fix_pr_discussion_comments == ["Thanks!"]
        assert 99 in case.linked_issues
        assert case.issue_bodies[99] == "Bug report"
        assert "critical" in case.issue_labels

    def test_with_issue_data(self) -> None:
        pr: dict[str, Any] = {
            "number": 1,
            "title": "Fix",
            "body": "fixes #5",
            "mergeCommit": {"oid": "sha"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "files": [{"path": "x.rs"}],
            "labels": [],
            "mergedAt": "",
            "author": {"login": "dev"},
        }
        issue_data = {
            5: {"body": "Something broke", "labels": [{"name": "p0"}]},
        }
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="r-001",
            issue_data=issue_data,
        )
        assert case.issue_bodies[5] == "Something broke"
        assert "p0" in case.issue_labels

    def test_source_set_to_pr_mining(self) -> None:
        pr: dict[str, Any] = {
            "number": 42,
            "title": "Fix bug",
            "body": "",
            "mergeCommit": {"oid": "abc"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 2,
            "labels": [],
            "mergedAt": "2024-01-01T00:00:00Z",
            "author": {"login": "dev"},
            "files": [],
        }
        case = build_case_from_pr("org/repo", pr, "test-001")
        assert case.source == "pr-mining"

    def test_fix_pr_files_populated(self) -> None:
        pr: dict[str, Any] = {
            "number": 42,
            "title": "Fix bug",
            "body": "",
            "mergeCommit": {"oid": "abc"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 2,
            "labels": [],
            "mergedAt": "2024-01-01T00:00:00Z",
            "author": {"login": "dev"},
            "files": [{"path": "src/lib.rs"}, {"path": "tests/test.rs"}],
        }
        case = build_case_from_pr("org/repo", pr, "test-001")
        assert case.fix_pr_files == ["src/lib.rs", "tests/test.rs"]

    def test_with_relations(self) -> None:
        pr: dict[str, Any] = {
            "number": 1,
            "title": "Fix",
            "body": "",
            "mergeCommit": {"oid": "sha"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "files": [{"path": "x.rs"}],
            "labels": [],
            "mergedAt": "",
            "author": {"login": "dev"},
        }
        rels = [
            PRRelation(pr_number=2, role="related", commit="abc"),
        ]
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="r-001",
            relations=rels,
        )
        # Fix PR (full_fix) is auto-prepended + the explicit relation
        assert len(case.related_prs) == 2
        assert case.related_prs[0].role == "full_fix"
        assert case.related_prs[0].pr_number == 1
        assert case.related_prs[1].pr_number == 2


class TestBuildCaseRoundTrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        from bugeval.io import load_case, save_case

        pr: dict[str, Any] = {
            "number": 7,
            "title": "Fix bug",
            "body": "Fixes #3",
            "mergeCommit": {"oid": "deadbeef"},
            "additions": 20,
            "deletions": 5,
            "changedFiles": 2,
            "files": [
                {"path": "src/main.rs"},
                {"path": "src/util.rs"},
            ],
            "labels": [{"name": "bug"}],
            "mergedAt": "2024-08-01",
            "author": {"login": "dev"},
        }
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="repo-001",
        )
        path = tmp_path / "repo-001.yaml"
        save_case(case, path)
        loaded = load_case(path)
        assert loaded.id == "repo-001"
        assert loaded.fix_commit == "deadbeef"
        assert loaded.linked_issues == [3]
        assert loaded.pr_size == "small"
        assert loaded.stats is not None
        assert loaded.stats.lines_added == 20


class TestRunGh:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="ok",
                stderr="",
            ),
        )
        assert run_gh("pr", "list") == "ok"

    def test_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="err",
            ),
        )
        with pytest.raises(GhError, match="err"):
            run_gh("pr", "list")

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_timeout(*a: object, **kw: object) -> None:
            raise subprocess.TimeoutExpired(cmd="gh", timeout=60)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        with pytest.raises(GhError, match="timed out"):
            run_gh("pr", "list")


class TestMineRepo:
    def test_end_to_end(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test mine_repo with mocked gh calls."""
        pr_list_data = [
            {
                "number": 42,
                "title": "Fix overflow bug",
                "body": "Fixes #10",
                "mergeCommit": {"oid": "abc123"},
                "additions": 20,
                "deletions": 5,
                "changedFiles": 2,
                "files": [
                    {"path": "src/main.rs"},
                    {"path": "src/lib.rs"},
                ],
                "labels": [{"name": "bug"}],
                "mergedAt": "2024-07-10",
                "author": {"login": "alice"},
                "commits": [],
                "reviewDecision": "APPROVED",
                "statusCheckRollup": [],
                "baseRefName": "main",
                "headRefName": "fix-overflow",
            },
        ]
        graphql_response = {
            "data": {
                "repository": {
                    "pr_42": {
                        "number": 42,
                        "title": "Fix overflow bug",
                        "body": "Fixes #10",
                        "mergedAt": "2024-07-10",
                        "mergeCommit": {"oid": "abc123"},
                        "author": {"login": "alice"},
                        "commits": {
                            "nodes": [
                                {
                                    "commit": {
                                        "oid": "c1",
                                        "message": "fix: overflow",
                                    },
                                },
                            ],
                        },
                        "reviews": {"nodes": []},
                        "reviewThreads": {"nodes": []},
                        "comments": {"nodes": []},
                        "closingIssuesReferences": {"nodes": []},
                    },
                },
            },
        }
        issue_data = {
            "number": 10,
            "title": "Overflow",
            "body": "Counter overflows",
            "labels": [{"name": "bug"}],
        }

        call_count = {"n": 0}

        def mock_run(
            cmd: list[str],
            **kw: Any,
        ) -> subprocess.CompletedProcess[str]:
            call_count["n"] += 1
            if "pr" in cmd and "list" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=json.dumps(pr_list_data),
                    stderr="",
                )
            if "graphql" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=json.dumps(graphql_response),
                    stderr="",
                )
            if "issue" in cmd and "view" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=json.dumps(issue_data),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="unknown",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)

        cases = mine_repo(
            repo="ProvableHQ/snarkVM",
            limit=200,
            since="2023-01-01",
            output_dir=tmp_path,
        )
        assert len(cases) == 1
        assert cases[0].id == "snarkVM-001"
        assert cases[0].fix_pr_number == 42

        # Verify file was written
        case_file = tmp_path / "snarkVM" / "snarkVM-001.yaml"
        assert case_file.exists()
        loaded = load_case(case_file)
        assert loaded.id == "snarkVM-001"

        # Verify checkpoint
        ckpt = tmp_path / "snarkVM" / ".mine_checkpoint.json"
        assert ckpt.exists()

    def test_checkpoint_skips_done(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Already-checkpointed PRs are skipped."""
        from bugeval.io import save_checkpoint

        repo_dir = tmp_path / "snarkVM"
        repo_dir.mkdir(parents=True)
        save_checkpoint({"42"}, repo_dir / ".mine_checkpoint.json")

        pr_list_data = [
            {
                "number": 42,
                "title": "Fix overflow bug",
                "body": "",
                "mergeCommit": {"oid": "abc"},
                "additions": 20,
                "deletions": 5,
                "changedFiles": 1,
                "files": [{"path": "src/main.rs"}],
                "labels": [{"name": "bug"}],
                "mergedAt": "2024-01-01",
                "author": {"login": "x"},
                "commits": [],
                "reviewDecision": "",
                "statusCheckRollup": [],
                "baseRefName": "main",
                "headRefName": "fix",
            },
        ]

        def mock_run(
            cmd: list[str],
            **kw: Any,
        ) -> subprocess.CompletedProcess[str]:
            if "pr" in cmd and "list" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=json.dumps(pr_list_data),
                    stderr="",
                )
            if "graphql" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=json.dumps({"data": {"repository": {}}}),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="[]",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)

        cases = mine_repo(
            repo="ProvableHQ/snarkVM",
            limit=200,
            since="2023-01-01",
            output_dir=tmp_path,
        )
        assert len(cases) == 0


class TestBuildCaseLanguageFallback:
    def test_repo_language_used_when_detect_returns_unknown(self) -> None:
        """When files list is empty, repo_language param is used."""
        pr: dict[str, Any] = {
            "number": 99,
            "title": "Fix something",
            "body": "",
            "mergeCommit": {"oid": "abc"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "files": [],  # empty => _detect_language returns "unknown"
            "labels": [],
            "mergedAt": "",
            "author": {"login": "dev"},
        }
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="r-001",
            repo_language="rust",
        )
        assert case.language == "rust"

    def test_detect_language_wins_over_repo_language(self) -> None:
        """When files give a real language, it wins over repo_language."""
        pr: dict[str, Any] = {
            "number": 99,
            "title": "Fix something",
            "body": "",
            "mergeCommit": {"oid": "abc"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "files": [{"path": "src/main.rs"}],
            "labels": [],
            "mergedAt": "",
            "author": {"login": "dev"},
        }
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="r-001",
            repo_language="python",
        )
        assert case.language == "rust"

    def test_unknown_when_no_fallback(self) -> None:
        """When no files and no repo_language, language is 'unknown'."""
        pr: dict[str, Any] = {
            "number": 99,
            "title": "Fix something",
            "body": "",
            "mergeCommit": {"oid": "abc"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "files": [],
            "labels": [],
            "mergedAt": "",
            "author": {"login": "dev"},
        }
        case = build_case_from_pr(
            repo="org/repo",
            pr=pr,
            case_id="r-001",
        )
        assert case.language == "unknown"


class TestIsNonBugPr:
    """Tests for _is_non_bug_pr filtering."""

    # --- Should be filtered (return True) ---

    def test_clippy_errors(self) -> None:
        assert _is_non_bug_pr("Fix Clippy Errors", "") is True

    def test_clippy_warnings(self) -> None:
        assert _is_non_bug_pr("fix clippy warnings", "") is True

    def test_lint_chore(self) -> None:
        assert _is_non_bug_pr("chore: fix lint errors", "") is True

    def test_rustfmt(self) -> None:
        assert _is_non_bug_pr("rustfmt: reformat crate", "") is True

    def test_typo_fix(self) -> None:
        assert _is_non_bug_pr("fix: typos in panic function and comments", "") is True

    def test_typo_chore(self) -> None:
        assert (
            _is_non_bug_pr("chore: remove redundant words and fix some typos in comment", "")
            is True
        )

    def test_spelling_mistakes(self) -> None:
        assert _is_non_bug_pr("Fix spelling mistakes", "") is True

    def test_doc_fix(self) -> None:
        assert _is_non_bug_pr("Fix some doc.", "") is True

    def test_doc_improve(self) -> None:
        assert _is_non_bug_pr("Fix and improve some doc.", "") is True

    def test_doc_help_messages(self) -> None:
        assert _is_non_bug_pr("Fix help messages for command line options", "") is True

    def test_release(self) -> None:
        assert _is_non_bug_pr("[Release] Leo v3.3.1", "") is True

    def test_patch_release(self) -> None:
        assert _is_non_bug_pr("Leo v3.3.1 patch release", "") is True

    def test_version_bump(self) -> None:
        assert _is_non_bug_pr("version bump to 2.0.0", "") is True

    def test_perf_no_issue(self) -> None:
        assert _is_non_bug_pr("avoid extra allocation when building record members", "") is True

    def test_perf_prefix_no_issue(self) -> None:
        assert _is_non_bug_pr("perf: reduce memory allocations", "") is True

    def test_deprecation_removal(self) -> None:
        assert _is_non_bug_pr("[Fix] Remove deprecation warning for `leo build`", "") is True

    # --- Perf with linked issue should NOT be filtered ---

    def test_perf_with_issue_ref(self) -> None:
        assert (
            _is_non_bug_pr(
                "avoid extra allocation when building record members",
                "Fixes #123",
            )
            is False
        )

    # --- Real bugs must NOT be filtered (return False) ---

    def test_off_by_one(self) -> None:
        assert _is_non_bug_pr("Fix off-by-one in loop counter", "") is False

    def test_ssa_bug(self) -> None:
        assert _is_non_bug_pr("Fix SSA incorrectly replacing global vars", "") is False

    def test_panic_on_unknown_var(self) -> None:
        assert _is_non_bug_pr("[Fix] Panic on unknown variable", "") is False

    def test_double_negation(self) -> None:
        assert _is_non_bug_pr("Correctly parse double negation", "") is False

    def test_array_access(self) -> None:
        assert _is_non_bug_pr("Fix ArrayAccess in the interpreter", "") is False

    def test_generic_fix(self) -> None:
        assert _is_non_bug_pr("Fix race condition in worker pool", "") is False

    def test_empty_title(self) -> None:
        assert _is_non_bug_pr("", "") is False


class TestRunGhRetry:
    """Tests for run_gh retry-with-backoff logic."""

    def test_run_gh_retries_on_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail twice with HTTP 500, succeed on third attempt."""
        call_count = 0

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="HTTP 500")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("bugeval.mine.time.sleep", lambda _: None)
        result = run_gh("api", "/repos", retries=3, backoff=0.0)
        assert result == "ok"
        assert call_count == 3

    def test_run_gh_no_retry_on_permanent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Permanent error (not found) should not be retried."""
        call_count = 0

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(GhError):
            run_gh("api", "/repos", retries=3, backoff=0.0)
        assert call_count == 1

    def test_run_gh_succeeds_first_try(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No retries when first attempt succeeds."""
        call_count = 0

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="done", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = run_gh("api", "/repos", retries=3, backoff=0.0)
        assert result == "done"
        assert call_count == 1


class TestBuildCaseFromPrIssueFiltering:
    def test_only_linked_issue_bodies_included(self) -> None:
        """issue_data should only include bodies for linked issues, not all."""
        pr: dict[str, Any] = {
            "number": 100,
            "title": "Fix bug",
            "body": "Fixes #5",
            "mergeCommit": {"oid": "abc123"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 1,
            "labels": [],
            "mergedAt": "2024-01-01T00:00:00Z",
            "author": {"login": "dev"},
            "files": [],
        }
        # issue_data has 3 issues, but only #5 is linked
        issue_data: dict[int, dict[str, Any]] = {
            5: {"body": "Bug report for issue 5", "labels": [{"name": "bug"}]},
            10: {"body": "Unrelated issue 10", "labels": [{"name": "feature"}]},
            20: {"body": "Another unrelated issue", "labels": []},
        }
        case = build_case_from_pr("owner/repo", pr, "test-001", issue_data=issue_data)
        # Only issue 5 should be in issue_bodies
        assert 5 in case.issue_bodies
        assert 10 not in case.issue_bodies
        assert 20 not in case.issue_bodies
        assert len(case.issue_bodies) == 1


class TestFetchBugIssues:
    @patch("bugeval.mine.run_gh")
    def test_fetches_closed_bug_issues(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = json.dumps(
            [
                {
                    "number": 100,
                    "title": "[Bug] Crash",
                    "body": "It crashes",
                    "labels": [{"name": "bug"}],
                },
                {
                    "number": 200,
                    "title": "[Bug] Wrong output",
                    "body": "Bad",
                    "labels": [{"name": "bug"}],
                },
            ]
        )
        result = fetch_bug_issues("org/repo", 100, "2024-01-01")
        assert len(result) == 2
        assert result[0]["number"] == 100
        # Verify correct gh args
        call_args = mock_gh.call_args[0]
        assert "issue" in call_args
        assert "--label" in call_args
        assert "bug" in call_args
        assert "--state" in call_args
        assert "closed" in call_args

    @patch("bugeval.mine.run_gh")
    def test_empty_result(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = "[]"
        result = fetch_bug_issues("org/repo", 100, "2024-01-01")
        assert result == []


class TestFetchClosingPrs:
    @patch("bugeval.mine.run_gh")
    def test_finds_closing_prs(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = json.dumps(
            {
                "data": {
                    "repository": {
                        "issue_100": {
                            "number": 100,
                            "timelineItems": {
                                "nodes": [
                                    {"closer": {"number": 42, "merged": True}},
                                ],
                            },
                        },
                    },
                },
            }
        )
        result = fetch_closing_prs("org", "repo", [100])
        assert result == {100: [42]}

    @patch("bugeval.mine.run_gh")
    def test_skips_unmerged_prs(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = json.dumps(
            {
                "data": {
                    "repository": {
                        "issue_100": {
                            "number": 100,
                            "timelineItems": {
                                "nodes": [
                                    {"closer": {"number": 42, "merged": False}},
                                ],
                            },
                        },
                    },
                },
            }
        )
        result = fetch_closing_prs("org", "repo", [100])
        assert result == {}

    def test_empty_input(self) -> None:
        result = fetch_closing_prs("org", "repo", [])
        assert result == {}


class TestMineFromIssues:
    @patch("bugeval.mine.run_gh")
    def test_creates_cases_from_bug_issues(
        self,
        mock_gh: MagicMock,
        tmp_path: Path,
    ) -> None:
        """End-to-end: bug issue -> closing PR -> new case."""

        def gh_side_effect(*args: str, **kwargs: Any) -> str:
            joined = " ".join(args)
            if "issue" in joined and "list" in joined:
                return json.dumps(
                    [
                        {
                            "number": 100,
                            "title": "[Bug] Crash",
                            "body": "It crashes",
                            "closedAt": "2024-06-01",
                            "labels": [{"name": "bug"}],
                        },
                    ]
                )
            if "graphql" in joined:
                return json.dumps(
                    {
                        "data": {
                            "repository": {
                                "issue_100": {
                                    "number": 100,
                                    "timelineItems": {
                                        "nodes": [
                                            {
                                                "closer": {
                                                    "number": 42,
                                                    "merged": True,
                                                },
                                            },
                                        ],
                                    },
                                },
                            },
                        },
                    }
                )
            if "pr" in joined and "view" in joined:
                return json.dumps(
                    {
                        "number": 42,
                        "title": "Handle edge case",
                        "body": "Fixes #100",
                        "labels": [{"name": "bug"}],
                        "mergeCommit": {"oid": "abc123"},
                        "additions": 20,
                        "deletions": 5,
                        "changedFiles": 2,
                        "mergedAt": "2024-06-01T00:00:00Z",
                        "author": {"login": "dev"},
                        "reviewDecision": "APPROVED",
                    }
                )
            return "[]"

        mock_gh.side_effect = gh_side_effect

        cases = mine_from_issues(
            "ProvableHQ/leo",
            100,
            "2024-01-01",
            tmp_path,
        )
        assert len(cases) == 1
        assert cases[0].source == "issue-mining"
        assert cases[0].fix_pr_number == 42
        # Case file written
        assert (tmp_path / "leo" / "leo-001.yaml").exists()

    @patch("bugeval.mine.run_gh")
    def test_dedup_skips_existing_pr(
        self,
        mock_gh: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If the closing PR is already a case, skip it."""
        # Create an existing case with PR #42
        repo_dir = tmp_path / "leo"
        repo_dir.mkdir(parents=True)
        from bugeval.io import save_case
        from bugeval.models import TestCase as TC

        existing = TC(
            id="leo-001",
            repo="ProvableHQ/leo",
            kind=CaseKind.bug,
            base_commit="abc",
            fix_pr_number=42,
        )
        save_case(existing, repo_dir / "leo-001.yaml")

        def gh_side_effect(*args: str, **kwargs: Any) -> str:
            joined = " ".join(args)
            if "issue" in joined and "list" in joined:
                return json.dumps(
                    [
                        {
                            "number": 100,
                            "title": "[Bug] Crash",
                            "body": "",
                            "closedAt": "2024-06-01",
                            "labels": [{"name": "bug"}],
                        },
                    ]
                )
            if "graphql" in joined:
                return json.dumps(
                    {
                        "data": {
                            "repository": {
                                "issue_100": {
                                    "number": 100,
                                    "timelineItems": {
                                        "nodes": [
                                            {
                                                "closer": {
                                                    "number": 42,
                                                    "merged": True,
                                                },
                                            },
                                        ],
                                    },
                                },
                            },
                        },
                    }
                )
            return "[]"

        mock_gh.side_effect = gh_side_effect
        cases = mine_from_issues(
            "ProvableHQ/leo",
            100,
            "2024-01-01",
            tmp_path,
        )
        assert len(cases) == 0  # Deduped


class TestMineCli:
    def test_from_issues_flag_exists(self) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mine", "--help"])
        assert "--from-issues" in result.output


# --- Git PR parsing tests ---


def _get_git_env() -> dict[str, str]:
    """Get minimal env for git subprocess (need PATH at minimum)."""
    return {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")}


def _init_repo(path: Path) -> Path:
    """Create a minimal git repo for testing."""
    path.mkdir(exist_ok=True)
    _subprocess.run(
        ["git", "init"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    _subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        capture_output=True,
    )
    _subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
    )
    _subprocess.run(
        ["git", "checkout", "-b", "master"],
        cwd=path,
        capture_output=True,
    )
    _subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    return path


class TestParseGitPrs:
    def test_parses_merge_commit(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        _subprocess.run(
            ["git", "checkout", "-b", "fix/thing"],
            cwd=repo,
            capture_output=True,
        )
        (repo / "file.rs").write_text("let x = 1;\n")
        _subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        _subprocess.run(
            ["git", "commit", "-m", "fix the thing"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "checkout", "master"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            [
                "git",
                "merge",
                "--no-ff",
                "fix/thing",
                "-m",
                "Merge pull request #42 from org/fix/thing",
                "-m",
                "[Fix] The thing",
            ],
            cwd=repo,
            capture_output=True,
        )

        candidates = parse_git_prs(repo, since="2020-01-01")
        assert len(candidates) == 1
        c = candidates[0]
        assert c.pr_number == 42
        assert c.branch_name == "fix/thing"
        assert c.title == "[Fix] The thing"
        assert "fix the thing" in c.commit_messages
        assert c.lines_added > 0

    def test_parses_squash_merge(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        (repo / "file.rs").write_text("let x = 1;\n")
        _subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        _subprocess.run(
            ["git", "commit", "-m", "[Fix] Correct the parser (#99)"],
            cwd=repo,
            capture_output=True,
        )

        candidates = parse_git_prs(repo, since="2020-01-01")
        assert len(candidates) == 1
        c = candidates[0]
        assert c.pr_number == 99
        assert c.title == "[Fix] Correct the parser"

    def test_skips_non_pr_commits(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        _subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "just a commit"],
            cwd=repo,
            capture_output=True,
        )

        candidates = parse_git_prs(repo, since="2020-01-01")
        assert len(candidates) == 0

    def test_dedup_merge_over_squash(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        # Squash-style commit
        _subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "Fix thing (#42)"],
            cwd=repo,
            capture_output=True,
        )
        # Merge-style commit referencing same PR
        _subprocess.run(
            [
                "git",
                "commit",
                "--allow-empty",
                "-m",
                "Merge pull request #42 from org/fix\n\nFix thing",
            ],
            cwd=repo,
            capture_output=True,
        )

        candidates = parse_git_prs(repo, since="2020-01-01")
        assert len(candidates) == 1
        assert candidates[0].pr_number == 42

    def test_empty_repo(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        candidates = parse_git_prs(repo, since="2020-01-01")
        assert candidates == []

    def test_since_filters_old_commits(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        (repo / "f.rs").write_text("x\n")
        _subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        env = {
            "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
            "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
        }
        _subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Fix old thing (#10)",
                "--date",
                "2020-01-01T00:00:00",
            ],
            cwd=repo,
            capture_output=True,
            env={**_get_git_env(), **env},
        )
        candidates = parse_git_prs(repo, since="2024-01-01")
        assert len(candidates) == 0


class TestHasLocalFixSignal:
    def _candidate(self, **kwargs: Any) -> GitPRCandidate:
        defaults: dict[str, Any] = dict(
            pr_number=1,
            sha="abc",
            title="",
            branch_name="",
            commit_messages="",
            author="dev",
            date="2024-01-01",
        )
        defaults.update(kwargs)
        return GitPRCandidate(**defaults)

    def test_fix_in_title(self) -> None:
        c = self._candidate(title="Fix off-by-one in loop")
        assert has_local_fix_signal(c) is True

    def test_fix_in_branch_name(self) -> None:
        c = self._candidate(
            title="Adjust SSA behavior",
            branch_name="fix/ssa-replacement",
        )
        assert has_local_fix_signal(c) is True

    def test_bug_in_branch_name(self) -> None:
        c = self._candidate(
            title="Shadowing of external inputs",
            branch_name="bug/stub-input-shadowing",
        )
        assert has_local_fix_signal(c) is True

    def test_fix_in_commit_messages(self) -> None:
        c = self._candidate(
            title="Adjust how SSA works",
            commit_messages="fix: SSA incorrectly replacing global vars",
        )
        assert has_local_fix_signal(c) is True

    def test_no_signal(self) -> None:
        c = self._candidate(
            title="Add new feature",
            branch_name="feat/new-thing",
            commit_messages="implement new thing",
        )
        assert has_local_fix_signal(c) is False

    def test_correct_in_title(self) -> None:
        c = self._candidate(title="Correctly parse double negation")
        assert has_local_fix_signal(c) is True

    def test_revert_in_title(self) -> None:
        c = self._candidate(title="Revert bad change")
        assert has_local_fix_signal(c) is True

    def test_hotfix_in_branch(self) -> None:
        c = self._candidate(
            title="Update config",
            branch_name="hotfix/urgent-config",
        )
        assert has_local_fix_signal(c) is True

    def test_patch_in_branch(self) -> None:
        c = self._candidate(
            title="Update version",
            branch_name="patch/version-bump",
        )
        assert has_local_fix_signal(c) is True


class TestMineFromGit:
    def test_creates_cases_from_local_git(
        self,
        tmp_path: Path,
    ) -> None:
        """End-to-end: local git -> fix candidates -> API enrich -> cases."""
        # Create repo with fix merge commit
        repo = tmp_path / "repo"
        repo.mkdir()
        _subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        _subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "checkout", "-b", "master"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "checkout", "-b", "fix/parser"],
            cwd=repo,
            capture_output=True,
        )
        (repo / "src.rs").write_text("fn main() {}\n" * 5)
        _subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        _subprocess.run(
            ["git", "commit", "-m", "fix parser bug"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "checkout", "master"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            [
                "git",
                "merge",
                "--no-ff",
                "fix/parser",
                "-m",
                "Merge pull request #42 from org/fix/parser\n\n[Fix] Parser",
            ],
            cwd=repo,
            capture_output=True,
        )

        output_dir = tmp_path / "cases"

        with patch("bugeval.mine.run_gh") as mock_gh:
            mock_gh.return_value = json.dumps(
                {
                    "data": {
                        "repository": {
                            "pr_42": {
                                "number": 42,
                                "title": "[Fix] Parser",
                                "body": "Fixes the parser",
                                "mergedAt": "2024-06-01T00:00:00Z",
                                "mergeCommit": {"oid": "abc123"},
                                "mergeMethod": "MERGE",
                                "statusCheckRollup": None,
                                "author": {"login": "dev"},
                                "commits": {"nodes": []},
                                "reviews": {"nodes": []},
                                "reviewThreads": {"nodes": []},
                                "comments": {"nodes": []},
                                "closingIssuesReferences": {"nodes": []},
                            },
                        },
                    },
                }
            )
            cases = mine_from_git(
                repo="ProvableHQ/leo",
                repo_dir=repo,
                since="2020-01-01",
                output_dir=output_dir,
            )

        assert len(cases) == 1
        assert cases[0].source == "git-mining"
        assert cases[0].fix_pr_number == 42

    def test_dedup_skips_existing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        _subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "checkout", "-b", "master"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            [
                "git",
                "commit",
                "--allow-empty",
                "-m",
                "Merge pull request #42 from org/fix/x\n\nFix thing",
            ],
            cwd=repo,
            capture_output=True,
        )

        output_dir = tmp_path / "cases"
        case_dir = output_dir / "leo"
        case_dir.mkdir(parents=True)
        from bugeval.io import save_case
        from bugeval.models import CaseKind
        from bugeval.models import TestCase as TC

        existing = TC(
            id="leo-001",
            repo="ProvableHQ/leo",
            kind=CaseKind.bug,
            base_commit="abc",
            fix_pr_number=42,
        )
        save_case(existing, case_dir / "leo-001.yaml")

        cases = mine_from_git(
            repo="ProvableHQ/leo",
            repo_dir=repo,
            since="2020-01-01",
            output_dir=output_dir,
        )
        assert len(cases) == 0

    def test_non_bug_pr_filtered(self, tmp_path: Path) -> None:
        """PRs with typo/clippy titles should be filtered even from git."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        _subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "checkout", "-b", "master"],
            cwd=repo,
            capture_output=True,
        )
        _subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"],
            cwd=repo,
            capture_output=True,
        )
        (repo / "f.rs").write_text("x\n")
        _subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        _subprocess.run(
            ["git", "commit", "-m", "fix typos in comments (#50)"],
            cwd=repo,
            capture_output=True,
        )

        output_dir = tmp_path / "cases"
        cases = mine_from_git(
            repo="ProvableHQ/leo",
            repo_dir=repo,
            since="2020-01-01",
            output_dir=output_dir,
        )
        assert len(cases) == 0


class TestMineFromGitCli:
    def test_from_git_flag_exists(self) -> None:
        from click.testing import CliRunner

        from bugeval.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mine", "--help"])
        assert "--from-git" in result.output
        assert "--repo-dir" in result.output
