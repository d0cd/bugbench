"""Evaluation orchestrator: dispatch to runners, manage checkpoints."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import click
import yaml

from bugeval.git_utils import get_diff
from bugeval.io import (
    load_cases,
    load_checkpoint,
    save_checkpoint,
    save_result,
    write_run_metadata,
)
from bugeval.models import TestCase
from bugeval.result_models import ToolResult

log = logging.getLogger(__name__)

_GIT_TIMEOUT = 120
_GH_TIMEOUT = 120

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"


def load_tool_timeouts(
    config_path: Path = CONFIG_PATH,
) -> dict[str, int]:
    """Load per-tool timeout_seconds from config/config.yaml."""
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    tools = data.get("tools", {})
    result: dict[str, int] = {}
    for name, spec in tools.items():
        if isinstance(spec, dict) and "timeout_seconds" in spec:
            result[name] = int(spec["timeout_seconds"])
    return result


def resolve_timeout(
    tool: str,
    cli_timeout: int,
    tool_timeouts: dict[str, int],
) -> int:
    """Return tool-specific timeout if configured, else CLI default."""
    return tool_timeouts.get(tool, cli_timeout)


_PR_TOOLS = {"copilot", "greptile", "coderabbit"}

_SDK_TOOLS = {"agent-sdk", "agent-sdk-2pass", "agent-sdk-v3"}


# ---------------------------------------------------------------------------
# Docker infrastructure — wraps SDK runners in a container
# ---------------------------------------------------------------------------


def is_docker_available() -> bool:
    """Check if Docker daemon is reachable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_sdk_in_docker(
    case: TestCase,
    diff: str,
    workspace: Path | None,
    context_level: str,
    tool_name: str,
    timeout: int = 600,
    transcript_dir: Path | None = None,
    model: str = "",
    max_turns: int = 30,
    docker_image: str = "bugeval-agent",
) -> ToolResult:
    """Run Agent SDK inside Docker container for any SDK tool.

    This is the Docker wrapper — it builds the prompt, sends config to
    _docker_runner.py inside the container, and parses the result.
    The same SDK code runs inside Docker as would run locally.
    """
    import time

    from bugeval.agent_runner import (
        MODEL,
        build_system_prompt,
        build_user_prompt,
        parse_agent_findings,
        sanitize_diff,
    )

    sanitized = sanitize_diff(diff)
    system_prompt = build_system_prompt(context_level, bash_enabled=True)
    user_prompt = build_user_prompt(
        case,
        sanitized,
        context_level,
        inline_diff=False,
    )
    full_prompt = f"{system_prompt}\n\n{user_prompt}"

    # For 2pass, run explorer then reviewer
    if tool_name == "agent-sdk-2pass":
        return _run_2pass_in_docker(
            case,
            diff,
            workspace,
            context_level,
            full_prompt,
            timeout,
            transcript_dir,
            model,
            max_turns,
            docker_image,
        )

    effective_model = model or MODEL
    src_dir = Path(__file__).resolve().parent.parent
    if not workspace:
        return ToolResult(
            case_id=case.id,
            tool=tool_name,
            context_level=context_level,
            error="No workspace provided for Docker execution",
        )
    workspace_path = str(workspace.resolve())

    config = {
        "prompt": full_prompt,
        "model": effective_model,
        "max_turns": max_turns,
        "timeout": timeout,
        "cwd": "/work",
        "allowed_tools": ["Read", "Glob", "Grep", "Bash", "WebSearch"],
        "disallowed_tools": ["Edit", "Write", "NotebookEdit"],
    }

    start = time.monotonic()
    data = _docker_sdk_exec(config, workspace_path, src_dir, docker_image, timeout)
    elapsed = time.monotonic() - start

    if "error" in data and not data.get("result_text"):
        return ToolResult(
            case_id=case.id,
            tool=tool_name,
            context_level=context_level,
            time_seconds=round(elapsed, 2),
            error=data["error"],
        )

    result_text = data.get("result_text", "")
    cost = data.get("cost_usd", 0.0)
    comments = parse_agent_findings(result_text)

    tp = ""
    if transcript_dir:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        t_path = transcript_dir / f"{case.id}-sdk-docker.json"
        t_path.write_text(
            json.dumps(
                {
                    "model": effective_model,
                    "messages": data.get("messages", []),
                    "result_text": result_text,
                    "cost_usd": cost,
                    "elapsed_seconds": round(elapsed, 2),
                },
                indent=2,
                default=str,
            )
        )
        tp = str(t_path)

    return ToolResult(
        case_id=case.id,
        tool=tool_name,
        context_level=context_level,
        comments=comments,
        time_seconds=round(elapsed, 2),
        cost_usd=cost,
        transcript_path=tp,
    )


