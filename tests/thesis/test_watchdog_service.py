"""Wave D2: WatchdogService — giá theo lô qua PriceSnapshot, rule stop dùng ATR, AI escalation.

Không chạm DB thật: ThesisRepository được patch bằng fake trả list thesis
SimpleNamespace; agent/quote/context service là AsyncMock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.thesis.models import AssumptionStatus
from src.thesis.price_snapshot import PriceSnapshot
from src.thesis.stop_breach_service import NEAR_STOP_ATR
from src.thesis.watchdog_service import WatchdogService, WatchdogTickerResult, _max_level


def _assumption(status: AssumptionStatus, aid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=aid, description=f"A{aid}", status=status, note="")


def _thesis(
    ticker: str = "HPG",
    stop_loss: float | None = 23_000.0,
    assumptions: list | None = None,
    created_days_ago: int = 1,
    reviews: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        user_id="u1",
        ticker=ticker,
        title="Thép hồi phục",
        summary="Giá HRC tăng",
        entry_price=24_000.0,
        stop_loss=stop_loss,
        target_price=30_000.0,
        assumptions=assumptions
        if assumptions is not None
        else [_assumption(AssumptionStatus.VALID)],
        reviews=reviews or [],
        created_at=datetime.now(UTC) - timedelta(days=created_days_ago),
        status=None,
        closed_at=None,
    )


def _ctx(price: float, atr14: float | None = None, quality: str = "live") -> SimpleNamespace:
    return SimpleNamespace(
        price=price,
        atr14=atr14,
        source_quality=quality,
        format_for_prompt=lambda: f"{price:,.0f} | ATR14 {atr14} | data {quality}",
    )


def _service(theses: list, *, agent=None, tcs=None, qs=None, invalidation=None) -> WatchdogService:
    svc = WatchdogService(
        session=AsyncMock(),
        watchdog_agent=agent,
        quote_service=qs,
        invalidation_svc=invalidation,
        ticker_context_service=tcs,
    )
    svc._repo = SimpleNamespace(
        list_active_for_user=AsyncMock(return_value=theses),
        save=AsyncMock(),
    )
    return svc


def _health(overall: str, score: int = 50) -> SimpleNamespace:
    return SimpleNamespace(
        health_score=score,
        overall_health=overall,
        recommended_action="REVIEW_SOON",
        alert_level={"HEALTHY": "OK", "WARNING": "SILENT_WARNING", "CRITICAL": "URGENT_ALERT"}[
            overall
        ],
        discord_summary=lambda ticker: f"{ticker} {overall}",
    )


# ── helpers ─────────────────────────────────────────────────────────────────


def test_max_level_ordering() -> None:
    assert _max_level(None, "OK") == "OK"
    assert _max_level("OK", "SILENT_WARNING", None) == "SILENT_WARNING"
    assert _max_level("SILENT_WARNING", "URGENT_ALERT") == "URGENT_ALERT"
    assert _max_level() == "OK"


class TestStopAlertLevel:
    def test_atr_path_owns_when_available(self) -> None:
        f = WatchdogService._stop_alert_level
        assert f(1.0, -0.5) == "OK"  # đã xuyên → StopBreachService sở hữu
        assert f(1.0, NEAR_STOP_ATR / 2) == "SILENT_WARNING"
        assert f(1.0, NEAR_STOP_ATR) == "OK"
        assert f(1.0, 3.0) == "OK"

    def test_pct_fallback_only_without_atr(self) -> None:
        f = WatchdogService._stop_alert_level
        assert f(1.5, None) == "URGENT_ALERT"
        assert f(4.0, None) == "SILENT_WARNING"
        assert f(8.0, None) == "OK"
        assert f(None, None) is None


# ── run_for_user ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_bulk_price_fetch_once_and_near_stop_warning() -> None:
    # Giá 23,400, stop 23,000, ATR 500 → distance 0.8 ATR → near_stop → SILENT_WARNING
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={"HPG": _ctx(23_400.0, 500.0, "live")}))
    qs = SimpleNamespace(get_quote=AsyncMock())
    svc = _service([_thesis()], tcs=tcs, qs=qs)

    run = await svc.run_for_user("u1")

    tcs.get_many.assert_awaited_once_with(["HPG"])
    qs.get_quote.assert_not_awaited()
    assert len(run.results) == 1
    r = run.results[0]
    assert r.alert_level == "SILENT_WARNING"
    assert r.near_stop is True
    assert r.source_quality == "live"
    assert r.stop_distance_atr == pytest.approx(0.8)
    assert r.health_score is None and r.agent_failed is False


@pytest.mark.anyio
async def test_breached_stop_not_realerted_by_watchdog() -> None:
    # Giá 22,000 < stop 23,000 với ATR → distance âm → "OK" (StopBreachService lo)
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={"HPG": _ctx(22_000.0, 500.0)}))
    svc = _service([_thesis()], tcs=tcs)

    run = await svc.run_for_user("u1")

    assert run.results[0].alert_level == "OK"
    assert run.results[0].stop_distance_atr < 0


@pytest.mark.anyio
async def test_quote_fallback_uses_pct_rule() -> None:
    # Không có context service → get_quote → không ATR → pct: (23300-23000)/23300 = 1.3% → URGENT
    qs = SimpleNamespace(get_quote=AsyncMock(return_value=SimpleNamespace(price=23_300.0)))
    svc = _service([_thesis()], qs=qs)

    run = await svc.run_for_user("u1")

    r = run.results[0]
    assert r.alert_level == "URGENT_ALERT"
    assert r.source_quality == "quote"
    assert r.stop_distance_atr is None
    assert r.overall_health == "CRITICAL" and r.recommended_action == "REVIEW_URGENT"


@pytest.mark.anyio
async def test_assumption_collapse_urgent_without_price() -> None:
    thesis = _thesis(
        stop_loss=None,
        assumptions=[
            _assumption(AssumptionStatus.INVALID, 1),
            _assumption(AssumptionStatus.UNCERTAIN, 2),
            _assumption(AssumptionStatus.VALID, 3),
        ],
    )
    svc = _service([thesis])  # không quote, không context

    run = await svc.run_for_user("u1")

    assert run.results[0].alert_level == "URGENT_ALERT"
    assert "A1" in (run.results[0].discord_summary or "")


@pytest.mark.anyio
async def test_stale_thesis_silent_warning() -> None:
    svc = _service([_thesis(stop_loss=None, created_days_ago=20)])
    run = await svc.run_for_user("u1")
    assert run.results[0].alert_level == "SILENT_WARNING"


@pytest.mark.anyio
async def test_agent_receives_ticker_context_and_stop_escalates_ai_verdict() -> None:
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={"HPG": _ctx(23_400.0, 500.0, "stale")}))
    agent = SimpleNamespace(assess=AsyncMock(return_value=_health("HEALTHY", 80)))
    svc = _service([_thesis()], tcs=tcs, agent=agent)

    run = await svc.run_for_user("u1")

    agent.assess.assert_awaited_once()
    ctx = agent.assess.await_args.args[0]
    assert "ATR14 500" in ctx.ticker_context and "data stale" in ctx.ticker_context
    assert ctx.current_price == 23_400.0
    assert agent.assess.await_args.kwargs["user_id"] == "u1"

    r = run.results[0]
    # AI nói HEALTHY nhưng near_stop → nâng lên SILENT_WARNING, giữ health_score của AI
    assert r.alert_level == "SILENT_WARNING"
    assert r.health_score == 80 and r.overall_health == "HEALTHY"


@pytest.mark.anyio
async def test_agent_failure_falls_back_to_rules() -> None:
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={"HPG": _ctx(26_000.0, 500.0)}))
    agent = SimpleNamespace(assess=AsyncMock(return_value=None))
    svc = _service([_thesis()], tcs=tcs, agent=agent)

    run = await svc.run_for_user("u1")

    r = run.results[0]
    assert r.alert_level == "OK" and r.agent_failed is True and r.health_score is None


@pytest.mark.anyio
async def test_ai_critical_triggers_invalidation_check() -> None:
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={"HPG": _ctx(26_000.0, 500.0)}))
    agent = SimpleNamespace(assess=AsyncMock(return_value=_health("CRITICAL", 20)))
    signal = SimpleNamespace(verdict="CONFIRMED", action="EXIT", confidence=0.9)
    inv = SimpleNamespace(check_with_ai=AsyncMock(return_value=(None, signal)))
    thesis = _thesis()
    svc = _service([thesis], tcs=tcs, agent=agent, invalidation=inv)

    run = await svc.run_for_user("u1")

    r = run.results[0]
    assert r.alert_level == "URGENT_ALERT" and r.auto_invalidated is True
    inv.check_with_ai.assert_awaited_once()
    assert inv.check_with_ai.await_args.kwargs["watchdog_verdict"] == "CRITICAL"
    svc._repo.save.assert_awaited_once_with(thesis)
    assert run.has_urgent() and run.has_notable()


@pytest.mark.anyio
async def test_low_confidence_confirmation_does_not_invalidate() -> None:
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={"HPG": _ctx(26_000.0, 500.0)}))
    agent = SimpleNamespace(assess=AsyncMock(return_value=_health("CRITICAL", 20)))
    signal = SimpleNamespace(verdict="CONFIRMED", action="EXIT", confidence=0.5)
    inv = SimpleNamespace(check_with_ai=AsyncMock(return_value=(None, signal)))
    svc = _service([_thesis()], tcs=tcs, agent=agent, invalidation=inv)

    run = await svc.run_for_user("u1")

    r = run.results[0]
    assert r.alert_level == "URGENT_ALERT" and r.auto_invalidated is False
    assert r.invalidation_signal is signal
    svc._repo.save.assert_not_awaited()


@pytest.mark.anyio
async def test_per_thesis_error_isolated() -> None:
    bad = _thesis(ticker="BAD")
    bad.assumptions = None  # gây TypeError trong rule check
    svc = _service([bad, _thesis(ticker="HPG", stop_loss=None)])

    run = await svc.run_for_user("u1")

    assert "BAD" in run.errors
    assert [r.ticker for r in run.results] == ["HPG"]


def test_result_near_stop_property() -> None:
    base = dict(
        thesis_id=1,
        ticker="X",
        alert_level="OK",
        health_score=None,
        overall_health=None,
        recommended_action=None,
        discord_summary=None,
    )
    assert WatchdogTickerResult(**base, stop_distance_atr=0.5).near_stop is True
    assert WatchdogTickerResult(**base, stop_distance_atr=-0.1).near_stop is False
    assert WatchdogTickerResult(**base, stop_distance_atr=None).near_stop is False
    assert PriceSnapshot(price=1.0).prompt_context == ""
