"""HostGuard: token bucket + tạm nghỉ host khi bị 429/403."""

from __future__ import annotations

import httpx
import pytest

from src.market.adapters import http_guard
from src.market.adapters.http_guard import HostBlockedError, HostGuard, guarded_request


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_block_on_429_and_recover() -> None:
    clk = _Clock()
    g = HostGuard("x", cooldown=60, clock=clk)
    g.check()
    g.report(200)
    g.check()
    g.report(429, retry_after="120")
    with pytest.raises(HostBlockedError):
        g.check()
    assert g.blocked_for == pytest.approx(120)
    clk.t += 121
    g.check()


@pytest.mark.anyio
async def test_token_bucket_allows_burst_then_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    clk = _Clock()
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clk.t += s

    monkeypatch.setattr(http_guard.asyncio, "sleep", fake_sleep)
    g = HostGuard("x", rate=2.0, burst=3, clock=clk)
    for _ in range(3):
        await g.acquire()
    assert slept == []
    await g.acquire()
    assert slept and slept[0] == pytest.approx(0.5)


@pytest.mark.anyio
async def test_guarded_request_blocks_host_after_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_guard, "_guards", {})
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403)

    async with httpx.AsyncClient(
        base_url="https://blocked.example/", transport=httpx.MockTransport(handler)
    ) as client:
        r = await guarded_request(client, "GET", "x")
        assert r.status_code == 403
        with pytest.raises(HostBlockedError):
            await guarded_request(client, "GET", "x")
    assert calls == 1, "đang tạm nghỉ thì không chạm mạng"