def _run_2pass_in_docker(
    case: TestCase,
    diff: str,
    workspace: Path | None,
    context_level: str,
    full_prompt: str,
    timeout: int,
    transcript_dir: Path | None,
    model: str,
    max_turns: int,
    docker_image: str,
) -> ToolResult:
    """Two-pass Docker execution: explorer + reviewer."""
    import time

    from bugeval.agent_runner import (
        _EXPLORER_PROMPT,
        _REVIEWER_PROMPT,
        MODEL,
        _scrub_fix_references,
        parse_agent_findings,
        sanitize_diff,
    )

    effective_model = model or MODEL
    src_dir = Path(__file__).resolve().parent.parent
    if not workspace:
        return ToolResult(
            case_id=case.id,
            tool="agent-sdk-2pass",
            context_level=context_level,
            error="No workspace provided for Docker execution",
        )
    workspace_path = str(workspace.resolve())

    sanitized = sanitize_diff(diff)
    description = ""
    if workspace:
        desc_path = workspace / ".pr" / "description.md"
        if desc_path.exists():
            description = desc_path.read_text()[:2000]

    start = time.monotonic()

    # Pass 1: Explorer
    explorer_prompt = _EXPLORER_PROMPT + "\nRead diff.patch and .pr/description.md."
    explorer_config = {
        "prompt": explorer_prompt,
        "model": effective_model,
        "max_turns": max_turns,
        "timeout": timeout // 2,
        "cwd": "/work",
        "allowed_tools": ["Read", "Glob", "Grep", "Bash", "WebSearch"],
        "disallowed_tools": ["Edit", "Write", "NotebookEdit"],
    }
    explorer_data = _docker_sdk_exec(
        explorer_config,
        workspace_path,
        src_dir,
        docker_image,
        timeout // 2,
    )
    context_text = explorer_data.get("result_text", "") or "(Explorer produced no output)"

    # Pass 2: Reviewer
    reviewer_prompt = _REVIEWER_PROMPT.format(
        diff=sanitized[:15000],
        description=_scrub_fix_references(description),
        context=context_text[:10000],
    )
    reviewer_config = {
        "prompt": reviewer_prompt,
        "model": effective_model,
        "max_turns": max_turns,
        "timeout": timeout // 2,
        "cwd": "/work",
        "allowed_tools": ["Read", "Glob", "Grep", "Bash", "WebSearch"],
        "disallowed_tools": ["Edit", "Write", "NotebookEdit"],
    }
    reviewer_data = _docker_sdk_exec(
        reviewer_config,
        workspace_path,
        src_dir,
        docker_image,
        timeout // 2,
    )

    elapsed = time.monotonic() - start
    reviewer_text = reviewer_data.get("result_text", "")
    total_cost = explorer_data.get("cost_usd", 0.0) + reviewer_data.get("cost_usd", 0.0)
    comments = parse_agent_findings(reviewer_text)

    tp = ""
    if transcript_dir:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        t_path = transcript_dir / f"{case.id}-2pass-docker.json"
        t_path.write_text(
            json.dumps(
                {
                    "tool": "agent-sdk-2pass",
                    "model": effective_model,
                    "explorer_output": context_text,
                    "explorer_messages": explorer_data.get("messages", []),
                    "reviewer_output": reviewer_text,
                    "reviewer_messages": reviewer_data.get("messages", []),
                    "cost_explorer": explorer_data.get("cost_usd", 0.0),
                    "cost_reviewer": reviewer_data.get("cost_usd", 0.0),
                    "time_total": round(elapsed, 2),
                },
                indent=2,
                default=str,
            )
        )
        tp = str(t_path)

    return ToolResult(
        case_id=case.id,
        tool="agent-sdk-2pass",
        context_level=context_level,
        comments=comments,
        time_seconds=round(elapsed, 2),
        cost_usd=total_cost,
        transcript_path=tp,
    )


