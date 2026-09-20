"""market.trend_engine.rule_based_prediction — single source of truth cho
verdict kỹ thuật không cần AI (Wave F8).

Trước đây có 2 bản rule khác ngưỡng ở bot.commands.trend và
ai.agents.trend_reasoning → cùng một bundle có thể ra verdict khác nhau
tùy surface. Test này khoá contract của bản hợp nhất.
"""

from __future__ import annotations

import pytest

from src.ai.schemas.trend_prediction import (
    SignalLabel,
    SignalScore,
    TechnicalSignalBundle,
    TrendDirection,
    TrendHorizon,
    TrendPrediction,
    TrendRegime,
    TrendVerdict,
)
from src.market.trend_engine import (
    RULE_FALLBACK_MAX_CONFIDENCE,
    RULE_FALLBACK_TAG,
    rule_based_prediction,
)


def _bundle(
    composite: float,
    regime: TrendRegime = TrendRegime.RANGING,
    momentum: SignalLabel = SignalLabel.NEUTRAL,
    structure: SignalLabel = SignalLabel.NEUTRAL,
    volume: SignalLabel = SignalLabel.NEUTRAL,
    volatility: SignalLabel = SignalLabel.NEUTRAL,
) -> TechnicalSignalBundle:
    def _s(label: SignalLabel) -> SignalScore:
        return SignalScore(value=0.5, label=label)

    return TechnicalSignalBundle(
        symbol="HPG",
        momentum=_s(momentum),
        structure=_s(structure),
        volume=_s(volume),
        volatility=_s(volatility),
        composite=composite,
        regime=regime,
    )


@pytest.mark.parametrize(
    ("composite", "verdict", "direction"),
    [
        (0.90, TrendVerdict.STRONG_BUY, TrendDirection.UP),
        (0.72, TrendVerdict.STRONG_BUY, TrendDirection.UP),
        (0.60, TrendVerdict.BUY, TrendDirection.UP),
        (0.50, TrendVerdict.HOLD, TrendDirection.SIDEWAYS),
        (0.40, TrendVerdict.WATCH, TrendDirection.SIDEWAYS),
        (0.25, TrendVerdict.REDUCE, TrendDirection.DOWN),
        (0.05, TrendVerdict.STRONG_SELL, TrendDirection.DOWN),
    ],
)
def test_composite_bands_map_to_verdict_and_direction(
    composite: float, verdict: TrendVerdict, direction: TrendDirection
) -> None:
    pred = rule_based_prediction(_bundle(composite))
    assert isinstance(pred, TrendPrediction)
    assert pred.verdict is verdict
    assert pred.direction is direction
    assert pred.symbol == "HPG"


def test_confidence_never_reaches_strong_alert_gate() -> None:
    # briefing.trend_batch_scheduler._MIN_ALERT_CONFIDENCE = 0.68 — fallback
    # phải luôn nằm dưới ngưỡng này để không tự đẩy STRONG alert.
    for c in (0.0, 0.2, 0.5, 0.8, 1.0):
        pred = rule_based_prediction(_bundle(c))
        assert pred.confidence <= RULE_FALLBACK_MAX_CONFIDENCE < 0.68
    assert rule_based_prediction(_bundle(0.5)).confidence == 0.0


def test_horizon_follows_regime() -> None:
    assert rule_based_prediction(_bundle(0.6, TrendRegime.VOLATILE)).horizon is (
        TrendHorizon.SHORT_TERM
    )
    assert rule_based_prediction(_bundle(0.6, TrendRegime.TRENDING_UP)).horizon is (
        TrendHorizon.MID_TERM
    )


def test_risk_signals_and_next_watch_from_labels() -> None:
    pred = rule_based_prediction(
        _bundle(
            0.25,
            regime=TrendRegime.RANGING,
            momentum=SignalLabel.BEARISH,
            structure=SignalLabel.BEARISH,
            volume=SignalLabel.BEARISH,
            volatility=SignalLabel.BULLISH,
        )
    )
    assert pred.verdict is TrendVerdict.REDUCE
    assert len(pred.risk_signals) == 4  # 3 bearish dims + ATR expansion khi REDUCE
    assert any("ATR" in r for r in pred.risk_signals)
    assert pred.next_watch == ["Chờ breakout khỏi vùng tích lũy"]


def test_reasoning_is_tagged_as_rule_based() -> None:
    pred = rule_based_prediction(_bundle(0.66))
    assert pred.reasoning.startswith(RULE_FALLBACK_TAG)
    assert "0.66" in pred.reasoning
