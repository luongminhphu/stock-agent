"""
Schemas for BriefingAgent (morning and EOD briefs).

Owner: ai segment.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from src.ai.schemas._base import Verdict, _coerce_confidence


class MarketSentiment(StrEnum):
    """Market sentiment values used by BriefingAgent.

    Canonical values (AI prompt instructs these): RISK_ON, RISK_OFF, MIXED, UNCERTAIN.
    Legacy values (kept for backward compat with old BriefSnapshot DB records): BULLISH, BEARISH, NEUTRAL.
    """

    RISK_ON   = "RISK_ON"
    RISK_OFF  = "RISK_OFF"
    MIXED     = "MIXED"
    UNCERTAIN = "UNCERTAIN"
    # Legacy — kept for backward compat; do not use in new prompts
    BULLISH   = "BULLISH"
    BEARISH   = "BEARISH"
    NEUTRAL   = "NEUTRAL"


class ActionPriority(StrEnum):
    ACT_TODAY  = "ACT_TODAY"
    WATCH_MORE = "WATCH_MORE"
    SKIP_TODAY = "SKIP_TODAY"


class PrioritizedAction(BaseModel):
    """A single prioritized action item in a brief."""

    ticker: str = Field(default="", description="Ticker symbol if applicable")
    priority: ActionPriority
    action: str = Field(description="Short action description")
    rationale: str = Field(description="Why this action at this priority")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @property
    def reason(self) -> str:
        return self.rationale

    @model_validator(mode="before")
    @classmethod
    def coerce_reason_to_rationale(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("ticker") is None:
            data = dict(data)
            data["ticker"] = ""
        if "rationale" not in data or not data.get("rationale"):
            for alias in ("reason", "explanation", "why"):
                if data.get(alias):
                    data = dict(data)
                    data["rationale"] = data[alias]
                    break
        return data

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class ActionQueue(BaseModel):
    """Derived action queue from BriefOutput.prioritized_actions.

    Populated by build_action_queue model_validator in BriefOutput.
    Not filled directly by AI.
    """

    queue: list[PrioritizedAction] = Field(
        default_factory=list,
        description=(
            "Sorted actions: ACT_TODAY -> WATCH_MORE -> SKIP_TODAY. "
            "Max 5. Tiebreak by confidence descending."
        ),
    )
    top_action: PrioritizedAction | None = Field(
        default=None,
        description="ACT_TODAY item with highest confidence; None if no ACT_TODAY.",Jenkins
    )
    signal_summary: str = Field(
        default="",
        description=(
            "Summary string: '🔴 <urgent tickers>  🟡 <watch tickers>'. "
            "Fallback: '0 urgent / 0 watch'."
        ),
    )


class WatchlistTickerSummary(BaseModel):
    """Per-ticker summary in a brief."""

    ticker: str
    price: float = Field(default=0.0, description="Current price")
    change_pct: float = Field(default=0.0, description="Price change % today")
    signal: str = Field(default="neutral", description="bullish | bearish | neutral")
    one_line: str = Field(description="One-sentence summary for this ticker")
    watch_reason: str = Field(default="", description="Why this is on watchlist")
    verdict: Verdict = Verdict.NEUTRAL
    one_liner: str = Field(default="", description="Alias for one_line (legacy)")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class PortfolioPositionBrief(BaseModel):
    """Brief summary of a single portfolio position."""

    ticker: str
    pnl_pct: float | None = None
    verdict: Verdict = Verdict.NEUTRAL
    note: str = Field(default="", description="Short portfolio-level observation")


class BriefOutput(BaseModel):
    """Structured output from BriefingAgent (morning or EOD)."""

    headline: str = Field(description="One-sentence market headline")
    sentiment: MarketSentiment
    summary: str = Field(description="2-3 sentence narrative summary")
    key_movers: list[str] = Field(default_factory=list, description="Notable tickers or sectors")
    watchlist_alerts: list[str] = Field(
        default_factory=list,
        description="Watchlist-specific observations",
    )
    action_items: list[str] = Field(
        default_factory=list,
        description="[DEPRECATED] Flat action list. Superseded by prioritized_actions.",
    )
    prioritized_actions: list[PrioritizedAction] = Field(
        default_factory=list,
        description=(
            "Hành động phân loại theo priority: ACT_TODAY -> WATCH_MORE -> SKIP_TODAY. "
            "AI phải xuất ít nhất 1 item khi có watchlist. "
            "Formatter nhóm theo bucket và render thành 3 section Discord."
        ),
    )
    ticker_summaries: list[WatchlistTickerSummary] = Field(
        default_factory=list,
        description="Per-ticker summary for each watchlist item",
    )
    portfolio_summary: list[str] = Field(
        default_factory=list,
        description=(
            "Nhận xét portfolio alignment với market hôm nay. "
            "Mỗi item là 1 câu liên tục: rủi ro tập trung, position nổi bật, "
            "hoặc gợi ý cần chú ý. Rỗng nếu không có portfolio data."
        ),
    )
    action_queue: ActionQueue = Field(
        default_factory=ActionQueue,
        description=(
            "Derived từ prioritized_actions — không do AI điền trực tiếp. "
            "Populated bởi build_action_queue model_validator. "
            "Downstream (bot, formatter, readmodel) dùng field này thay vì "
            "tự sort/filter prioritized_actions."
        ),
    )

    @field_validator("prioritized_actions", mode="before")
    @classmethod
    def ensure_prioritized_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @model_validator(mode="after")
    def build_action_queue(self) -> "BriefOutput":
        """Derive ActionQueue từ prioritized_actions sau khi AI parse xong."""
        actions = self.prioritized_actions
        if not actions:
            return self

        _priority_order = {
            ActionPriority.ACT_TODAY: 0,
            ActionPriority.WATCH_MORE: 1,
            ActionPriority.SKIP_TODAY: 2,
        }
        sorted_actions = sorted(
            actions,
            key=lambda a: (_priority_order.get(a.priority, 9), -a.confidence),
        )
        queue = sorted_actions[:5]

        top_action = next(
            (a for a in queue if a.priority == ActionPriority.ACT_TODAY), None
        )

        urgent = [a.ticker for a in queue if a.priority == ActionPriority.ACT_TODAY and a.ticker]
        watch = [a.ticker for a in queue if a.priority == ActionPriority.WATCH_MORE and a.ticker]
        if urgent or watch:
            urgent_str = " ".join(urgent) if urgent else f"{len(urgent)} urgent"
            watch_str = " ".join(watch) if watch else f"{len(watch)} watch"
            signal_summary = f"\U0001f534 {urgent_str}  \U0001f7e1 {watch_str}"
        else:
            act_count = sum(1 for a in queue if a.priority == ActionPriority.ACT_TODAY)
            watch_count = sum(1 for a in queue if a.priority == ActionPriority.WATCH_MORE)
            signal_summary = f"\U0001f534 {act_count} urgent  \U0001f7e1 {watch_count} watch"

        self.action_queue = ActionQueue(
            queue=queue,
            top_action=top_action,
            signal_summary=signal_summary,
        )
        return self
