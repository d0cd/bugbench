"""Pydantic models for PR-mode evaluation state and configuration."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ToolType(StrEnum):
    pr = "pr"
    api = "api"
    cli = "cli"
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

    def _key(self, case_id: str, tool: str) -> str:
        return f"{case_id}::{tool}"

    def get(self, case_id: str, tool: str) -> CaseToolState:
        """Get state for a (case, tool) pair. Returns pending state if not found."""
        key = self._key(case_id, tool)
        if key not in self.pairs:
            return CaseToolState(case_id=case_id, tool=tool)
        return self.pairs[key]

    def set(self, state: CaseToolState) -> None:
        """Set state for a (case, tool) pair."""
        key = self._key(state.case_id, state.tool)
        self.pairs[key] = state

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
    org: str | None = None
    cooldown_seconds: int = 30
    api_endpoint: str | None = None
    api_key_env: str | None = None

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


class EvalConfig(BaseModel):
    """Top-level evaluation configuration."""

    eval_org: str
    tools: list[ToolDef]
    repos: dict[str, str] = Field(default_factory=dict)

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


def load_eval_config(path: Path) -> EvalConfig:
    """Parse config.yaml into an EvalConfig. Raises ValueError on missing fields."""
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    github = data.get("github") or {}
    eval_org = str(github.get("eval_org") or "")

    raw_tools = data.get("tools") or []
    tools = [ToolDef(**t) for t in raw_tools]

    repos = data.get("repos") or {}

    return EvalConfig(eval_org=eval_org, tools=tools, repos=repos)
