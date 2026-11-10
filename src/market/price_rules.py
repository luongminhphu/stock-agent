"""Quy tắc giá của sở giao dịch — bước giá + giờ chốt phiên theo từng sàn.

Owner: market segment. Pure functions, không I/O.

Vì sao cần:
    Giá hiển thị trên bảng giá của mọi CTCK (SSI, VPS, VCI, KBS...) đều là giá
    *khớp lệnh do sở công bố* — luôn nằm trên lưới bước giá và trong biên
    trần/sàn. Nguồn dữ liệu bên thứ ba đôi khi trả giá đã điều chỉnh, giá bình
    quân, hoặc sai đơn vị (nghìn đồng). Chuẩn hoá về lưới bước giá + kiểm tra biên
    là cách rẻ nhất để phát hiện dữ liệu lệch trước khi nó vào P&L / outcome.

Bước giá cổ phiếu (đơn vị VND):
    HOSE:        < 10.000 → 10 · 10.000–49.950 → 50 · ≥ 50.000 → 100
    HNX, UPCoM:  100

Giờ chốt giá đóng cửa (ICT):
    HOSE  — ATC 14:30–14:45 → giá đóng cửa có sau 14:45
    HNX   — ATC 14:30–14:45 (14:45–15:00 chỉ còn PLO, không đổi giá đóng cửa)
    UPCoM — không có ATC, khớp liên tục tới 15:00 → chỉ chốt được sau 15:00
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

_ICT = timezone(timedelta(hours=7))

_HOSE = "HOSE"
_HNX = "HNX"
_UPCOM = "UPCOM"

_SESSION_CLOSE: dict[str, time] = {
    _HOSE: time(14, 45),
    _HNX: time(14, 45),
    _UPCOM: time(15, 0),
}
# Mã chưa rõ sàn: dùng giờ muộn nhất để không bao giờ chốt sớm.
_DEFAULT_SESSION_CLOSE = time(15, 0)


def normalize_exchange(exchange: object) -> str:
    """'HSX'/'hose'/Exchange.HOSE → 'HOSE'; giá trị lạ → chuỗi upper nguyên trạng."""
    raw = getattr(exchange, "value", exchange)
    code = str(raw or "").strip().upper()
    return _HOSE if code in {"HSX", "HOSE"} else code


def tick_size(price: float, exchange: object) -> float:
    """Bước giá cổ phiếu tại mức `price` trên sàn `exchange`."""
    if normalize_exchange(exchange) == _HOSE:
        if price < 10_000:
            return 10.0
        if price < 50_000:
            return 50.0
        return 100.0
    return 100.0


def snap_to_tick(price: float, exchange: object) -> float:
    """Làm tròn `price` về bước giá gần nhất. Giá <= 0 giữ nguyên."""
    if price <= 0:
        return price
    step = tick_size(price, exchange)
    return round(round(price / step) * step, 2)


def is_on_tick(price: float, exchange: object) -> bool:
    if price <= 0:
        return True
    return abs(snap_to_tick(price, exchange) - price) < 1e-6


def prices_match(a: float, b: float, exchange: object) -> bool:
    """Hai giá được coi là trùng nếu lệch dưới nửa bước giá."""
    ref = max(a, b)
    return abs(a - b) < tick_size(ref, exchange) / 2


def session_close_time(exchange: object) -> time:
    return _SESSION_CLOSE.get(normalize_exchange(exchange), _DEFAULT_SESSION_CLOSE)


def is_session_closed(exchange: object, now: datetime | None = None) -> bool:
    """True nếu giá đóng cửa của sàn đã chốt ở thời điểm `now` (theo giờ ICT).

    Chỉ xét giờ trong ngày — caller tự kiểm tra ngày giao dịch.
    """
    t = (now or datetime.now(_ICT)).astimezone(_ICT).time()
    return t >= session_close_time(exchange)