def _run_v3_in_docker(
    case: TestCase,
    diff: str,
    workspace: Path | None,
    context_level: str,
    timeout: int = 900,
    transcript_dir: Path | None = None,
    model: str = "",
    max_turns: int = 40,
    docker_image: str = "bugeval-agent",
) -> ToolResult:
    """Three-phase Docker execution in a SINGLE container with shared session."""
    import time

    from bugeval.agent_runner import (
        _V3_PHASE1_SURVEY,
        _V3_PHASE2_INVESTIGATE,
        _V3_PHASE3_REPORT,
        _V3_SYSTEM,
        annotate_diff,
        parse_agent_findings,
        sanitize_diff,
    )

    effective_model = model or "claude-opus-4-6"
    src_dir = Path(__file__).resolve().parent.parent
    if not workspace:
        return ToolResult(
            case_id=case.id,
            tool="agent-sdk-v3",
            context_level=context_level,
            error="No workspace provided for Docker execution",
        )
    workspace_path = str(workspace.resolve())

    # Write annotated diff + domain file into workspace
    if workspace:
        sanitized = sanitize_diff(diff)
        annotated = annotate_diff(sanitized)
        diff_path = workspace / "diff.patch"
        if diff_path.exists():
            diff_path.write_text(annotated)
        domain_src = src_dir / "config" / "domain" / "compiler.md"
        if domain_src.exists():
            pr_dir = workspace / ".pr"
            pr_dir.mkdir(parents=True, exist_ok=True)
            (pr_dir / "domain.md").write_text(domain_src.read_text())

    start = time.monotonic()

    # Single container, multi-prompt: all 3 phases share one SDK session
    config = {
        "prompts": [
            _V3_SYSTEM
            + "\n\n"
            + _V3_PHASE1_SURVEY
            + "\nStart by reading diff.patch and .pr/domain.md.",
            _V3_PHASE2_INVESTIGATE,
            _V3_PHASE3_REPORT,
        ],
        "model": effective_model,
        "max_turns": max_turns,
        "timeout": timeout,
        "cwd": "/work",
        "allowed_tools": [
            "Read",
            "Glob",
            "Grep",
            "Bash",
            "WebSearch",
        ],
        "disallowed_tools": ["Edit", "Write", "NotebookEdit"],
    }
    data = _docker_sdk_exec(
        config,
        workspace_path,
        src_dir,
        docker_image,
        timeout,
    )

    elapsed = time.monotonic() - start
    result_text = data.get("result_text", "")
    total_cost = data.get("cost_usd", 0.0)
    phase_results = data.get("phase_results", [])

    # Parse findings from the last phase (report)
    comments = parse_agent_findings(result_text)

    # Fallback: if report phase produced nothing, try earlier phases
    if not comments and phase_results:
        for pr in reversed(phase_results):
            comments = parse_agent_findings(pr)
            if comments:
                result_text = pr
                break

    tp = ""
    if transcript_dir:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        t_path = transcript_dir / f"{case.id}-v3-docker.json"
        t_path.write_text(
            json.dumps(
                {
                    "tool": "agent-sdk-v3",
                    "model": effective_model,
                    "phase_results": phase_results,
                    "messages": data.get("messages", []),
                    "cost_usd": total_cost,
                    "time_total": round(elapsed, 2),
                },
                indent=2,
                default=str,
            )
        )
        tp = str(t_path)

    return ToolResult(
        case_id=case.id,
        tool="agent-sdk-v3",
        context_level=context_level,
        comments=comments,
        time_seconds=round(elapsed, 2),
        cost_usd=total_cost,
        transcript_path=tp,
    )


