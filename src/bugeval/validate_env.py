"""CLI command: validate-env — pre-flight check of env vars and config."""

from __future__ import annotations

import os
from pathlib import Path

import click

from bugeval.pr_eval_models import EvalConfig, ToolType, load_eval_config


class EnvCheckResult:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.ok: list[str] = []

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


def check_env(config: EvalConfig, cases_dir: Path | None = None) -> EnvCheckResult:
    """Run all pre-flight checks. Returns an EnvCheckResult."""
    result = EnvCheckResult()

    # Always-required keys
    for key in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN"):
        if os.environ.get(key):
            result.ok.append(f"{key} is set")
        else:
            result.errors.append(f"{key} is not set")

    # Tool-specific API keys (skip keys already checked in the always-required list above)
    _always_checked = {"ANTHROPIC_API_KEY", "GITHUB_TOKEN"}
    for tool in config.tools:
        if tool.api_key_env and tool.api_key_env not in _always_checked:
            if os.environ.get(tool.api_key_env):
                result.ok.append(f"{tool.api_key_env} is set (for {tool.name})")
            else:
                result.errors.append(f"{tool.api_key_env} not set (required for {tool.name})")

    # eval_org required if any PR tools configured
    pr_tools = [t for t in config.tools if t.type == ToolType.pr]
    if pr_tools:
        if config.eval_org:
            result.ok.append(f"eval_org = {config.eval_org!r}")
        else:
            result.errors.append("github.eval_org is empty but PR tools are configured")

    # repos must be non-empty
    if config.repos:
        result.ok.append(f"{len(config.repos)} repo(s) configured")
    else:
        result.warnings.append("config.repos is empty — no repos to evaluate")

    # optional: cases dir
    if cases_dir is not None and cases_dir.exists():
        yamls = list(cases_dir.glob("*.yaml"))
        if yamls:
            result.ok.append(f"{len(yamls)} case(s) found in {cases_dir}")
        else:
            result.warnings.append(f"No case YAML files in {cases_dir}")

    return result


@click.command("validate-env")
@click.option(
    "--config",
    "config_path",
    default="config/config.yaml",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to config.yaml",
)
@click.option(
    "--cases-dir",
    default=None,
    type=click.Path(dir_okay=True, file_okay=False, path_type=Path),
    help="Optional: directory to check for case YAML files",
)
def validate_env(config_path: str, cases_dir: Path | None) -> None:
    """Pre-flight check: verify env vars and config before starting a run."""
    config = load_eval_config(Path(config_path))
    result = check_env(config, cases_dir)

    for msg in result.ok:
        click.echo(f"  [ok]   {msg}")
    for msg in result.warnings:
        click.echo(f"  [warn] {msg}", err=True)
    for msg in result.errors:
        click.echo(f"  [fail] {msg}", err=True)

    if result.passed:
        click.echo("\nAll checks passed.")
    else:
        click.echo(f"\n{len(result.errors)} check(s) failed.", err=True)
        raise SystemExit(1)
