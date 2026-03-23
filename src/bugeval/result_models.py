"""Tool result models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Comment(BaseModel):
    file: str = ""
    line: int = 0
    body: str = ""
    suggested_fix: str = ""


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    tool: str
    context_level: str = ""
    comments: list[Comment] = []
    time_seconds: float = 0.0
    cost_usd: float = 0.0
    error: str = ""
    transcript_path: str = ""
    pr_number: int = 0
    pr_state: str = ""
    pr_head_branch: str = ""
    pr_base_branch: str = ""
    potentially_contaminated: bool = False
