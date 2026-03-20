"""Tests for scrape-reviewer-comments CLI and bot filtering."""

from __future__ import annotations


def test_bot_comments_filtered_from_signals() -> None:
    from bugeval.github_scraper import extract_reviewer_bug_signals

    reviews = [
        {"body": "<!-- cubic:attribution --> This is a bug", "_source": "review", "state": ""},
        {"body": "This has a real bug in the loop", "_source": "review", "state": ""},
    ]
    signals, notes = extract_reviewer_bug_signals(reviews)
    assert len(notes) == 1
    assert "real bug" in notes[0]


def test_bot_findings_filtered() -> None:
    from bugeval.github_scraper import _parse_reviewer_findings

    items = [
        {"_source": "inline", "_path": "f.rs", "_line": 10, "body": "coderabbit: suggestion"},
        {"_source": "inline", "_path": "g.rs", "_line": 20, "body": "This variable is wrong"},
    ]
    findings = _parse_reviewer_findings(items)
    assert len(findings) == 1
    assert findings[0].file == "g.rs"


def test_scrape_reviewer_comments_help() -> None:
    from click.testing import CliRunner

    from bugeval.scrape_reviewer_comments import scrape_reviewer_comments

    runner = CliRunner()
    result = runner.invoke(scrape_reviewer_comments, ["--help"])
    assert result.exit_code == 0
    assert "--cases-dir" in result.output
    assert "--skip-existing" in result.output
