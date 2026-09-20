import pytest
from pydantic import ValidationError

from src.ai.schemas import (
    BriefOutput,
    MarketSentiment,
    RiskLevel,
    StockAnalysisOutput,
    ThesisReviewOutput,
    Verdict,
)


def test_thesis_review_output_valid() -> None:
    data = {
        "overall_verdict": "BULLISH",
        "conviction_score": 0.8,
        "confidence": 0.75,
        "key_risks": ["Margin pressure Q1"],
        "action_recommendation": "HOLD",
        "summary": "Strong export growth offsets domestic weakness.",
    }
    out = ThesisReviewOutput.model_validate(data)
    assert out.overall_verdict == Verdict.BULLISH
    assert out.confidence == 0.75
    assert out.conviction_score == 0.8


def test_thesis_review_output_legacy_aliases() -> None:
    """verdict/reasoning/risk_signals (schema cũ) vẫn được map sang field mới."""
    out = ThesisReviewOutput.model_validate(
        {
            "verdict": "bullish",
            "confidence": 0.75,
            "reasoning": "Legacy reasoning.",
            "risk_signals": ["Margin pressure Q1"],
        }
    )
    assert out.overall_verdict == Verdict.BULLISH
    assert out.conviction_score == 0.75  # falls back to confidence
    assert out.summary == "Legacy reasoning."
    assert out.key_risks == ["Margin pressure Q1"]
    assert out.action_recommendation == "HOLD"


def test_thesis_review_output_confidence_clamped() -> None:
    """Out-of-range/garbage confidence is clamped, not rejected (LLM output is noisy)."""
    out = ThesisReviewOutput.model_validate(
        {"overall_verdict": "BULLISH", "confidence": 1.5, "summary": "x"}
    )
    assert out.confidence == 1.0
    assert out.conviction_score == 1.0
    out = ThesisReviewOutput.model_validate(
        {"overall_verdict": "BULLISH", "confidence": "n/a", "summary": "x"}
    )
    assert out.confidence == 0.5


def test_thesis_review_output_invalid_verdict_raises() -> None:
    with pytest.raises(ValidationError):
        ThesisReviewOutput.model_validate(
            {"overall_verdict": "MOON", "confidence": 0.5, "summary": "x"}
        )


def test_thesis_review_output_key_risks_string_coerced() -> None:
    """Single string should be coerced to a list."""
    data = {
        "overall_verdict": "NEUTRAL",
        "confidence": 0.5,
        "summary": "Mixed signals.",
        "key_risks": "High debt",  # string, not list
    }
    out = ThesisReviewOutput.model_validate(data)
    assert out.key_risks == ["High debt"]


def test_stock_analysis_output_valid() -> None:
    data = {
        "ticker": "VNM",
        "verdict": "NEUTRAL",
        "confidence": 0.6,
        "risk_level": "MEDIUM",
        "summary": "Dairy sector headwinds persist.",
    }
    out = StockAnalysisOutput.model_validate(data)
    assert out.ticker == "VNM"
    assert out.risk_level == RiskLevel.MEDIUM


def test_brief_output_valid() -> None:
    data = {
        "headline": "VN-Index giảm nhẹ sau áp lực chốt lời",
        "sentiment": "MIXED",
        "summary": "Thị trường rung lắc nhẹ trong phiên sáng.",
    }
    out = BriefOutput.model_validate(data)
    assert out.sentiment == MarketSentiment.MIXED
