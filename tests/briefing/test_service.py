"""Unit tests for BriefingService."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.ai.schemas import BriefOutput, MarketSentiment
from src.ai.schemas.briefing import ActionPriority, PrioritizedAction
from src.briefing.agenda_cache import AgendaBuckets, CachedAgenda, _AGENDA_CACHE
from src.briefing.service import BriefingService


@pytest.fixture(autouse=True)
def clear_agenda_cache() -> None:
    """Ensure a clean agenda cache between tests.

    The in-memory _AGENDA_CACHE is process-global, so tests must clear it
    to avoid leaking DECIDE buckets across cases.
    """
    _AGENDA_CACHE.clear()


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


@pytest.mark.asyncio
async def test_generate_morning_brief_success(sample_brief: BriefOutput) -> None:
    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = [
        SimpleNamespace(ticker="HPG"),
        SimpleNamespace(ticker="FPT"),
    ]

    quote_service = AsyncMock()
    quote_service.get_bulk_quotes.return_value = [
        SimpleNamespace(ticker="HPG", price=28000, change=500, change_pct=1.82, volume=12000000),
        SimpleNamespace(ticker="FPT", price=118000, change=-1500, change_pct=-1.26, volume=2300000),
    ]

    agent = AsyncMock()
    agent.morning_brief.return_value = sample_brief

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_morning_brief(user_id="u1")

    assert result.headline == sample_brief.headline
    watchlist_service.list_items.assert_called_once_with(user_id="u1")
    quote_service.get_bulk_quotes.assert_called_once_with(["HPG", "FPT"])
    agent.morning_brief.assert_called_once()
    context = agent.morning_brief.call_args.kwargs["market_context"]
    assert "HPG" in context
    assert "FPT" in context


@pytest.mark.asyncio
async def test_generate_eod_brief_with_empty_watchlist(sample_brief: BriefOutput) -> None:
    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = []

    quote_service = AsyncMock()
    agent = AsyncMock()
    agent.eod_brief.return_value = sample_brief

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_eod_brief(user_id="u1")

    assert result.summary == sample_brief.summary
    quote_service.get_bulk_quotes.assert_not_called()
    context = agent.eod_brief.call_args.kwargs["market_context"]
    assert "watchlist" in context.lower()


@pytest.mark.asyncio
async def test_generate_brief_quote_failure_falls_back(sample_brief: BriefOutput) -> None:
    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = [SimpleNamespace(ticker="VCB")]

    quote_service = AsyncMock()
    quote_service.get_bulk_quotes.side_effect = RuntimeError("market unavailable")

    agent = AsyncMock()
    agent.morning_brief.return_value = sample_brief

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_morning_brief(user_id="u1")

    assert result.key_movers == sample_brief.key_movers
    context = agent.morning_brief.call_args.kwargs["market_context"]
    assert "thiếu dữ liệu" in context.lower() or "không lấy được quote" in context.lower()


@pytest.mark.asyncio
async def test_enforce_agenda_mapping_adds_act_today_when_decide_missing_action(
    sample_brief: BriefOutput,
) -> None:
    """DECIDE with no existing action → appended ACT_TODAY action.

    This locks the guarantee that mỗi ticker trong DECIDE bucket sẽ có ít
    nhất một PrioritizedAction.ACT_TODAY, ngay cả khi BriefingAgent không
    tạo ra action nào cho mã đó.
    """
    # Arrange agenda cache with DECIDE=["HPG"].
    _AGENDA_CACHE["u1"] = CachedAgenda(
        summary="Daily Agenda:\nDECIDE (1): HPG",
        buckets=AgendaBuckets(decide=["HPG"], watch=[], defer=[]),
    )

    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = [SimpleNamespace(ticker="HPG")]

    quote_service = AsyncMock()
    quote_service.get_bulk_quotes.return_value = []

    # Agent returns a brief with no prioritized_actions at all.
    brief_without_actions = sample_brief.model_copy(update={"prioritized_actions": []})
    agent = AsyncMock()
    agent.morning_brief.return_value = brief_without_actions

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_morning_brief(user_id="u1")

    # One ACT_TODAY action for HPG must be present.
    actions = result.output.prioritized_actions
    assert len(actions) == 1
    action = actions[0]
    assert action.ticker == "HPG"
    assert action.priority == ActionPriority.ACT_TODAY
    assert action.confidence >= 0.7


@pytest.mark.asyncio
async def test_enforce_agenda_mapping_upgrades_watch_more_to_act_today(
    sample_brief: BriefOutput,
) -> None:
    """DECIDE with existing WATCH_MORE → upgraded to ACT_TODAY in-place.

    This ensures hệ thống không sinh thêm action trùng ticker mà nâng cấp
    đúng PrioritizedAction hiện có, giữ one-source-of-truth cho ticker đó.
    """
    _AGENDA_CACHE["u2"] = CachedAgenda(
        summary="Daily Agenda:\nDECIDE (1): FPT",
        buckets=AgendaBuckets(decide=["FPT"], watch=[], defer=[]),
    )

    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = [SimpleNamespace(ticker="FPT")]

    quote_service = AsyncMock()
    quote_service.get_bulk_quotes.return_value = []

    existing_action = PrioritizedAction(
        ticker="FPT",
        priority=ActionPriority.WATCH_MORE,
        action="Theo dõi thêm FPT",
        rationale="",
        confidence=0.5,
    )
    brief_with_watch_more = sample_brief.model_copy(
        update={"prioritized_actions": [existing_action]}
    )

    agent = AsyncMock()
    agent.morning_brief.return_value = brief_with_watch_more

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_morning_brief(user_id="u2")

    actions = result.output.prioritized_actions
    # Still exactly one action, but upgraded.
    assert len(actions) == 1
    action = actions[0]
    assert action.ticker == "FPT"
    assert action.priority == ActionPriority.ACT_TODAY
    # Rationale must be filled by enforcement layer when originally empty.
    assert action.rationale
    # Confidence must be bumped to at least 0.7.
    assert action.confidence >= 0.7


@pytest.mark.asyncio
async def test_enforce_agenda_mapping_multi_decide_mixed_upgrade_and_append(
    sample_brief: BriefOutput,
) -> None:
    """Multi-DECIDE: one ACT_TODAY stays, one WATCH_MORE is upgraded in-place.

    This locks the mix behavior:
      - DECIDE ticker đã có ACT_TODAY giữ nguyên.
      - DECIDE ticker chỉ có WATCH_MORE được nâng cấp ACT_TODAY, không tạo
        bản sao, đảm bảo mỗi ticker chỉ có một PrioritizedAction.
    """
    _AGENDA_CACHE["u3"] = CachedAgenda(
        summary="Daily Agenda:\nDECIDE (2): HPG, FPT",
        buckets=AgendaBuckets(decide=["HPG", "FPT"], watch=[], defer=[]),
    )

    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = [
        SimpleNamespace(ticker="HPG"),
        SimpleNamespace(ticker="FPT"),
    ]

    quote_service = AsyncMock()
    quote_service.get_bulk_quotes.return_value = []

    existing_actions = [
        PrioritizedAction(
            ticker="HPG",
            priority=ActionPriority.ACT_TODAY,
            action="Đã sẵn sàng hành động với HPG",
            rationale="",
            confidence=0.8,
        ),
        PrioritizedAction(
            ticker="FPT",
            priority=ActionPriority.WATCH_MORE,
            action="Theo dõi thêm FPT",
            rationale="",
            confidence=0.5,
        ),
    ]
    brief_with_mixed = sample_brief.model_copy(
        update={"prioritized_actions": existing_actions}
    )

    agent = AsyncMock()
    agent.morning_brief.return_value = brief_with_mixed

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_morning_brief(user_id="u3")

    actions = result.output.prioritized_actions
    # Vẫn chỉ có 2 action, không sinh thêm bản sao.
    assert len(actions) == 2

    by_ticker = {a.ticker: a for a in actions}
    hpg = by_ticker["HPG"]
    fpt = by_ticker["FPT"]

    # HPG đã ACT_TODAY thì giữ nguyên.
    assert hpg.priority == ActionPriority.ACT_TODAY
    assert hpg.confidence == 0.8

    # FPT được nâng cấp WATCH_MORE → ACT_TODAY, rationale được fill và confidence được bump.
    assert fpt.priority == ActionPriority.ACT_TODAY
    assert fpt.rationale
    assert fpt.confidence >= 0.7
