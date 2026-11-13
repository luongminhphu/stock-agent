"""market.baserate_actions — base rate → bước tiếp theo theo quy tắc."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.market.baserate import PathStats
from src.market.baserate_actions import ACT, CAUTION, WATCH, next_step


@dataclass(frozen=True)
class _O:
    avg_max_drawdown: float | None = -0.10
    median_return: float | None = 0.08
    p90_return: float | None = 0.25


@dataclass(frozen=True)
class _Rep:
    suggestion: str
    confidence: str = "trung_binh"
    last_close: float | None = 25_000.0
    outcomes: dict = field(default_factory=lambda: {60: _O(), 120: _O()})
    path: PathStats | None = None


PATH = PathStats(0.12, 0.08, 120, 15, 0.6, 0.27, 0.13, 40.0, 22.0)


def test_bullish_not_holding_gives_plan_with_levels_and_odds() -> None:
    ns = next_step(_Rep("NGHIENG_MUA", path=PATH), holding=False)
    assert ns.tone == ACT and ns.plan_cta
    assert ns.stop_price == 22_000.0 and ns.target_price == 27_000.0 and ns.review_sessions == 120
    text = " ".join(ns.steps)
    assert "Stop-loss ~22.000 (−12%)" in text and "Target ~27.000 (+8%)" in text
    assert "60% chạm Target" in text and "sụt tới 10%" in text


def test_low_confidence_bullish_is_watch() -> None:
    ns = next_step(_Rep("NGHIENG_MUA", confidence="thap", path=PATH), holding=False)
    assert ns.tone == WATCH and "thăm dò" in ns.headline


def test_stop_hit_more_often_warns_about_target() -> None:
    bad = replace(PATH, target_first=0.2, stop_first=0.5)
    ns = next_step(_Rep("NGHIENG_MUA", path=bad), holding=False)
    assert any("Target có thể quá xa" in s for s in ns.steps)


def test_bearish_holding_suggests_tighten_and_no_levels() -> None:
    ns = next_step(_Rep("NGHIENG_TRANH", path=PATH), holding=True)
    assert ns.tone == CAUTION and ns.stop_price is None
    assert "~23.750" in ns.steps[0]
    assert next_step(_Rep("NGHIENG_TRANH"), holding=False).headline.startswith("Tiền lệ bất lợi")


def test_neutral_and_insufficient() -> None:
    ns = next_step(_Rep("TRUNG_TINH"), holding=False)
    assert ns.tone == WATCH and not ns.plan_cta and "Watchlist" in ns.steps[0]
    held = next_step(_Rep("TRUNG_TINH", path=PATH), holding=True)
    assert held.stop_price == 22_000.0  # nhắc lại mức của kế hoạch
    ins = next_step(_Rep("KHONG_DU_TIEN_LE", outcomes={}), holding=False)
    assert ins.tone == WATCH and ins.plan_cta and "−10%" in ins.steps[0]
