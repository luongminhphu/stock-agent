"""Wave F1 — ReplayAgent hợp nhất: 2 entry point, 1 AI call, 1 module.

Trước F1 có 2 class ReplayAgent (ai/agents/replay.py và ai/agents/replay_agent.py)
với chữ ký khác nhau; bot scheduler gọi `.run(user_id, session)` không tồn tại
→ job replay đêm luôn fail. Test này khoá contract sau khi hợp nhất.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.ai.agents.replay import DecisionReplayResult, ReplayAgent
from src.ai.prompts.replay import ReplayContext
from src.ai.schemas.replay import OutcomeVerdict, ReplayOutput


def _output(**overrides: Any) -> ReplayOutput:
    base: dict[str, Any] = {
        "ticker": "HPG",
        "decision_date": "2026-09-01",
        "original_action": "SELL",
        "outcome_verdict": OutcomeVerdict.LOSS,
        "outcome_pnl_pct": -4.2,
        "what_went_right": [],
        "what_went_wrong": ["Bán khi chưa chạm stop"],
        "lessons": ["Giữ kỷ luật theo stop đã đặt"],
        "thesis_accuracy_note": "n/a",
        "pattern_tag": None,
        "exit_reason_assessment": "n/a",
        "confidence": 0.7,
        "summary": "Bán sớm do tâm lý.",
    }
    base.update(overrides)
    return ReplayOutput(**base)


def _ctx() -> ReplayContext:
    return ReplayContext(
        decision_id=7,
        thesis_id=3,
        ticker="HPG",
        decision_type="SELL",
        decision_at="2026-09-01 10:00",
        rationale="",
        price_at_decision=25000.0,
        thesis_score_at_decision=None,
        thesis_health_score_at_decision=None,
        active_signal=None,
        brief_summary=None,
        outcome_price=None,
        outcome_pnl_pct=-4.2,
        outcome_horizon_days=30,
    )


@pytest.mark.asyncio
async def test_analyze_returns_decision_result_via_single_ai_call() -> None:
    client = AsyncMock()
    client.chat = AsyncMock(return_value=_output())
    agent = ReplayAgent(ai_client=client)

    result = await agent.analyze(_ctx())

    assert isinstance(result, DecisionReplayResult)
    assert result.decision_id == 7
    assert result.key_lesson == "Giữ kỷ luật theo stop đã đặt"
    assert result.outcome_verdict == "LOSS"
    assert client.chat.await_count == 1
    kwargs = client.chat.await_args.kwargs
    assert kwargs["response_schema"] is ReplayOutput
    assert "ReplayOutput" in kwargs["system_prompt"] or "lessons" in kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_run_for_trade_builds_context_and_persists_lesson() -> None:
    client = AsyncMock()
    client.chat = AsyncMock(return_value=_output())
    agent = ReplayAgent(ai_client=client)

    with (
        patch("src.ai.memory.memory_service.MemoryService.log_interaction", new=AsyncMock()) as log,
        patch(
            "src.ai.memory.lesson_service.LessonService.persist_replay", new=AsyncMock()
        ) as persist,
    ):
        record = await agent.run_for_trade(
            session=AsyncMock(),
            user_id="u1",
            trade_snapshot={
                "id": 42,
                "ticker": "HPG",
                "traded_at": datetime.datetime(2026, 9, 1, 10, 0, tzinfo=datetime.UTC),
                "realized_pnl": -1000.0,
                "price": 25000.0,
                "exit_reason": "STOP_LOSS",
            },
            thesis_snapshot={"thesis_id": 3, "score": 61.0},
        )

    assert record is not None
    assert record.trade_id == 42
    assert record.user_id == "u1"
    assert log.await_count == 1
    # persist_replay được schedule qua create_task — đã được gọi (coroutine mock)
    assert persist.call_count == 1
    ctx_prompt = client.chat.await_args.kwargs["user_prompt"]
    assert "HPG" in ctx_prompt


@pytest.mark.asyncio
async def test_run_returns_none_when_ai_fails() -> None:
    client = AsyncMock()
    client.chat = AsyncMock(side_effect=RuntimeError("429"))
    agent = ReplayAgent(ai_client=client)

    assert await agent.analyze(_ctx()) is None
    assert await agent.run(AsyncMock(), _ctx(), user_id="u1", trade_id=1) is None


def test_legacy_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("src.ai.agents.replay_agent")


def test_build_replay_embed_accepts_envelopes() -> None:
    from src.bot.commands.decision_embeds import build_replay_embed
    from src.thesis.decision_service import DecisionReplayEnvelope

    env = DecisionReplayEnvelope(
        decision_id=1,
        ticker="HPG",
        outcome_verdict="CORRECT",
        replay=None,
        decision_type="BUY",
        outcome_pnl_pct=3.5,
    )
    embed = build_replay_embed([env], datetime.datetime.now(datetime.UTC))
    assert "HPG" in (embed.description or "")
    assert "+3.5%" in (embed.description or "")
