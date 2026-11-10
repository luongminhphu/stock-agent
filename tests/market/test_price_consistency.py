"""Đồng nhất giá với CTCK: bước giá, giờ chốt phiên theo sàn, đối soát open/close,
parser các nguồn (VCI/KBS/VNDirect) và chuỗi fallback lấp mã thiếu."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from src.market.adapters.chained import ChainedAdapter
from src.market.adapters.kbs import parse_kbs_item
from src.market.adapters.mock import MockAdapter, _make_mock_quote
from src.market.adapters.vci import _parse_item as parse_vci_item
from src.market.adapters.vci_ohlcv import _parse_ts
from src.market.adapters.vndirect import _parse_item as parse_vnd_item
from src.market.ohlcv_service import Candle
from src.market.price_reconciliation import (
    FLAG_CLOSE_VS_CANDLE,
    FLAG_CLOSE_VS_PEER,
    FLAG_OFF_TICK,
    SOURCE_CANDLE,
    SOURCE_CONSENSUS,
    SOURCE_PEER,
    SessionPriceReconciler,
    reconcile_quote,
)
from src.market.price_rules import (
    is_on_tick,
    is_session_closed,
    prices_match,
    snap_to_tick,
    tick_size,
)
from src.market.quote_service import Quote, QuoteService

_ICT = timezone(timedelta(hours=7))


def _q(ticker: str = "HPG", price: float = 27_350, **kw: object) -> Quote:
    base = {
        "ticker": ticker,
        "price": price,
        "change": price - 27_000,
        "change_pct": 1.3,
        "volume": 1_000_000,
        "value": 0.0,
        "open": 27_000,
        "high": 27_500,
        "low": 26_900,
        "ref_price": 27_000,
        "ceiling": 28_850,
        "floor": 25_150,
        "timestamp": datetime(2026, 11, 10, 14, 46, tzinfo=_ICT),
    }
    base.update(kw)
    return Quote(**base)  # type: ignore[arg-type]


# ── price_rules ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("price", "exchange", "tick"),
    [
        (9_990, "HOSE", 10),
        (10_000, "HOSE", 50),
        (49_950, "HSX", 50),
        (50_000, "HOSE", 100),
        (9_000, "HNX", 100),
        (120_000, "UPCOM", 100),
    ],
)
def test_tick_size(price: float, exchange: str, tick: float) -> None:
    assert tick_size(price, exchange) == tick


def test_snap_and_on_tick() -> None:
    assert snap_to_tick(27_370, "HOSE") == 27_350
    assert snap_to_tick(27_380, "HOSE") == 27_400
    assert is_on_tick(27_350, "HOSE") and not is_on_tick(27_360, "HOSE")
    assert snap_to_tick(15_040, "HNX") == 15_000
    assert prices_match(27_350, 27_360, "HOSE")
    assert not prices_match(27_350, 27_400, "HOSE")


def test_session_close_per_exchange() -> None:
    t = datetime(2026, 11, 10, 14, 50, tzinfo=_ICT)
    assert is_session_closed("HOSE", t)
    assert is_session_closed("HNX", t)
    assert not is_session_closed("UPCOM", t), "UPCoM khớp liên tục tới 15:00"
    assert not is_session_closed("", t), "mã chưa rõ sàn → luật muộn nhất"
    assert is_session_closed("UPCOM", t.replace(hour=15, minute=1))
    # input UTC vẫn quy về ICT
    assert is_session_closed("HOSE", datetime(2026, 11, 10, 7, 46, tzinfo=UTC))


# ── reconcile_quote ──────────────────────────────────────────────────────────


def _candle(open_: float, close: float, d: date = date(2026, 11, 10)) -> Candle:
    return Candle(
        ticker="HPG",
        date=d,
        open=open_,
        high=max(open_, close) + 100,
        low=min(open_, close) - 100,
        close=close,
        volume=1,
        value=0,
    )


def test_candle_is_authoritative_and_flags_mismatch() -> None:
    q = _q(price=27_300, open=27_000)
    settled, check = reconcile_quote(q, "HOSE", candle=_candle(27_050, 27_450))
    assert check.source == SOURCE_CANDLE
    assert FLAG_CLOSE_VS_CANDLE in check.flags
    assert settled.close == settled.price == 27_450
    assert settled.open == 27_050
    assert settled.change == 450
    assert settled.low <= settled.open <= settled.high


def test_candle_match_has_no_flags() -> None:
    settled, check = reconcile_quote(_q(), "HOSE", candle=_candle(27_000, 27_350))
    assert check.flags == ()
    assert settled.close == 27_350


def test_peer_consensus_without_candle() -> None:
    _, check = reconcile_quote(_q(), "HOSE", peer=_q(price=27_350))
    assert check.source == SOURCE_CONSENSUS and check.flags == ()


def test_peer_wins_when_primary_invalid() -> None:
    primary = _q(price=27_365)  # lệch lưới bước giá
    peer = _q(price=27_500, open=27_050)
    settled, check = reconcile_quote(primary, "HOSE", peer=peer)
    assert check.source == SOURCE_PEER
    assert FLAG_CLOSE_VS_PEER in check.flags
    assert settled.close == 27_500 and settled.open == 27_050


def test_primary_kept_when_both_valid_but_disagree() -> None:
    settled, check = reconcile_quote(_q(price=27_350), "HOSE", peer=_q(price=27_500))
    assert FLAG_CLOSE_VS_PEER in check.flags
    assert settled.close == 27_350


def test_off_tick_is_snapped_and_flagged() -> None:
    settled, check = reconcile_quote(_q(price=27_362, open=27_004), "HOSE")
    assert FLAG_OFF_TICK in check.flags
    assert (settled.close, settled.open) == (27_350, 27_000)


# ── SessionPriceReconciler ───────────────────────────────────────────────────


class _FakeOHLCV:
    def __init__(self, candles: dict[str, list[Candle]]) -> None:
        self._c = candles

    async def get_candles(self, ticker: str, from_date: date, to_date: date) -> list[Candle]:
        return self._c.get(ticker, [])


async def test_reconciler_uses_candle_for_session_date_only() -> None:
    session = date(2026, 11, 10)
    ohlcv = _FakeOHLCV({"HPG": [_candle(26_000, 26_500, session - timedelta(days=1))]})
    rec = SessionPriceReconciler(ohlcv_service=ohlcv, exchange_of=lambda _t: "HOSE")
    settled, checks = await rec.reconcile([_q()], session)
    assert checks[0].source != SOURCE_CANDLE, "nến ngày cũ không được dùng làm giá chốt"
    assert settled[0].close == 27_350


# ── capture_eod_close: pending theo sàn + reconciler ─────────────────────────


async def test_capture_leaves_upcom_pending_before_1500() -> None:
    svc = QuoteService(MockAdapter())
    exch = {"HPG": "HOSE", "BSR": "UPCOM"}
    result = await svc.capture_eod_close(
        ["HPG", "BSR"],
        now=datetime(2026, 11, 10, 14, 50, tzinfo=_ICT),
        exchange_of=lambda t: exch[t],
    )
    assert result.pending == ["BSR"]
    assert result.captured == 1 and result.requested == 2
    assert svc._cache.get_last_known("BSR") is None


async def test_capture_applies_reconciler() -> None:
    class _Rec:
        async def reconcile(self, quotes: list[Quote], session_date: date):  # type: ignore[no-untyped-def]
            from src.market.price_reconciliation import PriceCheck

            out = [replace(q, close=q.price + 50, price=q.price + 50) for q in quotes]
            checks = [PriceCheck(q.ticker, "HOSE", SOURCE_CANDLE, ("x",)) for q in quotes]
            return out, checks

    svc = QuoteService(MockAdapter())
    result = await svc.capture_eod_close(["VNM"], reconciler=_Rec())
    mock = _make_mock_quote("VNM")
    assert result.from_candle == 1 and result.flagged == {"VNM": ["x"]}
    assert svc._cache.get_last_known("VNM").close == mock.price + 50


# ── Parsers ──────────────────────────────────────────────────────────────────


def _vci(match_extra: dict) -> dict:
    return {
        "listingInfo": {"symbol": "HPG", "refPrice": 27_000, "ceiling": 28_850, "floor": 25_150},
        "matchPrice": {"matchPrice": 27_350, "matchVolume": 100, **match_extra},
    }


def test_vci_open_reads_open_price_field() -> None:
    assert parse_vci_item(_vci({"openPrice": 27_100})).open == 27_100
    assert parse_vci_item(_vci({"open": 27_050})).open == 27_050


def test_vci_open_without_field_uses_match_not_ref_when_traded() -> None:
    assert parse_vci_item(_vci({})).open == 27_350
    no_trade = _vci({"matchVolume": 0, "matchPrice": 0})
    assert parse_vci_item(no_trade).open == 27_000


def test_vci_daily_candle_date_is_ict() -> None:
    # 00:00 ICT ngày 10/11 = 17:00 UTC ngày 09/11 → phải ra 10/11
    epoch = datetime(2026, 11, 10, 0, 0, tzinfo=_ICT).timestamp()
    assert _parse_ts(int(epoch))[1] == date(2026, 11, 10)
    # 00:00 UTC (07:00 ICT) vẫn cùng ngày
    assert _parse_ts(int(datetime(2026, 11, 10, tzinfo=UTC).timestamp()))[1] == date(2026, 11, 10)


def test_kbs_parse() -> None:
    q = parse_kbs_item(
        {
            "SB": "hpg",
            "RE": 27_000,
            "CL": 28_850,
            "FL": 25_150,
            "CP": 27_350,
            "OP": 27_100,
            "HI": 27_500,
            "LO": 26_900,
            "TT": 12_345_600,
            "TV": 3.3e11,
            "TD": "10/11/2026",
        }
    )
    assert (q.ticker, q.price, q.open, q.volume, q.change) == (
        "HPG",
        27_350,
        27_100,
        12_345_600,
        350,
    )


def test_vndirect_parse_scales_thousands_and_uses_price_fields() -> None:
    q = parse_vnd_item(
        {
            "code": "HPG",
            "date": "2026-11-10",
            "time": "15:04:01",
            "floor": "HOSE",  # tên sàn — không được hiểu là giá sàn
            "basicPrice": 27.0,
            "ceilingPrice": 28.85,
            "floorPrice": 25.15,
            "open": 27.1,
            "high": 27.5,
            "low": 26.9,
            "close": 27.35,
            "change": 0.35,
            "pctChange": 1.3,
            "nmVolume": 12_000_000,
        }
    )
    assert q.price == pytest.approx(27_350)
    assert q.open == pytest.approx(27_100)
    assert q.floor == pytest.approx(25_150)
    assert q.ref_price == pytest.approx(27_000)
    assert q.change == pytest.approx(350)


# ── ChainedAdapter ───────────────────────────────────────────────────────────


class _Partial(MockAdapter):
    """Trả lô nhưng bỏ sót một số mã (giống nguồn thiếu mã mới niêm yết)."""

    def __init__(self, drop: set[str]) -> None:
        super().__init__()
        self._drop = drop

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        return [q for q in await super().fetch_bulk_quotes(tickers) if q.ticker not in self._drop]


async def test_chain_fills_missing_tickers_from_next_source() -> None:
    chain = ChainedAdapter(_Partial({"VNM"}), MockAdapter())
    quotes = await chain.fetch_bulk_quotes(["HPG", "VNM", "FPT"])
    assert [q.ticker for q in quotes] == ["HPG", "VNM", "FPT"]


async def test_chain_three_sources() -> None:
    chain = ChainedAdapter(
        MockAdapter(fail_tickers={"HPG"}), MockAdapter(fail_tickers={"HPG"}), MockAdapter()
    )
    assert (await chain.fetch_quote("HPG")).ticker == "HPG"
    assert len(await chain.fetch_bulk_quotes(["HPG", "VNM"])) == 2
