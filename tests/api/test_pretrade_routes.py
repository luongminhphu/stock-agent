"""P1b — POST /api/v1/pretrade/check: adapter mỏng quanh thesis.PreTradeService."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.ai.schemas import PreTradeCheckOutput


def _output(action: str = "SELL", proceed: bool = False) -> PreTradeCheckOutput:
    return PreTradeCheckOutput(
        ticker="HPG",
        intended_action=action,
        alignment="MISALIGNED",
        verdict="BULLISH",
        confidence=0.7,
        proceed_recommendation=proceed,
        blocking_issues=["Không có vị thế HPG đang mở trong danh mục"],
        resolution_steps=[],
        risk_summary="r",
        summary="Không nên bán.",
    )


@pytest.mark.asyncio
async def test_pretrade_check_passes_action_and_quantity(bootstrapped_client):
    with patch("src.thesis.pretrade_service.PreTradeService.check", new=AsyncMock()) as chk:
        chk.return_value = _output()
        r = await bootstrapped_client.post(
            "/api/v1/pretrade/check",
            json={"ticker": "hpg", "intended_action": "SELL", "quantity": 1000},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["intended_action"] == "SELL" and body["proceed_recommendation"] is False
        assert body["blocking_issues"]
        kw = chk.await_args.kwargs
        assert kw["ticker"] == "hpg" and kw["intended_action"] == "SELL" and kw["quantity"] == 1000


@pytest.mark.asyncio
async def test_pretrade_check_rejects_bad_action(bootstrapped_client):
    r = await bootstrapped_client.post(
        "/api/v1/pretrade/check", json={"ticker": "HPG", "intended_action": "SHORT"}
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_pretrade_check_service_error_is_503(bootstrapped_client):
    with patch(
        "src.thesis.pretrade_service.PreTradeService.check",
        new=AsyncMock(side_effect=RuntimeError("ai down")),
    ):
        r = await bootstrapped_client.post("/api/v1/pretrade/check", json={"ticker": "HPG"})
        assert r.status_code == 503 and "HPG" in r.json()["detail"]


@pytest.mark.asyncio
async def test_pretrade_check_returns_advice_id(bootstrapped_client):
    async def _fake_check(self, **kwargs):  # noqa: ANN001, ANN003
        self.last_advice_id = 123
        return _output()

    with patch("src.thesis.pretrade_service.PreTradeService.check", new=_fake_check):
        r = await bootstrapped_client.post("/api/v1/pretrade/check", json={"ticker": "HPG"})
        assert r.status_code == 200 and r.json()["advice_id"] == 123
