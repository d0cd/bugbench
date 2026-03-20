"""Pydantic models for PR-mode evaluation state and configuration."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ToolType(StrEnum):
    pr = "pr"
    api = "api"
    agent = "agent"


class CaseToolStatus(StrEnum):
    pending = "pending"
    branching = "branching"
    applying = "applying"
    pr_open = "pr_open"
    polling = "polling"
    scraping = "scraping"
    closing = "closing"
    submitting = "submitting"
    collecting = "collecting"
    preparing = "preparing"
    cloning = "cloning"
    running = "running"
    done = "done"
    failed = "failed"


class CaseToolState(BaseModel):
    """State for a single (case, tool) pair."""

    case_id: str
    tool: str
    status: CaseToolStatus = CaseToolStatus.pending
    pr_number: int | None = None
    branch_name: str | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RunState(BaseModel):
    """Checkpoint state for a full evaluation run."""

    pairs: dict[str, CaseToolState] = Field(default_factory=dict)

    def _key(self, case_id: str, tool: str, context_level: str = "") -> str:
        if context_level:
            return f"{case_id}::{tool}::{context_level}"
        return f"{case_id}::{tool}"

    def get(self, case_id: str, tool: str, context_level: str = "") -> CaseToolState:
        """Get state for a (case, tool[, context_level]) pair."""
        key = self._key(case_id, tool, context_level)
        if key not in self.pairs:
            # Fall back to old-format key for backward compatibility with existing checkpoints.
            old_key = f"{case_id}::{tool}"
            if context_level and old_key in self.pairs:
                return self.pairs[old_key]
            return CaseToolState(case_id=case_id, tool=tool)
        return self.pairs[key]

    def set(self, state: CaseToolState, context_level: str = "") -> None:
        """Set state for a (case, tool[, context_level]) pair."""
        key = self._key(state.case_id, state.tool, context_level)
        self.pairs[key] = state

    def states(self) -> list[CaseToolState]:
        """Return all stored states."""
        return list(self.pairs.values())

    def save(self, path: Path) -> None:
        """Save checkpoint to YAML."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(mode="json"), f, sort_keys=False)

    @classmethod
    def load(cls, path: Path) -> RunState:
        """Load checkpoint from YAML. Returns empty state if file doesn't exist."""
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


class ToolDef(BaseModel):
    """Definition of an evaluation tool from config.yaml."""

    name: str
    type: ToolType
    github_app: str | None = None
    cooldown_seconds: int = 30
    api_endpoint: str | None = None
    api_key_env: str | None = None
    reviewer: str | None = None
    model: str | None = None
    timeout_seconds: int = 600
    fresh_repo: bool = False

    @property
    def is_pr_tool(self) -> bool:
        """True if this tool operates as a GitHub PR review app."""
        return self.type == ToolType.pr

    @property
    def is_api_tool(self) -> bool:
        """True if this tool operates via REST API."""
        return self.type == ToolType.api

    @property
    def is_agent_tool(self) -> bool:
        """True if this tool is an in-house agent (CLI or API loop)."""
        return self.type == ToolType.agent


class ScoringConfig(BaseModel):
    """Scoring scale, labels, and catch threshold from config.yaml."""

    scale: list[int] = Field(default_factory=lambda: [0, 1, 2, 3])
    labels: dict[int, str] = Field(
        default_factory=lambda: {
            0: "missed",
            1: "wrong-area",
            2: "correct-id",
            3: "correct-id-and-fix",
        }
    )
    catch_threshold: int = 2
    severity_weights: dict[str, int] = Field(
        default_factory=lambda: {"critical": 4, "high": 3, "medium": 2, "low": 1}
    )
    actionability_weights: dict[str, float] = Field(
        default_factory=lambda: {"actionable": 1.0, "directional": 0.6, "vague": 0.3}
    )


def default_scoring() -> ScoringConfig:
    """Return default ScoringConfig matching the 0–3 rubric."""
    return ScoringConfig()


class JudgingConfig(BaseModel):
    """LLM judge parameters from config.yaml."""

    llm_calls: int = 3
    human_sample_rate: float = 0.25
    calibration_threshold: float = 0.85
    model: str = "claude-opus-4-6"
    models: list[str] = []  # if non-empty, overrides model+llm_calls for ensemble voting


def default_judging() -> JudgingConfig:
    """Return JudgingConfig from config/config.yaml, falling back to defaults."""
    config_path = Path("config") / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        raw = data.get("judging")
        if raw:
            return JudgingConfig(**raw)
    return JudgingConfig()


