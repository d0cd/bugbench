"""Pydantic models for normalized tool evaluation results."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class CommentType(StrEnum):
    inline = "inline"
    pr_level = "pr-level"
    summary = "summary"


class Comment(BaseModel):
    """A single comment or finding from a tool review."""

    file: str = ""
    line: int = 0
    body: str
    type: CommentType = CommentType.inline


class ResultMetadata(BaseModel):
    """Execution metadata for a tool review."""

    tokens: int = 0
    cost_usd: float = 0.0
    time_seconds: float = 0.0


class NormalizedResult(BaseModel):
    """Common output schema for all tool evaluation modes."""

    test_case_id: str
    tool: str
    context_level: str = ""
    comments: list[Comment] = []
    metadata: ResultMetadata = ResultMetadata()
