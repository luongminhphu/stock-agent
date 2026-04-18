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
        "verdict": "BULLISH",
        "confidence": 0.75,
        "risk_signals": ["Margin pressure Q1"],
        "next_watch_items": ["Q2 earnings"],
        "reasoning": "Strong export growth offsets domestic weakness.",
        "assumption_updates": [],
        "catalyst_status": ["Export order confirmed"],
    }
    out = ThesisReviewOutput.model_validate(data)
    assert out.verdict == Verdict.BULLISH
    assert out.confidence == 0.75


def test_thesis_review_output_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        ThesisReviewOutput.model_validate(
            {"verdict": "BULLISH", "confidence": 1.5, "reasoning": "x"}
        )


def test_thesis_review_output_risk_signals_string_coerced() -> None:
    """Single string should be coerced to a list."""
    data = {
        "verdict": "NEUTRAL",
        "confidence": 0.5,
        "reasoning": "Mixed signals.",
        "risk_signals": "High debt",  # string, not list
    }
    out = ThesisReviewOutput.model_validate(data)
    assert out.risk_signals == ["High debt"]


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