def _docker_sdk_exec(
    config: dict[str, Any],
    workspace_path: str,
    src_dir: Path,
    image: str,
    timeout: int,
) -> dict[str, Any]:
    """Run _docker_runner.py inside a Docker container and return parsed JSON."""
    config_json = json.dumps(config)
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-i",
        "-e",
        "ANTHROPIC_API_KEY",
        "-e",
        "HOME=/home/agent",
        "-e",
        "CLAUDE_CONFIG_DIR=/home/agent/.claude",
        "-v",
        f"{workspace_path}:/work",
        "-v",
        "bugeval-claude-auth:/home/agent/.claude",
        "-v",
        f"{src_dir}:/app/src:ro",
        "-w",
        "/work",
        image,
        "python3",
        "/app/src/bugeval/_docker_runner.py",
    ]

    try:
        result = subprocess.run(
            docker_cmd,
            input=config_json,
            capture_output=True,
            text=True,
            timeout=timeout + 60,
        )
    except subprocess.TimeoutExpired:
        return {
            "error": f"Docker container timed out after {timeout}s",
            "result_text": "",
            "cost_usd": 0.0,
            "messages": [],
        }
    except FileNotFoundError:
        return {
            "error": "docker CLI not found on PATH",
            "result_text": "",
            "cost_usd": 0.0,
            "messages": [],
        }

    if not result.stdout.strip():
        log.warning(
            "Docker SDK empty stdout (rc=%d): %s",
            result.returncode,
            result.stderr[:200],
        )
        return {
            "error": f"Docker returned empty stdout (rc={result.returncode})",
            "result_text": "",
            "cost_usd": 0.0,
            "messages": [],
        }

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        log.warning("Docker SDK invalid JSON: %s", result.stdout[:200])
        return {
            "error": "Docker returned invalid JSON",
            "result_text": result.stdout[:500],
            "cost_usd": 0.0,
            "messages": [],
        }


