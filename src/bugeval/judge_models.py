# src/bugeval/judge_models.py
"""Pydantic models for LLM judge results."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum

from pydantic import BaseModel


class CommentClassification(StrEnum):
    tp_expected = "TP-expected"
    tp_novel = "TP-novel"
    fp = "FP"
    low_value = "low-value"
    uncertain = "uncertain"

    @classmethod
    def _missing_(cls, value: object) -> CommentClassification | None:
        if value == "TP":
            return cls.tp_expected
        return None


class CommentJudgment(BaseModel):
    """Judge's classification of a single tool comment."""

    id: int
    classification: CommentClassification
    severity: str | None = None  # "critical"|"high"|"medium"|"low" (TP only)
    actionability: str | None = None  # "actionable"|"directional"|"vague" (TP only)
    relevance: str = ""  # "direct" | "adjacent" | "unrelated"


class NoiseStats(BaseModel):
    """Noise/SNR statistics derived from comment judgments."""

    total_comments: int = 0
    true_positives: int = 0
    novel_findings: int = 0
    false_positives: int = 0
    low_value: int = 0
    uncertain: int = 0
    snr: float = 0.0  # (tp + novel) / total
    weighted_signal: float = 0.0  # Σ severity_weight × actionability_weight for TPs
    actionability_rate: float = 0.0  # count(actionable) / count(all TPs)

    @property
    def precision(self) -> float:
        """Return (true_positives + novel_findings) / total_comments, or 0.0."""
        if self.total_comments == 0:
            return 0.0
        return (self.true_positives + self.novel_findings) / self.total_comments

    @property
    def snr_excluding_uncertain(self) -> float:
        """SNR with uncertain comments excluded from both numerator and denominator."""
        denom = self.total_comments - self.uncertain
        if denom <= 0:
            return 0.0
        return (self.true_positives + self.novel_findings) / denom

    @property
    def quality_adjusted_precision(self) -> float:
        """weighted_signal / total_comments."""
        if self.total_comments == 0:
            return 0.0
        return self.weighted_signal / self.total_comments

    @property
    def noise_ratio(self) -> float:
        """(FP + low_value) / total_comments."""
        if self.total_comments == 0:
            return 0.0
        return (self.false_positives + self.low_value) / self.total_comments


class JudgeScore(BaseModel):
    """LLM judge output for one (case x tool) pair."""

    test_case_id: str
    tool: str
    score: int  # 0–3
    votes: list[int]
    reasoning: str
    comment_judgments: list[CommentJudgment] = []
    noise: NoiseStats = NoiseStats()
    vote_agreement: float = 0.0  # fraction of votes matching the majority
    review_quality: int = 0  # DEPRECATED: kept for old score file compat
    review_quality_votes: list[int] = []  # DEPRECATED


def majority_vote(votes: list[int]) -> int:
    """Return the most common vote. On tie: return the median value."""
    if not votes:
        return 0
    counter = Counter(votes)
    max_count = max(counter.values())
    candidates = sorted(v for v, c in counter.items() if c == max_count)
    return candidates[len(candidates) // 2]
