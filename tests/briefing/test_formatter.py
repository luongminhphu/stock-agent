"""Unit tests for briefing.formatter.

Pure string tests — no DB, no HTTP.
"""
from __future__ import annotations

from src.ai.schemas import BriefOutput, MarketSentiment
from src.briefing.formatter import format_brief, format_eod_brief, format_morning_brief


def _brief(sentiment: MarketSentiment = MarketSentiment.MIXED) -> BriefOutput:
    return BriefOutput(
        headline="VN-Index +0.5% trong phiên sáng",
        sentiment=sentiment,
        summary="Thị trường giao dịch thận trọng. Nhóm ngân hàng dẫn dắt.",
        key_movers=["VCB", "TCB"],
        watchlist_alerts=["HPG vượt MA20"],
        action_items=["Review HPG thesis"],
    )


def test_format_brief_contains_headline():
    text = format_brief(_brief())
    assert "VN-Index" in text


def test_format_brief_contains_summary():
    text = format_brief(_brief())
    assert "Nhóm ngân hàng" in text


def test_format_brief_contains_key_movers():
    text = format_brief(_brief())
    assert "VCB" in text
    assert "TCB" in text


def test_format_brief_contains_watchlist_section():
    text = format_brief(_brief())
    assert "Watchlist" in text
    assert "HPG" in text


def test_format_brief_contains_action_items():
    text = format_brief(_brief())
    assert "Action Items" in text or "action" in text.lower()
    assert "Review HPG" in text


def test_format_brief_risk_on_emoji():
    text = format_brief(_brief(MarketSentiment.RISK_ON))
    assert "🟢" in text


def test_format_brief_risk_off_emoji():
    text = format_brief(_brief(MarketSentiment.RISK_OFF))
    assert "🔴" in text


def test_format_brief_uncertain_emoji():
    text = format_brief(_brief(MarketSentiment.UNCERTAIN))
    assert "⚪" in text


def test_format_morning_brief_label():
    text = format_morning_brief(_brief())
    assert "Morning Brief" in text


def test_format_eod_brief_label():
    text = format_eod_brief(_brief())
    assert "EOD Brief" in text


def test_format_brief_empty_sections():
    brief = BriefOutput(
        headline="Flat day",
        sentiment=MarketSentiment.MIXED,
        summary="Không có gì đặc biệt.",
    )
    text = format_brief(brief)
    assert "Flat day" in text
    # Empty sections should not produce empty bullet blocks
    assert "\u2022" not in text
