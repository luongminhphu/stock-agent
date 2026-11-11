"""/api/v1/plans — Kế hoạch nắm giữ: mẫu, gợi ý, tạo, liệt kê (đèn), kiểm tra."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.market.baserate import BaseRateReport, Outcome


def _report(t: str) -> BaseRateReport:
    o = Outcome(120, 10, 0.6, 0.6, 0.5, 0.1, 0.5, 0.7, 0.08, -0.1, 0.25, 0.02, -0.10)
    return BaseRateReport(
        ticker=t,
        as_of=date(2026, 11, 10),
        state={"valuation": "cheap", "trend": "below_both"},
        matched_on=("valuation",),
        dropped=(),
        matching_days=30,
        outcomes={120: o},
        analogs=[],
        suggestion="NGHIENG_MUA",
        confidence="trung_binh",
        history_from=None,
    )


class _BR:
    async def analyze(self, t):  # type: ignore[no-untyped-def]
        return _report(t)


class _Quotes:
    async def get_quote(self, t):  # type: ignore[no-untyped-def]
        return SimpleNamespace(ticker=t, settled_price=30_000.0)

    async def get_bulk_quotes(self, ts):  # type: ignore[no-untyped-def]
        return [SimpleNamespace(ticker=t, settled_price=26_000.0) for t in ts]


@pytest.fixture()
def overrides(app):  # type: ignore[no-untyped-def]
    from src.api.deps import get_baserate_service, get_current_user_id, get_quote_service

    app.dependency_overrides[get_baserate_service] = lambda: _BR()
    app.dependency_overrides[get_quote_service] = lambda: _Quotes()
    app.dependency_overrides[get_current_user_id] = lambda: "plan-user"
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_templates(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/plans/templates")
    assert r.status_code == 200 and {t["key"] for t in r.json()} >= {"gia_re", "xu_huong_len"}


@pytest.mark.asyncio
async def test_suggest_create_list_check(overrides, bootstrapped_client) -> None:  # type: ignore[no-untyped-def]
    c = bootstrapped_client
    r = await c.get("/api/v1/plans/suggest", params={"ticker": "hpg"})
    assert r.status_code == 200
    s = r.json()
    assert s["entry_price"] == 30_000 and s["stop_price"] == 26_400 and s["target_price"] == 32_400
    now = {t["key"]: t["now"] for t in s["templates"]}
    assert now["gia_re"] == "dung" and now["xu_huong_len"] == "sai"

    bad = await c.post(
        "/api/v1/plans", json={"ticker": "HPG", "entry_price": 30000, "stop_price": 31000}
    )
    assert bad.status_code == 422

    r = await c.post(
        "/api/v1/plans",
        json={
            "ticker": "hpg",
            "entry_price": 30_000,
            "stop_price": 26_400,
            "target_price": 32_400,
            "beliefs": [{"check_key": "gia_re"}, {"text": "Ngành thép hồi phục"}],
            "events": [{"text": "BCTC quý 4", "expected_date": "2027-01-20"}],
        },
    )
    assert r.status_code == 201, r.text

    r = await c.get("/api/v1/plans")
    assert r.status_code == 200
    [p] = r.json()
    assert p["ticker"] == "HPG" and p["title"] == "Kế hoạch nắm giữ HPG"
    assert p["light"] == "do", "giá 26.000 đã chạm Stop-loss 26.400"
    assert p["beliefs"][0] == {
        "text": "Giá đang rẻ so với lịch sử của chính mã",
        "check_key": "gia_re",
        "result": "dung",
    }
    assert p["beliefs"][1]["result"] == "tu_kiem"
    assert p["events"][0]["text"] == "BCTC quý 4"

    r = await c.post("/api/v1/plans/check")
    assert r.status_code == 200 and r.json()["changed"][0]["to"] == "do"


@pytest.mark.asyncio
async def test_explain_passes_computed_numbers_only(app, overrides, bootstrapped_client) -> None:  # type: ignore[no-untyped-def]
    from src.ai.agents.plan_explainer import PlanExplanation
    from src.api.deps import get_ai_client

    seen: dict = {}

    class _AI:
        async def chat(self, system_prompt, user_prompt, response_schema, **kw):  # type: ignore[no-untyped-def]
            seen["prompt"] = user_prompt
            return PlanExplanation(tom_tat="Kế hoạch đã sai vì giá chạm mức cắt lỗ.")

    app.dependency_overrides[get_ai_client] = lambda: _AI()
    c = bootstrapped_client
    r = await c.post(
        "/api/v1/plans", json={"ticker": "VNM", "entry_price": 30000, "stop_price": 27000}
    )
    pid = r.json()["id"]
    r = await c.post(f"/api/v1/plans/{pid}/explain")
    assert r.status_code == 200 and r.json()["tom_tat"].startswith("Kế hoạch đã sai")
    assert '"light": "do"' in seen["prompt"] and "xac_suat_lai" in seen["prompt"]
    assert (await c.post("/api/v1/plans/999999/explain")).status_code == 404
