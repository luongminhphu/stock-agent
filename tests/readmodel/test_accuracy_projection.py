"""Wave E3c — AccuracyProjection đọc ledger + decision_logs adherence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.ai.memory.user_behavior_log import UserBehaviorLog
from src.platform.db import AsyncSessionLocal
from src.readmodel.accuracy_projection import AccuracyProjection
from src.thesis.models import DecisionLog, OutcomeVerdict


def _advice(user_id: str, adherence: str | None, verdict: OutcomeVerdict | None) -> DecisionLog:
    return DecisionLog(
        thesis_id=None,
        user_id=user_id,
        ticker="HPG",
        decision_type="PRETRADE_ADVICE",
        decision_at=datetime.now(UTC) - timedelta(days=1),
        active_signal="BULLISH",
        rationale="t",
        adherence=adherence,
        outcome_verdict=verdict,
    )


@pytest.mark.anyio
async def test_empty_user_returns_stable_shape() -> None:
    async with AsyncSessionLocal() as s:
        out = await AccuracyProjection(s).get("nobody", days=7)
    assert out["days"] == 7
    assert out["by_source"]["core"] == {
        "total": 0,
        "acted": 0,
        "rejected": 0,
        "not_acted": 0,
        "acted_rate": None,
    }
    assert out["by_source"]["pretrade"]["follow_rate"] is None and out["trend"] == []


@pytest.mark.anyio
async def test_counts_by_source_and_pretrade_hit_rates() -> None:
    u = "u-acc"
    async with AsyncSessionLocal() as s:
        s.add_all(
            [
                UserBehaviorLog(
                    user_id=u, signal="engine:acted", source="core", ref_type="verdict", ref_id="a"
                ),
                UserBehaviorLog(
                    user_id=u,
                    signal="engine:rejected",
                    source="core",
                    ref_type="verdict",
                    ref_id="b",
                ),
                UserBehaviorLog(
                    user_id=u, signal="brief:acted", source="briefing", ref_type="brief", ref_id="1"
                ),
                UserBehaviorLog(
                    user_id=u,
                    signal="brief:skipped",
                    source="briefing",
                    ref_type="brief",
                    ref_id="2",
                ),
                UserBehaviorLog(
                    user_id=u,
                    signal="brief:skipped",
                    source="briefing",
                    ref_type="brief",
                    ref_id="3",
                ),
                UserBehaviorLog(
                    user_id=u, signal="bought", source="feedback_listener", ticker="HPG"
                ),
                _advice(u, "followed_advice", OutcomeVerdict.CORRECT),
                _advice(u, "followed_advice", OutcomeVerdict.INCORRECT),
                _advice(u, "ignored_advice", OutcomeVerdict.CORRECT),
                _advice(u, None, None),
            ]
        )
        await s.commit()
        out = await AccuracyProjection(s).get(u, days=30)

    core, brief, pre = (out["by_source"][k] for k in ("core", "briefing", "pretrade"))
    assert core == {"total": 2, "acted": 1, "rejected": 1, "not_acted": 0, "acted_rate": 0.5}
    assert brief == {"total": 3, "acted": 1, "watching": 0, "skipped": 2, "acted_rate": 0.333}
    assert pre["total"] == 4 and pre["followed"] == 2 and pre["ignored"] == 1
    assert pre["follow_rate"] == 0.667 and pre["evaluated"] == 3
    assert pre["followed_hit_rate"] == 0.5 and pre["ignored_hit_rate"] == 1.0
    assert pre["advice_hit_rate"] == 0.667
    assert sum(t["total"] for t in out["trend"]) == 6


# ── summary (Wave U3) — một kết luận dùng chung cho dashboard/bot ─────────────


def _pre(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "total": 0,
        "followed": 0,
        "ignored": 0,
        "follow_rate": None,
        "evaluated": 0,
        "followed_hit_rate": None,
        "ignored_hit_rate": None,
        "advice_hit_rate": None,
    }
    base.update(kw)
    return base


def _src(core_total: int = 0, brief_total: int = 0, **pre: object) -> dict[str, object]:
    return {
        "core": {"total": core_total},
        "briefing": {"total": brief_total},
        "pretrade": _pre(**pre),
    }


def test_summary_empty_when_no_signals() -> None:
    from src.readmodel.accuracy_projection import build_summary

    assert build_summary(_src())["verdict"] == "empty"


def test_summary_insufficient_below_min_evaluated() -> None:
    from src.readmodel.accuracy_projection import build_summary

    out = build_summary(_src(core_total=3, total=4, evaluated=2))
    assert out["verdict"] == "insufficient"
    assert "2/5" in out["headline"]


@pytest.mark.parametrize(
    ("f_hit", "i_hit", "verdict"),
    [
        (0.70, 0.40, "ai_edge"),
        (0.40, 0.70, "self_edge"),
        (0.55, 0.50, "mixed"),
    ],
)
def test_summary_edge_by_hit_rate_gap(f_hit: float, i_hit: float, verdict: str) -> None:
    from src.readmodel.accuracy_projection import build_summary

    out = build_summary(
        _src(
            total=10,
            followed=6,
            ignored=4,
            follow_rate=0.6,
            evaluated=10,
            followed_hit_rate=f_hit,
            ignored_hit_rate=i_hit,
            advice_hit_rate=0.6,
        )
    )
    assert out["verdict"] == verdict
    assert f"{round(f_hit * 100)}%" in out["headline"]


def test_summary_low_adoption_when_never_ignored_side_evaluated() -> None:
    from src.readmodel.accuracy_projection import build_summary

    out = build_summary(
        _src(
            total=10,
            followed=3,
            ignored=7,
            follow_rate=0.3,
            evaluated=6,
            followed_hit_rate=0.5,
            ignored_hit_rate=None,
            advice_hit_rate=0.5,
        )
    )
    assert out["verdict"] == "low_adoption"


@pytest.mark.anyio
async def test_get_includes_summary() -> None:
    async with AsyncSessionLocal() as s:
        out = await AccuracyProjection(s).get("nobody-2", days=7)
    assert out["summary"]["verdict"] == "empty"
