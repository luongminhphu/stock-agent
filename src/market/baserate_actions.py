"""Từ base rate → "Nên làm gì tiếp theo" (theo quy tắc, không dự báo).

Owner: market segment.

Đầu vào: BaseRateReport (gợi ý, độ tin, kết quả tầm chính, xác suất chạm Stop/Target) và
việc nhà đầu tư đang nắm mã hay chưa. Đầu ra: một câu kết luận + vài bước cụ thể kèm mức
giá — để người đọc biết bước kế tiếp thay vì chỉ thấy con số.

Nguyên tắc:
  - Chỉ diễn giải thống kê đã có; không thêm nhận định ngoài dữ liệu.
  - Mọi hành động mua đều đi kèm Kế hoạch nắm giữ (Stop-loss, Target, ngày xem lại).
  - Độ tin thấp / chưa đủ tiền lệ → ưu tiên theo dõi hơn hành động.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.market.baserate import (
    SUGGEST_INSUFFICIENT,
    SUGGEST_LEAN_AVOID,
    SUGGEST_LEAN_BUY,
    default_levels,
)

ACT = "act"  # có thể hành động (mua thăm dò / nắm tiếp)
WATCH = "watch"  # theo dõi, chưa cần làm gì
CAUTION = "caution"  # bất lợi — tránh mua / xem lại vị thế


@dataclass(frozen=True)
class NextStep:
    tone: str  # act | watch | caution
    headline: str
    steps: list[str] = field(default_factory=list)
    stop_price: float | None = None
    target_price: float | None = None
    review_sessions: int | None = None
    plan_cta: bool = False  # hiện nút "Lập kế hoạch nắm giữ"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _price(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".") if x >= 1000 else f"{x:,.1f}".replace(".", ",")


def next_step(rep: Any, *, holding: bool) -> NextStep:
    """Bước tiếp theo cho một mã dựa trên báo cáo base rate."""
    sug = rep.suggestion
    outcomes = rep.outcomes or {}
    main_h = max(outcomes) if outcomes else None
    path = getattr(rep, "path", None)
    if path is not None:
        main_h = path.horizon
    main = outcomes.get(main_h) if main_h is not None else None
    close = rep.last_close
    low_conf = rep.confidence in ("khong_du", "thap")

    stop_pct = target_pct = None
    if path is not None:
        stop_pct, target_pct = path.stop_pct, path.target_pct
    elif main is not None:
        stop_pct, target_pct = default_levels(main)
    stop_px = close * (1 - stop_pct) if close and stop_pct else None
    tgt_px = close * (1 + target_pct) if close and target_pct else None

    levels: list[str] = []
    if stop_px is not None and stop_pct is not None:
        lv = f"Stop-loss ~{_price(stop_px)} (−{_pct(stop_pct)})"
        if tgt_px is not None and target_pct is not None:
            lv += f", Target ~{_price(tgt_px)} (+{_pct(target_pct)})"
        if main_h:
            lv += f", xem lại sau {main_h} phiên"
        levels.append(lv + ".")
    odds: list[str] = []
    if path is not None and path.episodes and path.target_first is not None:
        odds.append(
            f"Với các mức này, {path.episodes} lần trước: {_pct(path.target_first)} chạm Target "
            f"trước, {_pct(path.stop_first or 0)} chạm Stop-loss trước, "
            f"{_pct(path.neither or 0)} hết kỳ chưa chạm mức nào."
        )
        if (path.stop_first or 0) > (path.target_first or 0) and path.target_pct:
            odds.append(
                "Chạm Stop-loss trước nhiều hơn chạm Target — Target có thể quá xa so với tiền lệ, "
                "cân nhắc hạ Target hoặc chờ trạng thái tốt hơn."
            )
    risk: list[str] = []
    if main is not None and main.avg_max_drawdown is not None and main.avg_max_drawdown < 0:
        risk.append(
            f"Các lần trước thường sụt tới {_pct(abs(main.avg_max_drawdown))} trong kỳ trước khi "
            "có kết quả — chỉ vào lệnh với khối lượng chịu được mức sụt này."
        )

    def mk(
        tone: str, headline: str, steps: list[str], plan: bool = False, with_levels: bool = True
    ) -> NextStep:
        return NextStep(
            tone=tone,
            headline=headline,
            steps=steps,
            stop_price=round(stop_px, 2) if with_levels and stop_px else None,
            target_price=round(tgt_px, 2) if with_levels and tgt_px else None,
            review_sessions=main_h if with_levels else None,
            plan_cta=plan,
        )

    if sug == SUGGEST_INSUFFICIENT:
        if holding:
            return mk(
                WATCH,
                "Chưa đủ tiền lệ — giữ theo Kế hoạch nắm giữ hiện có.",
                ["Base rate không giúp gì thêm ở trạng thái này; dựa vào lý do nắm giữ ban đầu."],
                with_levels=False,
            )
        return mk(
            WATCH,
            "Chưa đủ tiền lệ để có lợi thế thống kê — không nên dựa vào base rate để mua.",
            [
                "Nếu vẫn muốn mua vì lý do riêng: lập Kế hoạch nắm giữ, Stop-loss tạm −10%.",
                "Thêm vào Watchlist — app báo khi trạng thái đổi.",
            ],
            plan=True,
            with_levels=False,
        )

    if sug == SUGGEST_LEAN_BUY:
        if holding:
            return mk(
                ACT,
                "Tiền lệ ủng hộ tiếp tục nắm giữ.",
                [*levels, *odds, "Không cần làm gì thêm nếu Kế hoạch nắm giữ đã có các mức trên."],
                plan=True,
            )
        if low_conf:
            return mk(
                WATCH,
                "Nghiêng mua nhưng ít tiền lệ — theo dõi, nếu mua thì chỉ mua thăm dò.",
                [
                    "Thêm vào Watchlist; chờ thêm xác nhận hoặc mua phần nhỏ.",
                    *levels,
                    *odds,
                    *risk,
                ],
                plan=True,
            )
        return mk(
            ACT,
            "Tiền lệ ủng hộ mua — có thể mua thăm dò kèm Kế hoạch nắm giữ.",
            [
                "Lập Kế hoạch nắm giữ trước khi đặt lệnh, chia 2 lần mua.",
                *levels,
                *odds,
                *risk,
            ],
            plan=True,
        )

    if sug == SUGGEST_LEAN_AVOID:
        if holding:
            tighten = (
                f"Siết Stop-loss gần hơn (vd ~{_price(close * 0.95)}, −5%) hoặc giảm tỉ trọng."
                if close
                else "Siết Stop-loss gần hơn hoặc giảm tỉ trọng."
            )
            return mk(
                CAUTION,
                "Tiền lệ bất lợi khi đang nắm — xem lại Kế hoạch nắm giữ.",
                [
                    tighten,
                    *risk,
                    "Nếu lý do nắm giữ ban đầu không còn đúng, cân nhắc bán.",
                ],
                plan=True,
                with_levels=False,
            )
        return mk(
            CAUTION,
            "Tiền lệ bất lợi — chưa nên mua.",
            [
                "Thêm vào Watchlist và chờ trạng thái đổi (app báo khi đổi).",
                *risk,
            ],
            with_levels=False,
        )

    # Neutral
    if holding:
        return mk(
            WATCH,
            "Không có lợi thế thống kê — giữ theo Kế hoạch nắm giữ, không mua thêm.",
            [*levels, *odds],
            plan=True,
        )
    return mk(
        WATCH,
        "Không có lợi thế thống kê — chưa cần hành động.",
        [
            "Thêm vào Watchlist — app báo khi trạng thái chuyển Bullish/Bearish.",
            *risk,
        ],
        with_levels=False,
    )
