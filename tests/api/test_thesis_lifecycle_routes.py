"""T1 — POST /thesis/{id}/reactivate và thông điệp 409 đọc được khi sửa thesis đã đóng."""

from __future__ import annotations

import pytest

_BODY = {
    "ticker": "HPG",
    "title": "Thép hồi phục",
    "entry_price": 25000,
    "target_price": 31000,
    "stop_loss": 22000,
}


async def _create(client, **override):
    r = await client.post("/api/v1/thesis", json={**_BODY, **override})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_patch_closed_thesis_returns_readable_409(bootstrapped_client):
    tid = await _create(bootstrapped_client)
    assert (await bootstrapped_client.post(f"/api/v1/thesis/{tid}/close")).status_code == 200
    r = await bootstrapped_client.patch(f"/api/v1/thesis/{tid}", json={"title": "x"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail != str(tid) and "mở lại" in detail and f"#{tid}" in detail


@pytest.mark.asyncio
async def test_reactivate_closed_thesis_then_patch_ok(bootstrapped_client):
    tid = await _create(bootstrapped_client)
    await bootstrapped_client.post(f"/api/v1/thesis/{tid}/close")
    r = await bootstrapped_client.post(f"/api/v1/thesis/{tid}/reactivate")
    assert r.status_code == 200 and r.json()["status"] == "active"
    r = await bootstrapped_client.patch(f"/api/v1/thesis/{tid}", json={"title": "Sửa sau mở lại"})
    assert r.status_code == 200 and r.json()["title"] == "Sửa sau mở lại"


@pytest.mark.asyncio
async def test_reactivate_conflict_when_other_active_same_ticker(bootstrapped_client):
    # Tạo cả hai trước rồi mới đóng: tránh race với background task của ThesisClosedEvent
    # trên sqlite StaticPool trong test (không liên quan hành vi production).
    old = await _create(bootstrapped_client, title="Cũ")
    new = await _create(bootstrapped_client, title="Mới")
    assert (await bootstrapped_client.post(f"/api/v1/thesis/{old}/close")).status_code == 200
    r = await bootstrapped_client.post(f"/api/v1/thesis/{old}/reactivate")
    assert r.status_code == 409 and f"#{new}" in r.json()["detail"]


@pytest.mark.asyncio
async def test_reactivate_not_found(bootstrapped_client):
    r = await bootstrapped_client.post("/api/v1/thesis/999999/reactivate")
    assert r.status_code == 404
