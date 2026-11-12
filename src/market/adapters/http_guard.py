"""HostGuard — giới hạn tốc độ + tạm nghỉ theo từng host nguồn dữ liệu.

Owner: market segment.

Vì sao:
    API bảng giá của CTCK (VCI, KBS, VNDirect) không công bố hạn mức nhưng chặn IP gọi
    dồn (429/403). Mọi adapter đi qua một guard dùng chung theo host:
      - Token bucket: tối đa `rate` request/giây, cho phép dồn `burst` request.
      - Khi host trả 429/403: tạm nghỉ host đó `cooldown` giây (tôn trọng Retry-After).
        Trong thời gian nghỉ, request bị từ chối NGAY (HostBlockedError, không chạm mạng)
        để ChainedAdapter chuyển sang nguồn kế tiếp thay vì gọi tiếp và bị chặn lâu hơn.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import httpx

from src.platform.logging import get_logger

logger = get_logger(__name__)

BLOCK_STATUSES = frozenset({403, 429})
DEFAULT_COOLDOWN = 300.0  # 5 phút
MAX_COOLDOWN = 3600.0


class HostBlockedError(RuntimeError):
    """Host đang trong thời gian tạm nghỉ sau khi bị giới hạn/chặn."""


class HostGuard:
    def __init__(
        self,
        host: str,
        rate: float = 2.0,
        burst: int = 4,
        cooldown: float = DEFAULT_COOLDOWN,
        clock: Any = time.monotonic,
    ) -> None:
        self.host = host
        self._rate = rate
        self._burst = float(burst)
        self._cooldown = cooldown
        self._clock = clock
        self._tokens = float(burst)
        self._updated = clock()
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()
        self.blocked_count = 0

    @property
    def blocked_for(self) -> float:
        return max(0.0, self._blocked_until - self._clock())

    def check(self) -> None:
        remaining = self.blocked_for
        if remaining > 0:
            raise HostBlockedError(f"{self.host} đang tạm nghỉ thêm {remaining:.0f}s")

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self._tokens = min(self._burst, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self._rate)

    def report(self, status: int, retry_after: str | None = None) -> None:
        if status not in BLOCK_STATUSES:
            return
        wait = self._cooldown
        if retry_after:
            with contextlib.suppress(ValueError):
                wait = min(MAX_COOLDOWN, max(wait, float(retry_after)))
        self._blocked_until = self._clock() + wait
        self.blocked_count += 1
        logger.warning("http_guard.host_blocked", host=self.host, status=status, cooldown_s=wait)


# Hạn mức mặc định theo host (request/giây, burst)
_LIMITS: dict[str, tuple[float, int]] = {
    "trading.vietcap.com.vn": (3.0, 5),
    "iq.vietcap.com.vn": (1.0, 3),  # dữ liệu cơ bản — mỗi mã 1 lần / 12 giờ
    "kbbuddywts.kbsec.com.vn": (2.0, 4),
    "finfo-api.vndirect.com.vn": (1.0, 2),
}
_guards: dict[str, HostGuard] = {}


def get_guard(host: str) -> HostGuard:
    g = _guards.get(host)
    if g is None:
        rate, burst = _LIMITS.get(host, (2.0, 4))
        g = _guards[host] = HostGuard(host, rate=rate, burst=burst)
    return g


def guard_status() -> dict[str, dict[str, float]]:
    """Trạng thái guard — cho health check / debug."""
    return {
        h: {"blocked_for_s": round(g.blocked_for, 1), "blocked_count": g.blocked_count}
        for h, g in _guards.items()
    }


async def guarded_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """client.request() đi qua HostGuard của host đích."""
    full = client.build_request(method, url, **kwargs).url
    guard = get_guard(full.host)
    guard.check()
    await guard.acquire()
    response = await client.request(method, url, **kwargs)
    guard.report(response.status_code, response.headers.get("retry-after"))
    return response