def ensure_per_tool_clone(
    tool: str,
    repo_dir: Path,
) -> Path:
    """Create a per-tool local clone to avoid git lock contention.

    Returns repo_dir/{basename}-{tool} if the tool is PR-based,
    otherwise returns repo_dir unchanged.
    """
    if tool not in _PR_TOOLS:
        return repo_dir
    clone_dir = repo_dir.parent / f"{repo_dir.name}-{tool}"
    if clone_dir.exists():
        # Validate clone is healthy
        if not (clone_dir / ".git").exists():
            raise RuntimeError(f"{clone_dir} is not a valid git clone (missing .git)")
        # Fetch to pick up any new commits from the source repo
        try:
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=clone_dir,
                check=True,
                capture_output=True,
                timeout=_GIT_TIMEOUT,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass  # best-effort; clone still usable
        # Reset working tree to clean state
        try:
            subprocess.run(
                ["git", "checkout", "-f", "HEAD"],
                cwd=clone_dir,
                check=True,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=clone_dir,
                check=True,
                capture_output=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass  # best-effort
        # Validate clone is usable after reset
        subprocess.run(
            ["git", "status"],
            cwd=clone_dir,
            check=True,
            capture_output=True,
            timeout=30,
        )
        return clone_dir

    log.info(
        "Creating per-tool clone: %s -> %s",
        repo_dir,
        clone_dir,
    )
    try:
        subprocess.run(
            ["git", "clone", "--local", str(repo_dir), str(clone_dir)],
            check=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Clean up incomplete clone directory
        import shutil

        if clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)
        raise
    return clone_dir


_checkpoint_lock = threading.Lock()
_API_SEMAPHORE = threading.Semaphore(5)  # Max 5 concurrent API calls


def get_diff_for_case(case: TestCase, repo_dir: Path) -> str:
    """Get the introducing commit's diff (what the tool reviews)."""
    introducing = None
    if case.truth and case.truth.introducing_commit:
        introducing = case.truth.introducing_commit
    if not introducing:
        log.warning(
            "No introducing commit for case %s, skipping",
            case.id,
        )
        return ""
    # Diff introducing commit against its parent
    return get_diff(f"{introducing}~1", introducing, cwd=repo_dir)


def result_filename(case_id: str, tool: str, context: str) -> str:
    """Build the result filename for a case/tool/context combo."""
    if context:
        return f"{case_id}--{tool}--{context}.yaml"
    return f"{case_id}--{tool}.yaml"


def _checkpoint_key(case_id: str, tool: str, context: str) -> str:
    if context:
        return f"{case_id}::{tool}::{context}"
    return f"{case_id}::{tool}"


def process_case(
    case: TestCase,
    tool: str,
    context_level: str,
    repo_dir: Path,
    run_dir: Path,
    timeout: int,
    thinking_budget: int = 0,
    max_turns: int = 30,
    model: str = "",
    org: str = "",
    docker: bool = False,
    docker_image: str = "bugeval-agent",
) -> ToolResult:
    """Dispatch to the appropriate runner and save the result."""
    import time

    t0 = time.monotonic()
    diff = get_diff_for_case(case, repo_dir)
    t_diff = time.monotonic() - t0

    # Unified workspace setup for agent tools
    workspace: Path | None = None
    _is_agent_tool = tool.startswith("agent") or tool == "agent"
    if _is_agent_tool:
        from bugeval.agent_runner import (
            materialize_workspace,
            sanitize_diff,
            setup_workspace,
        )

        ws_dir = run_dir / "workspaces"
        ws_dir.mkdir(parents=True, exist_ok=True)

        if context_level != "diff-only":
            workspace = setup_workspace(
                case,
                repo_dir or Path("."),
                context_level,
                ws_dir,
            )
        else:
            # diff-only: create a minimal workspace with just the diff
            workspace = ws_dir / f"{case.id}-diffonly"
            workspace.mkdir(parents=True, exist_ok=True)

        # Write diff.patch + .pr/ metadata into workspace
        # materialize_workspace may return a different path for diff-only
        if workspace:
            workspace = materialize_workspace(
                case,
                sanitize_diff(diff),
                workspace,
                context_level,
            )
    elif not _is_agent_tool:
        # PR tools use repo_dir directly
        workspace = repo_dir if context_level != "diff-only" else None

    transcript_dir = run_dir / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)

    # Dispatch by tool name
    if tool == "greptile":
        from bugeval.greptile_runner import run_greptile

        result = run_greptile(
            case,
            repo_dir,
            timeout=timeout,
            org=org,
            transcript_dir=transcript_dir,
        )
    elif tool == "coderabbit":
        from bugeval.coderabbit_runner import run_coderabbit

        result = run_coderabbit(
            case,
            repo_dir,
            timeout=timeout,
            org=org,
            transcript_dir=transcript_dir,
        )
    elif tool == "copilot":
        from bugeval.copilot_runner import run_copilot

        result = run_copilot(
            case,
            repo_dir,
            timeout=timeout,
            org=org,
            transcript_dir=transcript_dir,
        )
    elif tool == "agent":
        from bugeval.agent_runner import run_anthropic_api

        result = run_anthropic_api(
            case,
            diff,
            workspace,
            context_level,
            timeout=timeout,
            transcript_dir=transcript_dir,
            thinking_budget=thinking_budget,
            model=model,
        )
    elif tool == "agent-gemini":
        from bugeval.agent_runner import run_google_api

        result = run_google_api(
            case,
            diff,
            workspace,
            context_level,
            timeout=timeout,
            transcript_dir=transcript_dir,
            thinking_budget=thinking_budget,
            model=model,
        )
    elif tool == "agent-openai":
        from bugeval.agent_runner import run_openai_api

        result = run_openai_api(
            case,
            diff,
            workspace,
            context_level,
            timeout=timeout,
            transcript_dir=transcript_dir,
            thinking_budget=thinking_budget,
            model=model,
        )
    elif tool.startswith("agent-cli"):
        from bugeval.agent_runner import run_agent_cli

        parts = tool.split("-", 2)
        cli_name = parts[2] if len(parts) > 2 else "claude"
        result = run_agent_cli(
            case,
            diff,
            workspace,
            context_level,
            cli_tool=cli_name,
            timeout=timeout,
            transcript_dir=transcript_dir,
            model=model,
        )
    elif tool in _SDK_TOOLS and docker:
        if tool == "agent-sdk-v3":
            result = _run_v3_in_docker(
                case,
                diff,
                workspace,
                context_level,
                timeout=timeout,
                transcript_dir=transcript_dir,
                model=model,
                max_turns=max_turns,
                docker_image=docker_image,
            )
        else:
            # Docker wrapping for agent-sdk and agent-sdk-2pass
            result = _run_sdk_in_docker(
                case,
                diff,
                workspace,
                context_level,
                tool_name=tool,
                timeout=timeout,
                transcript_dir=transcript_dir,
                model=model,
                max_turns=max_turns,
                docker_image=docker_image,
            )
    elif tool == "agent-sdk":
        from bugeval.agent_runner import run_agent_sdk

        result = run_agent_sdk(
            case,
            diff,
            workspace,
            context_level,
            timeout=timeout,
            transcript_dir=transcript_dir,
            model=model,
            max_turns=max_turns,
        )
    elif tool == "agent-sdk-2pass":
        from bugeval.agent_runner import run_agent_sdk_2pass

        result = run_agent_sdk_2pass(
            case,
            diff,
            workspace,
            context_level,
            timeout=timeout,
            transcript_dir=transcript_dir,
            model=model,
            max_turns=max_turns,
        )
    elif tool == "agent-sdk-v3":
        from bugeval.agent_runner import run_agent_sdk_v3

        result = run_agent_sdk_v3(
            case,
            diff,
            workspace,
            context_level,
            timeout=timeout,
            transcript_dir=transcript_dir,
            model=model,
            max_turns=max_turns,
        )
    else:
        # Unsupported tool — return error result
        result = ToolResult(
            case_id=case.id,
            tool=tool,
            context_level=context_level,
            error=f"Unsupported tool: {tool}",
        )

    # Log phase timing
    t_total = time.monotonic() - t0
    log.info(
        "Timing %s/%s: diff=%.1fs runner=%.1fs total=%.1fs cost=$%.2f",
        case.id,
        tool,
        t_diff,
        result.time_seconds,
        t_total,
        result.cost_usd,
    )

    # Save result
    results_dir = run_dir / "results"
    fname = result_filename(case.id, tool, context_level)
    save_result(result, results_dir / fname)
    return result


