"""Unit tests for BriefingService — current contract (Wave 12.1 rewrite).

Architecture today:
- BriefingService.generate_morning_brief/generate_eod_brief return BriefResult
  (snapshot_id, text, tickers, output) — the old direct-BriefOutput return
  was replaced in Wave interactions refresh.
- DECIDE → ACT_TODAY enforcement migrated from service code into the AI
  prompt pack (`src/ai/prompts/brief.py`). Service no longer mutates
  prioritized_actions; that guarantee is now contract-enforced via schema
  validators + prompt instructions. Therefore the legacy
  `test_enforce_agenda_mapping_*` tests asserting post-brief Python
  mutation have been dropped here; the remaining PromptEnforcementTest
  shape is that the service forwards cached agenda into the AI prompt via
  the `agenda_context` kwarg — covered by the agenda-context tests below.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.schemas import BriefOutput, MarketSentiment
from src.briefing.agenda_cache import AgendaBuckets, _AgendaCache, set_agenda
from src.briefing.service import BriefingService, BriefResult


@pytest.fixture(autouse=True)
def clear_agenda_cache() -> None:
    # In-memory cache is process-global; reset per test to avoid leaking
    # DECIDE buckets across cases within the same pytest worker.
    _AgendaCache.clear()


@pytest.fixture
def sample_brief() -> BriefOutput:
    return BriefOutput(
        headline="Dòng tiền thăm dò trở lại nhóm thép",
        sentiment=MarketSentiment.MIXED,
        summary="Thị trường giằng co nhưng một số mã trong watchlist có tín hiệu hồi phục.",
        key_movers=["HPG +2.1%", "FPT -1.3%"],
        watchlist_alerts=["HPG vượt MA20 intraday"],
        action_items=["Theo dõi thêm thanh khoản HPG phiên chiều"],
    )


def _make_svc(
    *,
    session,
    watchlist_items,
    quotes=None,
    sample_brief: BriefOutput,
    agent_method: str = "morning_brief",
) -> tuple[BriefingService, AsyncMock, AsyncMock]:
    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = watchlist_items
    watchlist_service.get_tickers.return_value = [it.ticker for it in watchlist_items]

    quote_service = AsyncMock()
    if quotes is not None:
        quote_service.get_bulk_quotes.return_value = quotes
    else:
        quote_service.get_bulk_quotes.side_effect = RuntimeError("market unavailable")

    agent = AsyncMock()
    getattr(agent, agent_method).return_value = sample_brief

    svc = BriefingService(
        session=session,
        briefing_agent=agent,
        watchlist_service=watchlist_service,
        quote_service=quote_service,
    )
    return svc, agent, quote_service


async def test_generate_morning_brief_success(sample_brief: BriefOutput, session) -> None:
    quotes = [
        SimpleNamespace(ticker="HPG", price=28000, change=500, change_pct=1.82, volume=12000000),
        SimpleNamespace(ticker="FPT", price=118000, change=-1500, change_pct=-1.26, volume=2300000),
    ]
    svc, agent, quote_service = _make_svc(
        session=session,
        watchlist_items=[SimpleNamespace(ticker="HPG"), SimpleNamespace(ticker="FPT")],
        quotes=quotes,
        sample_brief=sample_brief,
    )

    result = await svc.generate_morning_brief(user_id="u1")

    assert isinstance(result, BriefResult)
    assert result.tickers == ["HPG", "FPT"]
    assert result.output is sample_brief
    quote_service.get_bulk_quotes.assert_called_once_with(["HPG", "FPT"])
    agent.morning_brief.assert_called_once()
    market_context = agent.morning_brief.call_args.kwargs["market_context"]
    assert "HPG" in market_context
    assert "FPT" in market_context


async def test_generate_eod_brief_with_empty_watchlist(sample_brief: BriefOutput, session) -> None:
    svc, agent, quote_service = _make_svc(
        session=session,
        watchlist_items=[],
        quotes=[],
        sample_brief=sample_brief,
        agent_method="eod_brief",
    )

    result = await svc.generate_eod_brief(user_id="u1")

    assert result.tickers == []
    assert result.output is sample_brief
    quote_service.get_bulk_quotes.assert_not_called()
    agent.eod_brief.assert_called_once()


async def test_generate_brief_quote_failure_falls_back(sample_brief: BriefOutput, session) -> None:
    svc, agent, _ = _make_svc(
        session=session,
        watchlist_items=[SimpleNamespace(ticker="VCB")],
        quotes=None,  # triggers side_effect in helper
        sample_brief=sample_brief,
    )

    # Quote service raising must NOT block the brief — service swallow the
    # error and proceeds with empty quote list (current soft-fail contract).
    result = await svc.generate_morning_brief(user_id="u1")

    assert result.output is sample_brief
    agent.morning_brief.assert_called_once()
    market_context = agent.morning_brief.call_args.kwargs["market_context"]
    # When quotes list is empty, the market context block has no "Giá hiện tại" section.
    assert "Giá hiện tại" not in market_context


async def test_agenda_decide_bucket_forwarded_to_agent(sample_brief: BriefOutput, session) -> None:
    """DECIDE tickers cached earlier in the day must reach the AI prompt
    via the `agenda_context` kwarg so the prompt pack can enforce the
    'every DECIDE gets one ACT_TODAY' contract at LLM level."""
    set_agenda(
        "u1",
        summary="Daily Agenda:\nDECIDE (1): HPG",
        buckets=AgendaBuckets(decide=["HPG"], watch=[], defer=[]),
    )
    svc, agent, _ = _make_svc(
        session=session,
        watchlist_items=[SimpleNamespace(ticker="HPG")],
        quotes=[],
        sample_brief=sample_brief,
    )

    await svc.generate_morning_brief(user_id="u1")

    agenda_context = agent.morning_brief.call_args.kwargs.get("agenda_context", "")
    assert "DECIDE" in agenda_context
    assert "HPG" in agenda_context


async def test_agenda_empty_or_missing_sends_no_decide(sample_brief: BriefOutput, session) -> None:
    """When no agenda was cached today, agenda_context is empty string —
    the prompt receives no DECIDE instructions (assistant should then
    fall back to plain watchlist-driven analysis)."""
    svc, agent, _ = _make_svc(
        session=session,
        watchlist_items=[SimpleNamespace(ticker="HPG")],
        quotes=[],
        sample_brief=sample_brief,
    )

    await svc.generate_morning_brief(user_id="u1")

    agenda_context = agent.morning_brief.call_args.kwargs.get("agenda_context", "")
    assert "DECIDE" not in agenda_context


# ── Wave D4b: thesis context đọc health thật từ thesis.health_snapshot ─────────


def test_format_thesis_health_line_full() -> None:
    from src.briefing.service import _format_thesis_health_line

    line = _format_thesis_health_line(
        {
            "ticker": "HPG",
            "status": "AT_RISK",
            "health_score": 0.42,
            "last_verdict": "WEAKENING",
            "distance_to_stop_pct": 1.7,
            "stop_distance_atr": 0.8,
            "near_stop": True,
            "stop_proximity": "NEAR",
            "assumption_count": 3,
            "assumptions_invalidated": 1,
            "days_since_review": 2,
            "price_quality": "stale",
        }
    )
    assert line == (
        "HPG [AT_RISK] | health=0.42 | verdict=WEAKENING | cách stop 1.7% (0.8 ATR) SÁT STOP"
        " | giả định 2/3 valid | review 2d trước | dữ liệu giá cũ"
    )


def test_format_thesis_health_line_minimal_and_breached() -> None:
    from src.briefing.service import _format_thesis_health_line

    assert _format_thesis_health_line({"ticker": "SSI", "status": "OK"}) == (
        "SSI [OK] | chưa review"
    )
    line = _format_thesis_health_line(
        {"ticker": "VNM", "status": "AT_RISK", "stop_proximity": "BREACHED", "days_since_review": 0}
    )
    assert "stop ĐÃ XUYÊN" in line and "cách stop" not in line


@pytest.mark.anyio
async def test_build_thesis_context_passes_quote_service() -> None:
    svc = BriefingService.__new__(BriefingService)
    svc._quote_service = object()
    svc._thesis_service = SimpleNamespace(
        get_thesis_health=AsyncMock(
            return_value=[
                {
                    "ticker": "HPG",
                    "status": "REVIEW_DUE",
                    "health_score": 0.7,
                    "days_since_review": 9,
                }
            ]
        )
    )

    text = await svc._build_thesis_context("u1")

    svc._thesis_service.get_thesis_health.assert_awaited_once_with(
        "u1", quote_service=svc._quote_service
    )
    assert text == "HPG [REVIEW_DUE] | health=0.70 | review 9d trước"


@pytest.mark.anyio
async def test_build_thesis_context_swallows_errors() -> None:
    svc = BriefingService.__new__(BriefingService)
    svc._quote_service = None
    svc._thesis_service = SimpleNamespace(
        get_thesis_health=AsyncMock(side_effect=RuntimeError("x"))
    )
    assert await svc._build_thesis_context("u1") == ""
