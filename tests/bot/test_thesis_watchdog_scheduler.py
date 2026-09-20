"""Wave D3: ThesisWatchdogScheduler — thin adapter route WatchdogRunResult → Discord.

Không chạy tasks.loop; gọi run_once() trực tiếp với WatchdogService/bootstrap
được patch. Kiểm tra: route URGENT → alert channel, SILENT → morning digest,
chỉ OK → không gửi; monitor record_success/failure; embed render đúng nội dung.
"""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.bot.scheduler as sched_mod
from src.bot.commands.thesis_embeds import (
    build_watchdog_digest_embed,
    build_watchdog_urgent_embed,
)
from src.bot.scheduler import ThesisWatchdogScheduler
from src.thesis.watchdog_service import WatchdogRunResult, WatchdogTickerResult

_NOW = datetime.datetime(2026, 9, 21, 1, 25, tzinfo=datetime.UTC)


def _result(ticker: str, level: str, **over) -> WatchdogTickerResult:
    base = dict(
        thesis_id=1,
        ticker=ticker,
        alert_level=level,
        health_score=None,
        overall_health=None,
        recommended_action=None,
        discord_summary=None,
    )
    base.update(over)
    return WatchdogTickerResult(**base)


def _run(*results: WatchdogTickerResult, errors: dict | None = None) -> WatchdogRunResult:
    return WatchdogRunResult(user_id="u1", run_at=_NOW, results=list(results), errors=errors or {})


# ── embeds ──────────────────────────────────────────────────────────────────


def test_urgent_embed_lines() -> None:
    run = _run(
        _result(
            "HPG",
            "URGENT_ALERT",
            health_score=25,
            overall_health="CRITICAL",
            recommended_action="CONSIDER_EXIT",
            stop_distance_atr=0.4,
            source_quality="stale",
            auto_invalidated=True,
        ),
        _result("VNM", "OK"),
    )
    embed = build_watchdog_urgent_embed(run, _NOW)
    assert "1 thesis" in embed.title
    d = embed.description
    assert "**HPG** — CRITICAL (25/100)" in d
    assert "Cân nhắc thoát" in d and "cách stop 0.4 ATR" in d and "dữ liệu cũ" in d
    assert "đã tự vô hiệu" in d
    assert "VNM" not in d


def test_digest_embed_counts_and_pct_fallback() -> None:
    run = _run(
        _result("HPG", "URGENT_ALERT", health_score=20, overall_health="CRITICAL"),
        _result(
            "SSI",
            "SILENT_WARNING",
            stop_loss_distance_pct=3.2,
            stop_distance_atr=None,
            agent_failed=True,
        ),
        _result("VNM", "OK"),
        errors={"BAD": "boom"},
    )
    embed = build_watchdog_digest_embed(run, _NOW)
    assert embed.description == "1 ổn · 1 cần chú ý · 1 khẩn · 1 lỗi (BAD)"
    names = [f.name for f in embed.fields]
    assert names == ["Cần chú ý", "Khẩn (đã gửi alert riêng)"]
    assert "**SSI** — rule-based (AI lỗi) · cách stop 3.2%" in embed.fields[0].value
    assert embed.fields[1].value == "HPG"


