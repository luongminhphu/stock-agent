"""Kế hoạch nắm giữ — cách đọc một thesis dành cho người không làm tài chính.

Owner: thesis segment. Pure functions, không I/O, không AI.

Một "kế hoạch" (bảng `theses`) trả lời 4 câu hỏi:
    1. Vì sao tôi mua?          → "điều tôi tin" (bảng `assumptions`). Mỗi điều có thể gắn
                                  một mẫu kiểm tra tự động (check_key) — hệ thống tự đối
                                  chiếu với số liệu hằng ngày; điều tự ghi thì người dùng tự kiểm.
    2. Khi nào tôi sai?         → Stop-loss (`stop_loss`).
    3. Chốt lời ở đâu?          → Target (`target_price`).
    4. Khi nào xem lại?         → hạn xem lại (`review_by`, mặc định 6 tháng).

Thay cho điểm 0–100 là MỘT đèn trạng thái + một dòng tóm tắt:
    xanh  — đúng kế hoạch
    vàng  — cần xem lại (một điều tôi tin không còn đúng, sát mức sai, tới mục tiêu, tới hạn)
    đỏ    — kế hoạch đã sai (chạm mức chấp nhận sai, hoặc phần lớn điều tôi tin đã sai)
    xám   — đã đóng
Mọi lý do đều ghi rõ bằng tiếng Việt để người dùng biết vì sao đèn đổi màu.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

LIGHT_GREEN = "xanh"
LIGHT_YELLOW = "vang"
LIGHT_RED = "do"
LIGHT_GREY = "xam"

DEFAULT_REVIEW_DAYS = 182  # ~6 tháng
NEAR_STOP_PCT = 3.0  # cách mức chấp nhận sai < 3% → nhắc
REVIEW_SOON_DAYS = 7

BELIEF_TRUE = "dung"
BELIEF_FALSE = "sai"
BELIEF_UNKNOWN = "chua_ro"
BELIEF_MANUAL = "tu_kiem"  # điều tự ghi — hệ thống không kiểm được

# ── Mẫu "điều tôi tin" kiểm được bằng số liệu ───────────────────────────────
# check(state) → True / False / None (chưa rõ). `state` là trạng thái của
# market.baserate (trend, range_52w, momentum_3m, volume, valuation, earnings).


def _map(key: str, true: set[str], false: set[str]) -> Callable[[Mapping[str, str]], bool | None]:
    def check(state: Mapping[str, str]) -> bool | None:
        v = state.get(key)
        if v in true:
            return True
        if v in false:
            return False
        return None

    return check


@dataclass(frozen=True)
class BeliefTemplate:
    key: str
    label: str
    explain: str  # giải thích 1 câu cho người không làm tài chính
    check: Callable[[Mapping[str, str]], bool | None]


BELIEF_TEMPLATES: dict[str, BeliefTemplate] = {
    t.key: t
    for t in (
        BeliefTemplate(
            "gia_re",
            "Giá đang rẻ so với lịch sử của chính mã",
            "P/E (giá so với lợi nhuận) nằm trong 1/3 thấp nhất các mức mã từng có.",
            _map("valuation", {"cheap"}, {"expensive"}),
        ),
        BeliefTemplate(
            "loi_nhuan_tang",
            "Lợi nhuận công ty đang tăng",
            "Lợi nhuận mỗi cổ phiếu (EPS) quý gần nhất cao hơn cùng kỳ năm trước.",
            _map("earnings", {"growing", "strong"}, {"declining"}),
        ),
        BeliefTemplate(
            "xu_huong_len",
            "Giá đang trong xu hướng đi lên",
            "Giá nằm trên cả đường trung bình 50 phiên và 200 phiên.",
            _map("trend", {"above_both"}, {"below_both"}),
        ),
        BeliefTemplate(
            "chua_qua_nong",
            "Giá chưa tăng quá nóng",
            "Giá chưa nằm ở vùng cao nhất của biên độ 52 tuần.",
            _map("range_52w", {"low", "mid"}, {"high"}),
        ),
        BeliefTemplate(
            "dong_tien_vao",
            "Dòng tiền đang quan tâm mã này",
            "Khối lượng giao dịch 20 phiên gần đây cao hơn mức thường ngày.",
            _map("volume", {"heavy"}, {"dry"}),
        ),
    )
}


def check_belief(check_key: str | None, state: Mapping[str, str] | None) -> str:
    """Kết quả tự kiểm một điều tôi tin: dung / sai / chua_ro / tu_kiem."""
    if not check_key or check_key not in BELIEF_TEMPLATES:
        return BELIEF_MANUAL
    if not state:
        return BELIEF_UNKNOWN
    r = BELIEF_TEMPLATES[check_key].check(state)
    return BELIEF_TRUE if r is True else BELIEF_FALSE if r is False else BELIEF_UNKNOWN


# ── Đánh giá kế hoạch ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BeliefInput:
    text: str
    check_key: str | None = None
    manual_status: str | None = None  # valid / invalid / uncertain … (điều tự ghi)


@dataclass
class BeliefResult:
    text: str
    check_key: str | None
    result: str  # dung / sai / chua_ro / tu_kiem


@dataclass
class PlanEvaluation:
    light: str
    reasons: list[str] = field(default_factory=list)
    beliefs: list[BeliefResult] = field(default_factory=list)
    beliefs_ok: int = 0
    beliefs_checked: int = 0
    pnl_pct: float | None = None
    to_stop_pct: float | None = None  # còn cách mức chấp nhận sai bao nhiêu % (dương = còn xa)
    to_target_pct: float | None = None
    days_left: int | None = None
    summary: str = ""


def _vnd(x: float) -> str:
    """28400 → "28.400" (kiểu số Việt Nam)."""
    return f"{x:,.0f}".replace(",", ".")


def _pct(x: float, sign: bool = False) -> str:
    """-14.26 → "-14,3" (dấu phẩy thập phân)."""
    return (f"{x:+.1f}" if sign else f"{x:.1f}").replace(".", ",")


_MANUAL_OK = {"valid"}
_MANUAL_BAD = {"invalid"}


def evaluate_plan(
    *,
    closed: bool,
    entry_price: float | None,
    stop_price: float | None,
    target_price: float | None,
    review_by: date | None,
    beliefs: list[BeliefInput],
    price: float | None,
    today: date,
    state: Mapping[str, str] | None = None,
) -> PlanEvaluation:
    ev = PlanEvaluation(light=LIGHT_GREEN)
    for b in beliefs:
        res = check_belief(b.check_key, state)
        if res == BELIEF_MANUAL and b.manual_status:
            ms = str(b.manual_status).lower()
            res = BELIEF_TRUE if ms in _MANUAL_OK else BELIEF_FALSE if ms in _MANUAL_BAD else res
        ev.beliefs.append(BeliefResult(b.text, b.check_key, res))
    decided = [r for r in ev.beliefs if r.result in (BELIEF_TRUE, BELIEF_FALSE)]
    ev.beliefs_checked = len(decided)
    ev.beliefs_ok = sum(1 for r in decided if r.result == BELIEF_TRUE)
    wrong = [r for r in decided if r.result == BELIEF_FALSE]

    if price and entry_price:
        ev.pnl_pct = round((price / entry_price - 1) * 100, 2)
    if price and stop_price:
        ev.to_stop_pct = round((price / stop_price - 1) * 100, 2)
    if price and target_price:
        ev.to_target_pct = round((target_price / price - 1) * 100, 2)
    if review_by is not None:
        ev.days_left = (review_by - today).days

    if closed:
        ev.light = LIGHT_GREY
        ev.reasons.append("Kế hoạch đã đóng.")
        ev.summary = _summary(ev)
        return ev

    red: list[str] = []
    yellow: list[str] = []
    if price and stop_price and price <= stop_price:
        red.append(f"Giá {_vnd(price)} đã chạm Stop-loss {_vnd(stop_price)}.")
    elif ev.to_stop_pct is not None and ev.to_stop_pct < NEAR_STOP_PCT:
        yellow.append(f"Giá chỉ còn cách Stop-loss {_pct(ev.to_stop_pct)}%.")
    if decided and len(wrong) * 2 > len(decided):
        red.append(f"{len(wrong)}/{len(decided)} điều tôi tin đã không còn đúng.")
    elif wrong:
        yellow.append("Không còn đúng: " + "; ".join(w.text for w in wrong) + ".")
    if price and target_price and price >= target_price:
        yellow.append(f"Giá đã tới Target {_vnd(target_price)} — chốt lời hay nâng Target?")
    if ev.days_left is not None:
        if ev.days_left <= 0:
            yellow.append("Đã tới hạn xem lại kế hoạch.")
        elif ev.days_left <= REVIEW_SOON_DAYS:
            yellow.append(f"Còn {ev.days_left} ngày tới hạn xem lại.")
    if stop_price is None:
        yellow.append("Chưa đặt Stop-loss — nên đặt để biết khi nào mình sai.")

    if red:
        ev.light, ev.reasons = LIGHT_RED, red + yellow
    elif yellow:
        ev.light, ev.reasons = LIGHT_YELLOW, yellow
    else:
        ev.reasons = ["Mọi thứ vẫn đúng như kế hoạch."]
    ev.summary = _summary(ev)
    return ev


def _summary(ev: PlanEvaluation) -> str:
    parts: list[str] = []
    if ev.beliefs:
        if ev.beliefs_checked:
            parts.append(f"{ev.beliefs_ok}/{ev.beliefs_checked} điều tôi tin còn đúng")
        else:
            parts.append(f"{len(ev.beliefs)} điều tôi tin chưa kiểm được")
    if ev.pnl_pct is not None:
        parts.append(f"P&L {_pct(ev.pnl_pct, sign=True)}%")
    if ev.to_stop_pct is not None and ev.to_stop_pct > 0:
        parts.append(f"cách Stop-loss {_pct(ev.to_stop_pct)}%")
    if ev.days_left is not None and ev.days_left > 0:
        parts.append(f"còn {ev.days_left} ngày tới hạn xem lại")
    return " · ".join(parts)


# ── Gợi ý mức từ tiền lệ ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class LevelSuggestion:
    stop_price: float | None
    target_price: float | None
    review_by: date
    stop_note: str
    target_note: str


def suggest_levels(
    entry_price: float,
    today: date,
    *,
    typical_drawdown: float | None = None,  # vd -0.12: các lần trước thường sụt 12% trong kỳ
    median_return: float | None = None,  # vd 0.08: trung vị lợi nhuận sau 6 tháng
    p90_return: float | None = None,
    round_to: Callable[[float], float] = lambda x: round(x, -1),
) -> LevelSuggestion:
    """Gợi ý mức chấp nhận sai / chốt lời từ tiền lệ của chính mã.

    - Mức chấp nhận sai đặt sâu hơn mức sụt điển hình 20% (để không bị văng ra bởi một
      nhịp dao động bình thường); không có tiền lệ → −10%.
    - Mục tiêu = trung vị lợi nhuận của tiền lệ (thực tế, không lạc quan); trung vị ≤ 0
      → dùng mức 90% tốt nhất nhưng ghi chú rõ; không có tiền lệ → bỏ trống.
    """
    if typical_drawdown is not None and typical_drawdown < 0:
        dd = typical_drawdown * 1.2
        stop = round_to(entry_price * (1 + dd))
        stop_note = (
            f"Các lần trước mã có max drawdown trung bình {abs(typical_drawdown) * 100:.0f}% khi nắm giữ; "
            f"đặt sâu hơn một chút ({abs(dd) * 100:.0f}%) để không bị văng ra vì dao động bình thường."
        )
    else:
        stop = round_to(entry_price * 0.9)
        stop_note = "Chưa đủ base rate — tạm đặt Stop-loss thấp hơn Entry 10%."
    target: float | None = None
    if median_return is not None and median_return > 0:
        target = round_to(entry_price * (1 + median_return))
        target_note = f"Một nửa số lần trước lãi ít nhất {median_return * 100:.0f}% sau 6 tháng."
    elif p90_return is not None and p90_return > 0:
        target = round_to(entry_price * (1 + p90_return))
        target_note = (
            "Base rate cho thấy đa số lần không lãi sau 6 tháng; Target này chỉ đạt ở 10% trường hợp "
            "tốt nhất — cân nhắc kỹ trước khi mua."
        )
    else:
        target_note = "Chưa đủ base rate để gợi ý Target."
    return LevelSuggestion(
        stop_price=stop,
        target_price=target,
        review_by=today + timedelta(days=DEFAULT_REVIEW_DAYS),
        stop_note=stop_note,
        target_note=target_note,
    )