class PricingConfig(BaseModel):
    """Per-model token pricing (USD per million tokens)."""

    rates: dict[str, tuple[float, float]] = Field(default_factory=dict)

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Return estimated cost in USD given model and token counts."""
        rate = self.rates.get(model, (0.0, 0.0))
        return (input_tokens * rate[0] + output_tokens * rate[1]) / 1_000_000


def default_pricing() -> PricingConfig:
    """Return default PricingConfig matching config.yaml pricing section."""
    return PricingConfig(
        rates={
            "claude-sonnet-4-6": (3.0, 15.0),
            "claude-haiku-4-5": (0.80, 4.0),
            "claude-opus-4-6": (15.0, 75.0),
            "gemini-2.5-flash-lite": (0.0, 0.0),
            "gemini-2.5-flash": (0.15, 0.60),
            "gemini-2.5-pro": (1.25, 10.0),
            "gpt-4.1-mini": (0.40, 1.60),
            "o4-mini": (1.10, 4.40),
            "gpt-5.4-mini": (0.40, 1.60),
            "gpt-5.4": (2.00, 8.00),
            "gpt-5.3-codex": (2.00, 8.00),
        }
    )


class EvalConfig(BaseModel):
    """Top-level evaluation configuration."""

    eval_org: str
    tools: list[ToolDef]
    repos: dict[str, str] = Field(default_factory=dict)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    judging: JudgingConfig = Field(default_factory=JudgingConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    max_concurrent: int = 1

    @property
    def pr_tools(self) -> list[ToolDef]:
        """Return only tools that operate via PR review."""
        return [t for t in self.tools if t.is_pr_tool]

    @property
    def api_tools(self) -> list[ToolDef]:
        """Return only tools that operate via REST API."""
        return [t for t in self.tools if t.is_api_tool]

    @property
    def agent_tools(self) -> list[ToolDef]:
        """Return only in-house agent tools."""
        return [t for t in self.tools if t.is_agent_tool]


def is_case_done(run_dir: Path, case_id: str, tool: str, context_level: str = "") -> bool:
    """Check if a (case, tool, context_level) result already exists.

    For agent/API tools: done if metadata.json exists in the raw dir.
    For PR tools (no context_level): done if comments.json exists in the raw dir.
    Cases with error.json (but no metadata.json/comments.json) are treated as not done
    so they get retried on resume.
    """
    suffix = f"-{context_level}" if context_level else ""
    raw_dir = run_dir / "raw" / f"{case_id}-{tool}{suffix}"
    if context_level:
        return (raw_dir / "metadata.json").exists()
    # PR tools write comments.json; agent/API tools write metadata.json
    return (raw_dir / "comments.json").exists() or (raw_dir / "metadata.json").exists()


def write_error_marker(
    run_dir: Path, case_id: str, tool: str, error: str, context_level: str = ""
) -> None:
    """Write an error.json marker so failed cases are distinguishable from not-started."""
    suffix = f"-{context_level}" if context_level else ""
    raw_dir = run_dir / "raw" / f"{case_id}-{tool}{suffix}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "error.json").write_text(json.dumps({"error": error}))


def parse_case_ids(value: str) -> list[str]:
    """Parse a --case-ids value into a list of case IDs.

    Accepts either:
      - Comma-separated IDs: "leo-001,leo-002,snarkVM-001"
      - A file path prefixed with @: "@pilot-step1.txt" (one ID per line, # comments)
    """
    if value.startswith("@"):
        file_path = Path(value[1:])
        ids: list[str] = []
        for line in file_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ids.append(line)
        return ids
    return [cid.strip() for cid in value.split(",") if cid.strip()]


def load_eval_config(path: Path) -> EvalConfig:
    """Parse config.yaml into an EvalConfig. Raises ValueError on missing fields."""
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    github = data.get("github") or {}
    eval_org = str(github.get("eval_org") or "")

    raw_tools = data.get("tools") or []
    tools = [ToolDef(**t) for t in raw_tools]

    repos = data.get("repos") or {}

    raw_scoring = data.get("scoring")
    scoring = ScoringConfig(**raw_scoring) if raw_scoring else ScoringConfig()

    raw_judging = data.get("judging")
    judging = JudgingConfig(**raw_judging) if raw_judging else JudgingConfig()

    raw_pricing = data.get("pricing")
    pricing = PricingConfig(rates=raw_pricing) if raw_pricing else PricingConfig()

    max_concurrent = int(data.get("max_concurrent") or 1)

    return EvalConfig(
        eval_org=eval_org,
        tools=tools,
        repos=repos,
        scoring=scoring,
        judging=judging,
        pricing=pricing,
        max_concurrent=max_concurrent,
    )
