"""Embed tổng hợp kế hoạch nắm giữ: đỏ trước, có lý do + tóm tắt."""

from __future__ import annotations

import datetime

from src.bot.commands.thesis_embeds import build_plan_digest_embed
from src.thesis.plan_service import PlanChange


def test_digest_orders_red_first_and_shows_reasons() -> None:
    changes = [
        PlanChange(
            1,
            "u",
            "VNM",
            "VNM",
            "xanh",
            "vang",
            "1/2 điều tôi tin còn đúng",
            ["Còn 5 ngày tới hạn xem lại."],
        ),
        PlanChange(
            2,
            "u",
            "HPG",
            "HPG",
            "xanh",
            "do",
            "lãi/lỗ -12.0%",
            ["Giá 26,000 đã chạm mức chấp nhận sai 26,400."],
        ),
    ]
    e = build_plan_digest_embed(
        changes, datetime.datetime(2026, 11, 11, 8, 30, tzinfo=datetime.UTC)
    )
    assert e.fields[0].name.startswith("🔴 HPG — Kế hoạch đã sai (trước: Đúng kế hoạch)")
    assert "chạm mức chấp nhận sai" in e.fields[0].value
    assert e.fields[1].name.startswith("🟡 VNM")
    assert e.colour.value == 0xDC2626