def evaluate_tool(
    tool: str,
    cases_dir: Path,
    run_dir: Path,
    context_level: str,
    repo_dir: Path,
    concurrency: int,
    timeout: int,
    dry_run: bool,
    thinking_budget: int = 0,
    max_turns: int = 30,
    model: str = "",
    org: str = "",
    docker: bool = False,
    docker_image: str = "bugeval-agent",
) -> None:
    """Main orchestrator: load cases, process each, checkpoint progress."""
    # Load per-tool timeouts; CLI --timeout is the fallback default
    tool_timeouts = load_tool_timeouts()
    effective_timeout = resolve_timeout(tool, timeout, tool_timeouts)
    if effective_timeout != timeout:
        log.info(
            "Using config timeout %ds for %s (CLI default: %ds)",
            effective_timeout,
            tool,
            timeout,
        )
    timeout = effective_timeout

    # Auto-create per-tool local clone for PR tools to avoid lock contention
    if org and tool in _PR_TOOLS:
        repo_dir = ensure_per_tool_clone(tool, repo_dir)

    cases = load_cases(cases_dir)
    if not cases:
        log.warning("No cases found in %s", cases_dir)
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = run_dir / "checkpoint.json"
    done = load_checkpoint(checkpoint_path)

    # Validate checkpoint consistency
    results_dir = run_dir / "results"
    if done:
        stale_keys = []
        for key in list(done):
            parts = key.split("::")
            case_id = parts[0]
            # Check if result file exists
            pattern = f"{case_id}--*"
            if not list(results_dir.glob(pattern)):
                stale_keys.append(key)
        if stale_keys:
            log.warning(
                "Removing %d stale checkpoint entries (missing result files): %s",
                len(stale_keys),
                stale_keys[:5],
            )
            done -= set(stale_keys)
            save_checkpoint(done, checkpoint_path)

    # Write run metadata
    metadata_path = run_dir / "run_metadata.json"
    if not metadata_path.exists():
        write_run_metadata(
            run_dir,
            tool,
            context_level,
            cases_dir,
            model=model,
            thinking_budget=thinking_budget,
            timeout=timeout,
        )

    # Filter to pending cases
    pending: list[TestCase] = []
    for c in cases:
        key = _checkpoint_key(c.id, tool, context_level)
        if key not in done:
            pending.append(c)

    log.info(
        "Evaluating %s: %d pending, %d done, %d total",
        tool,
        len(pending),
        len(done),
        len(cases),
    )

    if dry_run:
        for c in pending:
            log.info("[dry-run] Would process %s with %s", c.id, tool)
        return

    checkpoint_batch_size = 5

    completed = 0
    total = len(pending)

    if concurrency <= 1:
        pending_keys: list[str] = []
        for c in pending:
            try:
                process_case(
                    c,
                    tool,
                    context_level,
                    repo_dir,
                    run_dir,
                    timeout,
                    thinking_budget=thinking_budget,
                    max_turns=max_turns,
                    model=model,
                    org=org,
                    docker=docker,
                    docker_image=docker_image,
                )
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                OSError,
                json.JSONDecodeError,
                KeyError,
                ValueError,
            ):
                log.exception("Error processing %s", c.id)
                continue
            key = _checkpoint_key(c.id, tool, context_level)
            pending_keys.append(key)
            completed += 1
            log.info("Evaluated %d/%d: %s", completed, total, c.id)
            if len(pending_keys) >= checkpoint_batch_size:
                with _checkpoint_lock:
                    done.update(pending_keys)
                    save_checkpoint(done, checkpoint_path)
                pending_keys = []
        if pending_keys:
            with _checkpoint_lock:
                done.update(pending_keys)
                save_checkpoint(done, checkpoint_path)
    else:

        def _throttled_process_case(c: TestCase) -> ToolResult:
            with _API_SEMAPHORE:
                return process_case(
                    c,
                    tool,
                    context_level,
                    repo_dir,
                    run_dir,
                    timeout,
                    thinking_budget=thinking_budget,
                    max_turns=max_turns,
                    model=model,
                    org=org,
                    docker=docker,
                    docker_image=docker_image,
                )

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_throttled_process_case, c): c for c in pending}
            pending_keys_concurrent: list[str] = []
            for future in as_completed(futures):
                c = futures[future]
                try:
                    future.result()
                    key = _checkpoint_key(c.id, tool, context_level)
                    pending_keys_concurrent.append(key)
                    completed += 1
                    log.info("Evaluated %d/%d: %s", completed, total, c.id)
                    if len(pending_keys_concurrent) >= checkpoint_batch_size:
                        with _checkpoint_lock:
                            done.update(pending_keys_concurrent)
                            save_checkpoint(done, checkpoint_path)
                        pending_keys_concurrent = []
                except (
                    subprocess.CalledProcessError,
                    subprocess.TimeoutExpired,
                    OSError,
                    json.JSONDecodeError,
                    KeyError,
                    ValueError,
                ):
                    log.warning(
                        "Error processing %s, will retry next run",
                        c.id,
                    )
            if pending_keys_concurrent:
                with _checkpoint_lock:
                    done.update(pending_keys_concurrent)
                    save_checkpoint(done, checkpoint_path)

    click.echo(f"Evaluation complete: {completed}/{total} cases processed")
