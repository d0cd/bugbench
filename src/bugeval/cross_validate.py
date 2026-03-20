"""Cross-model ground truth validation for expected findings."""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Any

import click
import yaml

from bugeval.io import load_all_cases
from bugeval.models import TestCase


class FindingVerdict:
    """Result of cross-validating a single expected finding."""

    # Use a simple dict-based approach rather than pydantic for lightweight output


_CROSS_VALIDATE_PROMPT = """\
Given this diff, evaluate each expected finding. For each finding, respond \
with a JSON object:
{"verdicts": [{"index": N, "verdict": "confirmed"|"disputed"|"ambiguous", \
"reason": "..."}]}

A finding is:
- "confirmed" if the issue described is real and visible in the diff
- "disputed" if the finding is incorrect, not visible in the diff, or \
describes something that isn't actually a bug
- "ambiguous" if you cannot determine with confidence whether the finding \
is real
"""


def cross_validate_case(
    case: TestCase,
    diff_content: str,
    model: str = "gemini-2.5-pro",
) -> list[dict[str, Any]]:
    """Send expected findings + diff to a non-Claude model for verification.

    Returns list of dicts with keys: index, finding_summary, verdict, reason.
    """
    findings_text = "\n".join(
        f"  [{i}] file={f.file}, line={f.line}: {f.summary}"
        for i, f in enumerate(case.expected_findings)
    )

    user_prompt = (
        f"## Expected Findings\n{findings_text}\n\n"
        f"## Diff\n```diff\n{diff_content[:8000]}\n```\n\n"
        "Evaluate each finding. Return ONLY the JSON object."
    )

    text = _call_model(model, _CROSS_VALIDATE_PROMPT, user_prompt)

    return _parse_verdicts(text, case)


def _call_model(
    model: str, system_prompt: str, user_prompt: str
) -> str:
    if model.startswith("gemini-"):
        return _call_google(model, system_prompt, user_prompt)
    elif model.startswith(("gpt-", "o4-", "o3-")):
        return _call_openai(model, system_prompt, user_prompt)
    else:
        raise ValueError(f"Unsupported cross-validation model: {model!r}")


def _call_google(
    model: str, system_prompt: str, user_prompt: str
) -> str:
    import google.genai as genai  # type: ignore[import-untyped]

    api_key = os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=api_key)
    cfg = genai.types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=2048,
        temperature=0,
    )
    response = client.models.generate_content(
        model=model,
        contents=[
            genai.types.Content(
                role="user",
                parts=[genai.types.Part(text=user_prompt)],
            )
        ],
        config=cfg,
    )
    if response.candidates:
        content = response.candidates[0].content
        parts = (content.parts if content else None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                return text
    return ""


def _call_openai(
    model: str, system_prompt: str, user_prompt: str
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=2048,
        temperature=0,
    )
    return response.choices[0].message.content or ""


def _parse_verdicts(
    text: str, case: TestCase
) -> list[dict[str, Any]]:
    fence = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL
    )
    raw = fence.group(1) if fence else text

    brace = re.search(r"\{.*\}", raw, re.DOTALL)
    if not brace:
        return [
            {
                "index": i,
                "finding_summary": f.summary,
                "verdict": "ambiguous",
                "reason": "Failed to parse cross-validation response",
            }
            for i, f in enumerate(case.expected_findings)
        ]

    try:
        data = json.loads(brace.group(0))
    except json.JSONDecodeError:
        return [
            {
                "index": i,
                "finding_summary": f.summary,
                "verdict": "ambiguous",
                "reason": "Failed to parse cross-validation response",
            }
            for i, f in enumerate(case.expected_findings)
        ]

    raw_verdicts = data.get("verdicts", [])
    results: list[dict[str, Any]] = []
    for i, f in enumerate(case.expected_findings):
        matching = next(
            (v for v in raw_verdicts if v.get("index") == i), None
        )
        if matching:
            verdict = matching.get("verdict", "ambiguous")
            if verdict not in ("confirmed", "disputed", "ambiguous"):
                verdict = "ambiguous"
            results.append({
                "index": i,
                "finding_summary": f.summary,
                "verdict": verdict,
                "reason": matching.get("reason", ""),
            })
        else:
            results.append({
                "index": i,
                "finding_summary": f.summary,
                "verdict": "ambiguous",
                "reason": "No verdict returned for this finding",
            })
    return results


@click.command("cross-validate")
@click.option(
    "--cases-dir",
    required=True,
    type=click.Path(exists=True),
    help="Cases directory",
)
@click.option(
    "--model",
    default="gemini-2.5-pro",
    show_default=True,
    help="Non-Claude model for cross-validation",
)
@click.option(
    "--sample-rate",
    default=0.1,
    show_default=True,
    type=float,
    help="Fraction of cases to sample (0.0-1.0)",
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(),
    help="Output directory",
)
@click.option(
    "--patches-dir",
    default="patches/",
    show_default=True,
    type=click.Path(),
    help="Directory containing .patch files",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print sample without calling API",
)
@click.option(
    "--seed",
    default=42,
    show_default=True,
    type=int,
    help="Random seed for sampling",
)
def cross_validate(
    cases_dir: str,
    model: str,
    sample_rate: float,
    output_dir: str,
    patches_dir: str,
    dry_run: bool,
    seed: int,
) -> None:
    """Cross-validate expected findings with a non-Claude model."""
    cases = load_all_cases(Path(cases_dir))
    cases = [
        c for c in cases if c.expected_findings and c.valid_for_code_review
    ]

    rng = random.Random(seed)
    sample_size = max(1, int(len(cases) * sample_rate))
    sample = rng.sample(cases, min(sample_size, len(cases)))

    click.echo(
        f"Sampled {len(sample)} cases "
        f"(of {len(cases)} valid) for cross-validation"
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    patches = Path(patches_dir)

    confirmed = 0
    disputed = 0
    ambiguous = 0

    for case in sample:
        patch_file = patches / f"{case.id}.patch"
        diff_content = (
            patch_file.read_text() if patch_file.exists() else ""
        )

        if dry_run:
            click.echo(
                f"  [dry-run] {case.id}: "
                f"{len(case.expected_findings)} findings"
            )
            continue

        click.echo(f"  [validating] {case.id}...")
        verdicts = cross_validate_case(
            case, diff_content, model=model
        )

        result = {
            "case_id": case.id,
            "model": model,
            "verdicts": verdicts,
        }
        (out / f"{case.id}.yaml").write_text(
            yaml.safe_dump(result, sort_keys=False)
        )

        for v in verdicts:
            if v["verdict"] == "confirmed":
                confirmed += 1
            elif v["verdict"] == "disputed":
                disputed += 1
            else:
                ambiguous += 1

        click.echo(
            f"  [done] {case.id}: "
            f"{[v['verdict'] for v in verdicts]}"
        )

    if not dry_run:
        total = confirmed + disputed + ambiguous
        click.echo(
            f"\nSummary: {confirmed} confirmed, "
            f"{disputed} disputed, "
            f"{ambiguous} ambiguous (of {total} findings)"
        )
