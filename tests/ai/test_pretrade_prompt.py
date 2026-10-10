"""P1 — prompt pretrade phải khớp PreTradeCheckOutput và mang lệnh dự kiến."""

from __future__ import annotations

from src.ai.prompts.pretrade import SYSTEM_PROMPT, build_pretrade_prompt
from src.ai.schemas import PreTradeCheckOutput


def test_system_prompt_only_references_real_schema_fields() -> None:
    # Bản cũ hướng dẫn decision GO/AVOID/WAIT, resolution_path, risk_flags —
    # không có trong schema → AI điền sai field, output bị validator bỏ qua.
    for stale in ("resolution_path", "risk_flags", "decision = GO", "decision = AVOID"):
        assert stale not in SYSTEM_PROMPT
    for field in (
        "intended_action",
        "proceed_recommendation",
        "blocking_issues",
        "resolution_steps",
    ):
        assert field in SYSTEM_PROMPT
        assert field in PreTradeCheckOutput.model_fields


def test_user_prompt_carries_intended_action_quantity_and_position() -> None:
    p = build_pretrade_prompt(
        "HPG",
        27_500,
        -1.2,
        "",
        "",
        "",
        intended_action="SELL",
        quantity=1000,
        portfolio_context="Vị thế đang mở: 3,000 cp @ giá vốn 25,000",
    )
    assert "=== LỆNH DỰ KIẾN ===" in p and "Hành động: SELL" in p
    assert "Khối lượng dự kiến: 1,000 cp" in p
    assert "3,000 cp" in p
    assert 'intended_action = "SELL"' in p


def test_user_prompt_defaults_to_buy_without_position() -> None:
    p = build_pretrade_prompt("VCB", 90_000, 0.5, "", "", "")
    assert "Hành động: BUY" in p
    assert "Không có vị thế VCB" in p
    assert "Khối lượng dự kiến: chưa rõ" in p
