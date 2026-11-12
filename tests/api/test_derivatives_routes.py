"""GET /api/v1/derivatives/* — adapter mỏng trên market.derivatives."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.market.test_derivatives import _Bar, _FakeKBS, _FakeOHLCV


@pytest.fixture()
def fake_svc(app):  # type: ignore[no-untyped-def]
    from src.api.deps import get_current_user_id, get_derivatives_service
    from src.market.derivatives import DerivativesService
    from src.market.ohlcv_service import _today_ict

    today = _today_ict()
    days = [today - timedelta(days=i) for i in range(400, -1, -1)]
    vn30 = [_Bar(d, 1300 + (i % 9)) for i, d in enumerate(days)]
    svc = DerivativesService(_FakeOHLCV({"VN30": vn30, "VN30F1M": vn30}), _FakeKBS())
    app.dependency_overrides[get_derivatives_service] = lambda: svc
    app.dependency_overrides[get_current_user_id] = lambda: "nobody"
    yield svc
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_contracts(fake_svc, client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/derivatives/contracts")
    assert r.status_code == 200
    body = r.json()
    assert [c["series"] for c in body["contracts"]] == ["F1M", "F2M", "F1Q", "F2Q"]
    assert body["contracts"][0]["price"] == 1305.0
    assert body["contracts"][0]["krx"].startswith("41I1")


@pytest.mark.asyncio
async def test_basis_and_vn30_baserate(fake_svc, client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/derivatives/basis?days=200")
    assert r.status_code == 200 and r.json()["median"] == 0.0
    r = await client.get("/api/v1/derivatives/vn30/baserate")
    assert r.status_code == 200
    assert [o["horizon"] for o in r.json()["outcomes"]] == [5, 20, 60]


@pytest.mark.asyncio
async def test_position_preview(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get(
        "/api/v1/derivatives/position-preview",
        params={"code": "VN30F2612", "side": "SHORT", "qty": 2, "entry": 1300, "mark": 1290},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["unrealized_pnl"] == 10 * 100_000 * 2
    assert body["expiry"] == "2026-12-17"


@pytest.mark.asyncio
async def test_hedge_without_positions_404(fake_svc, bootstrapped_client) -> None:  # type: ignore[no-untyped-def]
    r = await bootstrapped_client.get("/api/v1/derivatives/hedge")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_position_book_open_list_close(fake_svc, bootstrapped_client) -> None:  # type: ignore[no-untyped-def]
    from src.market.derivatives import active_contracts
    from src.market.ohlcv_service import _today_ict

    c = bootstrapped_client
    code = active_contracts(_today_ict())[0].code
    r = await c.post(
        "/api/v1/derivatives/positions",
        json={
            "contract_code": code,
            "side": "SHORT",
            "qty": 2,
            "entry_price": 1310,
            "purpose": "hedge",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    bad = await c.post(
        "/api/v1/derivatives/positions",
        json={"contract_code": code, "side": "BUY", "qty": 1, "entry_price": 1},
    )
    assert bad.status_code == 422

    r = await c.get("/api/v1/derivatives/positions")
    body = r.json()
    [p] = body["positions"]
    # _FakeKBS trả giá 1305 cho F1M → Short 2 HĐ từ 1310 lãi 5 điểm × 100.000 × 2
    assert p["mark_price"] == 1305.0 and p["unrealized_pnl"] == 1_000_000
    assert body["open_margin"] > 0

    r = await c.post(f"/api/v1/derivatives/positions/{pid}/close", json={"exit_price": 1300})
    assert r.json()["realized_pnl"] == 2_000_000
    again = await c.post(f"/api/v1/derivatives/positions/{pid}/close", json={"exit_price": 1300})
    assert again.status_code == 422
    r = await c.get("/api/v1/derivatives/positions", params={"include_closed": True})
    assert (
        r.json()["realized_pnl"] == 2_000_000 and r.json()["positions"][0]["unrealized_pnl"] is None
    )


@pytest.mark.asyncio
async def test_sim_config_and_view(fake_svc, client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/derivatives/sim")
    assert r.status_code == 200
    body = r.json()
    assert body["config"]["active"] is False and body["primary"] == "base"
    assert set(body["books"]) == {"base", "fade"}

    r = await client.put(
        "/api/v1/derivatives/sim",
        json={"series": "F2M", "qty": 3, "mode": "auto", "stop_atr": 0.6, "target_points": None},
    )
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert (cfg["series"], cfg["qty"], cfg["mode"], cfg["stop_atr"]) == ("F2M", 3, "auto", 0.6)
    assert cfg["target_points"] is None and cfg["stop_points"] == 15.0

    assert (await client.put("/api/v1/derivatives/sim", json={"mode": "x"})).status_code == 422
    r = await client.post("/api/v1/derivatives/sim/reset")
    assert r.status_code == 200 and r.json()["trades"] == []
