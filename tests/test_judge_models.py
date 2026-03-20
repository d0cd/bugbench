# tests/test_judge_models.py
import pytest

from bugeval.judge_models import CommentClassification, CommentJudgment, JudgeScore, NoiseStats


def test_judge_score_defaults() -> None:
    s = JudgeScore(test_case_id="x", tool="y", score=2, votes=[2, 2, 3], reasoning="ok")
    assert s.comment_judgments == []
    assert s.noise.total_comments == 0
    assert s.noise.snr == 0.0


def test_judge_score_model_dump_round_trip() -> None:
    s = JudgeScore(
        test_case_id="case-001",
        tool="greptile",
        score=3,
        votes=[3, 3, 2],
        reasoning="Correct ID and fix",
        comment_judgments=[
            CommentJudgment(
                id=0,
                classification=CommentClassification.tp_expected,
                relevance="direct",
            )
        ],
        noise=NoiseStats(total_comments=4, true_positives=1, snr=0.25),
    )
    data = s.model_dump(mode="json")
    restored = JudgeScore(**data)
    assert restored.score == 3
    assert restored.noise.snr == 0.25
    assert restored.comment_judgments[0].classification == "TP-expected"


def test_majority_vote() -> None:
    from bugeval.judge_models import majority_vote

    assert majority_vote([2, 2, 3]) == 2
    assert majority_vote([3, 3, 3]) == 3
    assert majority_vote([0, 1, 2]) == 1  # fallback: median (middle value sorted)


def test_comment_classification_tp_expected() -> None:
    assert CommentClassification.tp_expected.value == "TP-expected"


def test_comment_classification_tp_novel() -> None:
    assert CommentClassification.tp_novel.value == "TP-novel"


def test_comment_classification_backward_compat() -> None:
    assert CommentClassification("TP") == CommentClassification.tp_expected


def test_judge_score_review_quality_backward_compat() -> None:
    """Old score files with review_quality still load (deprecated fields default)."""
    s = JudgeScore(
        test_case_id="x",
        tool="y",
        score=2,
        votes=[2],
        reasoning="ok",
        review_quality=3,
        review_quality_votes=[3, 3, 4],
    )
    assert s.review_quality == 3
    assert s.review_quality_votes == [3, 3, 4]


def test_judge_score_review_quality_defaults() -> None:
    s = JudgeScore(test_case_id="x", tool="y", score=2, votes=[2], reasoning="ok")
    assert s.review_quality == 0
    assert s.review_quality_votes == []


def test_comment_judgment_severity_actionability() -> None:
    j = CommentJudgment(
        id=0,
        classification=CommentClassification.tp_expected,
        severity="high",
        actionability="actionable",
        relevance="direct",
    )
    assert j.severity == "high"
    assert j.actionability == "actionable"


def test_comment_judgment_severity_none_for_fp() -> None:
    j = CommentJudgment(
        id=0,
        classification=CommentClassification.fp,
        relevance="unrelated",
    )
    assert j.severity is None
    assert j.actionability is None


def test_noise_stats_weighted_signal() -> None:
    ns = NoiseStats(
        total_comments=3,
        true_positives=1,
        novel_findings=1,
        false_positives=0,
        low_value=1,
        weighted_signal=4.2,
        actionability_rate=0.5,
    )
    assert ns.weighted_signal == pytest.approx(4.2)
    assert ns.actionability_rate == pytest.approx(0.5)


def test_noise_stats_quality_adjusted_precision() -> None:
    ns = NoiseStats(total_comments=3, weighted_signal=4.2)
    assert ns.quality_adjusted_precision == pytest.approx(1.4)


def test_noise_stats_quality_adjusted_precision_empty() -> None:
    ns = NoiseStats()
    assert ns.quality_adjusted_precision == 0.0


def test_noise_stats_noise_ratio() -> None:
    ns = NoiseStats(total_comments=4, false_positives=1, low_value=1)
    assert ns.noise_ratio == pytest.approx(0.5)


def test_noise_stats_noise_ratio_empty() -> None:
    ns = NoiseStats()
    assert ns.noise_ratio == 0.0


def test_noise_stats_novel_findings() -> None:
    ns = NoiseStats(
        total_comments=10,
        true_positives=6,
        novel_findings=2,
        false_positives=1,
        low_value=1,
    )
    assert ns.novel_findings == 2
    assert ns.false_positives == 1
    assert ns.low_value == 1


def test_noise_stats_precision() -> None:
    ns = NoiseStats(
        total_comments=10,
        true_positives=6,
        novel_findings=2,
        false_positives=1,
        low_value=1,
    )
    assert ns.precision == pytest.approx(0.8)


def test_noise_stats_precision_empty() -> None:
    ns = NoiseStats()
    assert ns.precision == 0.0


def test_comment_classification_uncertain() -> None:
    assert CommentClassification.uncertain.value == "uncertain"


def test_uncertain_excluded_from_snr() -> None:
    """uncertain comments should be excluded from both SNR numerator and denominator."""
    ns = NoiseStats(
        total_comments=5,
        true_positives=2,
        novel_findings=1,
        false_positives=1,
        low_value=0,
        uncertain=1,
    )
    # SNR denominator = total - uncertain = 4; numerator = tp + novel = 3
    assert ns.snr_excluding_uncertain == pytest.approx(3 / 4)
