"""Pure helper functions for the dashboard (no Flask dependency)."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from bugeval.models import TestCase
from bugeval.validate_cases import (
    AlignmentStatus,
    _case_level_status,
    validate_case_alignment,
)


def classify_runner_type(tool_name: str) -> str:
    """Returns 'Commercial', 'CLI', or 'API' based on tool name prefix."""
    cli_prefixes = ("claude-code-cli", "claude-cli", "gemini-cli", "codex-cli")
    if tool_name in cli_prefixes or any(tool_name.startswith(p) for p in cli_prefixes):
        return "CLI"
    api_prefixes = ("anthropic-api", "claude-api", "openai-api")
    if tool_name in api_prefixes or any(tool_name.startswith(p) for p in api_prefixes):
        return "API"
    return "Commercial"


def group_agg_by_runner(agg: dict[str, dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Groups aggregate_scores output by runner type for sectioned display."""
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for tool, metrics in agg.items():
        runner = classify_runner_type(tool)
        grouped.setdefault(runner, {})[tool] = metrics
    return grouped


def load_alignment_for_cases(
    cases: list[TestCase], patches_dir: Path
) -> dict[str, AlignmentStatus]:
    """Returns {case_id: AlignmentStatus}. Falls back to quality_flags if patches missing."""
    result: dict[str, AlignmentStatus] = {}
    for case in cases:
        patch_path = patches_dir / f"{case.id}.patch"
        if patch_path.exists():
            patch_text = patch_path.read_text()
            per_finding = validate_case_alignment(case, patch_text)
            result[case.id] = _case_level_status(per_finding)
        elif any(f.startswith("alignment-") for f in case.quality_flags):
            if "alignment-verified" in case.quality_flags:
                result[case.id] = AlignmentStatus.aligned
            else:
                result[case.id] = AlignmentStatus.misaligned
        else:
            result[case.id] = AlignmentStatus.aligned  # no patch, assume ok
    return result


def md_to_html(md_text: str) -> str:
    """Lightweight markdown to HTML converter (tables, headings, bold, hr, code)."""
    lines = md_text.split("\n")
    html_lines: list[str] = []
    in_table = False

    for line in lines:
        stripped = line.strip()

        # Horizontal rule
        if re.match(r"^-{3,}$", stripped):
            if in_table:
                html_lines.append("</tbody></table>")
                in_table = False
            html_lines.append("<hr>")
            continue

        # Headings
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            if in_table:
                html_lines.append("</tbody></table>")
                in_table = False
            level = len(heading_match.group(1))
            text = _inline_format(heading_match.group(2))
            html_lines.append(f"<h{level}>{text}</h{level}>")
            continue

        # Table rows
        if "|" in stripped and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            # Skip separator rows
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue
            if not in_table:
                in_table = True
                html_lines.append("<table><thead><tr>")
                for c in cells:
                    html_lines.append(f"<th>{_inline_format(c)}</th>")
                html_lines.append("</tr></thead><tbody>")
                continue
            html_lines.append("<tr>")
            for c in cells:
                html_lines.append(f"<td>{_inline_format(c)}</td>")
            html_lines.append("</tr>")
            continue

        # Close table if we left it
        if in_table:
            html_lines.append("</tbody></table>")
            in_table = False

        # Empty line
        if not stripped:
            continue

        # Paragraph
        html_lines.append(f"<p>{_inline_format(stripped)}</p>")

    if in_table:
        html_lines.append("</tbody></table>")

    return "\n".join(html_lines)


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting: bold, code, italic."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    return text


def compute_comparison_data(
    run_dirs: list[Path],
) -> dict[str, list[dict[str, Any]]]:
    """Returns {tool: [{run, catch_rate, avg_score}, ...]} for comparison table."""
    import yaml
    from pydantic import ValidationError

    from bugeval.analyze import aggregate_scores
    from bugeval.judge_models import JudgeScore

    tool_data: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for run_dir in run_dirs:
        scores_dir = run_dir / "scores"
        if not scores_dir.exists():
            continue
        scores: list[JudgeScore] = []
        for path in sorted(scores_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text()) or {}
            try:
                scores.append(JudgeScore(**data))
            except (ValidationError, TypeError):
                pass
        if not scores:
            continue
        agg = aggregate_scores(scores)
        for tool, metrics in agg.items():
            tool_data[tool].append(
                {
                    "run": run_dir.name,
                    "catch_rate": metrics["catch_rate"],
                    "avg_score": metrics["avg_score"],
                }
            )

    return dict(tool_data)


def compute_dataset_stats(cases: list[TestCase]) -> dict[str, Any]:
    """Returns distributions, quality stats, expected findings list for dataset inspector."""
    total = len(cases)
    verified = sum(1 for c in cases if c.verified)
    needs_review = sum(1 for c in cases if c.needs_manual_review)

    total_findings = sum(len(c.expected_findings) for c in cases)
    avg_findings = total_findings / total if total else 0.0

    # Distributions
    distributions: dict[str, dict[str, int]] = {}
    dist_fields = (
        "category",
        "difficulty",
        "severity",
        "language",
        "repo",
        "pr_size",
        "visibility",
    )
    for field in dist_fields:
        counts: dict[str, int] = {}
        for c in cases:
            val = getattr(c, field)
            key = val.value if hasattr(val, "value") else str(val)
            counts[key] = counts.get(key, 0) + 1
        distributions[field] = dict(sorted(counts.items()))

    # Quality flags
    grounded_pass = sum(1 for c in cases if "groundedness-failed" not in c.quality_flags)
    grounded_fail = total - grounded_pass

    # Expected findings flat list
    findings_list: list[dict[str, str]] = []
    for c in cases:
        for ef in c.expected_findings:
            findings_list.append(
                {
                    "case_id": c.id,
                    "repo": c.repo,
                    "file": ef.file,
                    "line": str(ef.line),
                    "summary": ef.summary,
                    "category": c.category.value,
                }
            )

    return {
        "total": total,
        "verified": verified,
        "needs_review": needs_review,
        "avg_findings": avg_findings,
        "distributions": distributions,
        "grounded_pass": grounded_pass,
        "grounded_fail": grounded_fail,
        "findings_list": findings_list,
    }