# ── scheduler routing ───────────────────────────────────────────────────────


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    """Patch settings, DB session, bootstrap getters và WatchdogService."""
    import src.platform.bootstrap as bootstrap
    import src.thesis.watchdog_service as wd_mod

    monkeypatch.setattr(sched_mod.settings, "scheduler_user_id", "u1", raising=False)
    monkeypatch.setattr(sched_mod.settings, "morning_channel_id", "100", raising=False)
    monkeypatch.setattr(type(sched_mod.settings), "alert_channel_id", property(lambda self: "200"))
    monkeypatch.setattr(sched_mod.settings, "auto_invalidate_enabled", True, raising=False)
    monkeypatch.setattr(sched_mod.settings, "auto_invalidate_min_confidence", 0.8, raising=False)

    session = AsyncMock()

    @asynccontextmanager
    async def _session_factory():
        yield session

    monkeypatch.setattr(sched_mod, "AsyncSessionLocal", _session_factory)
    monkeypatch.setattr(bootstrap, "get_ai_client", lambda: object())
    monkeypatch.setattr(bootstrap, "get_quote_service", lambda: object())
    monkeypatch.setattr(bootstrap, "get_ticker_context_service", lambda: object())

    state: dict = {"ctor_kwargs": None, "run": _run()}

    class FakeWatchdogService:
        def __init__(self, **kwargs):
            state["ctor_kwargs"] = kwargs

        async def run_for_user(self, user_id: str):
            state["user_id"] = user_id
            return state["run"]

    monkeypatch.setattr(wd_mod, "WatchdogService", FakeWatchdogService)

    channels = {100: SimpleNamespace(send=AsyncMock()), 200: SimpleNamespace(send=AsyncMock())}
    client = MagicMock()
    client.get_channel = lambda cid: channels.get(cid)
    monitor = SimpleNamespace(
        register_task=MagicMock(), record_success=AsyncMock(), record_failure=AsyncMock()
    )
    scheduler = ThesisWatchdogScheduler(client, monitor=monitor)
    return SimpleNamespace(
        scheduler=scheduler, state=state, channels=channels, monitor=monitor, session=session
    )


@pytest.mark.anyio
async def test_routes_urgent_to_alert_and_digest_to_morning(wired) -> None:
    wired.state["run"] = _run(
        _result("HPG", "URGENT_ALERT", health_score=20, overall_health="CRITICAL"),
        _result("SSI", "SILENT_WARNING", stop_distance_atr=0.6),
    )

    await wired.scheduler.run_once(_NOW)

    assert wired.state["user_id"] == "u1"
    kw = wired.state["ctor_kwargs"]
    assert kw["min_confidence"] == 0.8 and kw["invalidation_svc"] is not None
    assert kw["ticker_context_service"] is not None
    wired.session.commit.assert_awaited_once()

    alert_embed = wired.channels[200].send.await_args.kwargs["embed"]
    digest_embed = wired.channels[100].send.await_args.kwargs["embed"]
    assert "cần xem lại ngay" in alert_embed.title
    assert "Watchdog sáng" in digest_embed.title
    wired.monitor.record_success.assert_awaited_once_with("thesis.watchdog")
    wired.monitor.record_failure.assert_not_awaited()


@pytest.mark.anyio
async def test_missing_user_id_skips(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched_mod.settings, "scheduler_user_id", None, raising=False)
    await wired.scheduler.run_once(_NOW)
    assert wired.state["ctor_kwargs"] is None
    wired.monitor.record_success.assert_not_awaited()


@pytest.mark.anyio
async def test_only_ok_sends_nothing(wired) -> None:
    wired.state["run"] = _run(_result("HPG", "OK"), _result("VNM", "OK"))

    await wired.scheduler.run_once(_NOW)

    wired.channels[100].send.assert_not_awaited()
    wired.channels[200].send.assert_not_awaited()
    wired.monitor.record_success.assert_awaited_once()


@pytest.mark.anyio
async def test_auto_invalidate_disabled_passes_no_invalidation_service(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched_mod.settings, "auto_invalidate_enabled", False, raising=False)
    wired.state["run"] = _run(_result("HPG", "SILENT_WARNING"))

    await wired.scheduler.run_once(_NOW)

    assert wired.state["ctor_kwargs"]["invalidation_svc"] is None
    wired.channels[100].send.assert_awaited_once()
    wired.channels[200].send.assert_not_awaited()


@pytest.mark.anyio
async def test_service_error_records_failure(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.thesis.watchdog_service as wd_mod

    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(wd_mod, "WatchdogService", Boom)

    await wired.scheduler.run_once(_NOW)

    wired.monitor.record_failure.assert_awaited_once()
    assert wired.monitor.record_failure.await_args.args[0] == "thesis.watchdog"
    wired.monitor.record_success.assert_not_awaited()
