"""Embed builders for Conviction Score Timeline.

Owner: bot segment — presentation only.
No domain logic. No DB calls. No AI calls.
Inputs come from readmodel.ConvictionTimelineResponse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from src.readmodel.schemas import ConvictionTimelineResponse

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

TREND_META: dict[str, tuple[str, int]] = {
    # trend_value → (label, discord embed colour)
    "improving": ("📈 Improving", 0x27AE60),
    "declining": ("📉 Declining", 0xE74C3C),
    "stable": ("➡️ Stable", 0x3498DB),
    "insufficient_data": ("⚪ Insufficient data", 0x95A5A6),
}

TIER_ICON: dict[str, str] = {
    "Critical": "🔴",
    "Weak": "🟠",
    "Moderate": "🟡",
    "Healthy": "🟢",
    "Strong": "💎",
}

VERDICT_ICON: dict[str, str] = {
    "BULLISH": "🟢",
    "BEARISH": "🔴",
    "NEUTRAL": "🟡",
    "WATCH": "👀",
}

_BAR_FILLED = "█"
_BAR_EMPTY = "░"
_BAR_WIDTH = 8  # chars per dimension bar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score_bar(score: float, max_score: float = 100.0) -> str:
    """ASCII progress bar: '██████░░' for 75/100."""
    if max_score <= 0:
        return _BAR_EMPTY * _BAR_WIDTH
    filled = round((score / max_score) * _BAR_WIDTH)
    filled = max(0, min(_BAR_WIDTH, filled))
    return _BAR_FILLED * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)


def _sparkline(scores: list[float]) -> str:
    """Unicode block sparkline for up to 10 score points.

    Uses ▁▂▃▄▅▆▇█ mapped to 0-100 range.
    """
    blocks = " ▁▂▃▄▅▆▇█"
    if not scores:
        return "—"
    lo, hi = min(scores), max(scores)
    span = hi - lo if hi != lo else 1.0
    chars = []
    for s in scores:
        idx = round(((s - lo) / span) * (len(blocks) - 1))
        chars.append(blocks[max(0, min(len(blocks) - 1, idx))])
    return "".join(chars)


# ---------------------------------------------------------------------------
# Main embed builder
# ---------------------------------------------------------------------------


def build_conviction_embed(result: "ConvictionTimelineResponse") -> discord.Embed:
    """Build a Discord embed from ConvictionTimelineResponse.

    Layout:
        Header  : [TREND_BADGE]  TICKER — title
        Body    : latest score + tier
                  sparkline (last 5 pts)
                  breakdown bars (if available on latest point)
                  nearest-prior verdict + confidence
        Footer  : total snapshots · delta vs earliest
    """
    trend_key = (result.trend or "insufficient_data").lower()
    trend_label, colour = TREND_META.get(trend_key, TREND_META["insufficient_data"])

    title = f"{trend_label}  ·  {result.ticker}"
    description = f"> *{result.title}*" if result.title else ""

    embed = discord.Embed(
        title=title,
        description=description or discord.utils.MISSING,
        colour=colour,
    )

    # -- Latest score block --
    if result.latest_score is not None:
        latest_pt = result.points[-1] if result.points else None
        tier_label = latest_pt.score_tier if latest_pt else "Unknown"
        tier_icon = TIER_ICON.get(tier_label, "⚪")
        embed.add_field(
            name="Current conviction",
            value=f"{tier_icon} **{result.latest_score:.1f} / 100** — {tier_label}",
            inline=True,
        )

        # Delta vs earliest
        if result.earliest_score is not None and result.total >= 2:
            delta = result.latest_score - result.earliest_score
            sign = "+" if delta >= 0 else ""
            embed.add_field(
                name="Δ vs earliest",
                value=f"{sign}{delta:.1f} pts",
                inline=True,
            )

    # -- Sparkline (last 5 points) --
    if result.points:
        recent = result.points[-5:]
        scores = [p.score for p in recent]
        spark = _sparkline(scores)
        dates = [p.snapshotted_at.strftime("%d/%m") for p in recent]
        embed.add_field(
            name=f"Score history (last {len(recent)})",
            value=f"`{spark}`\n{' → '.join(dates)}",
            inline=False,
        )

    # -- Breakdown bars (from latest point if available) --
    latest_pt = result.points[-1] if result.points else None
    if latest_pt and latest_pt.breakdown:
        bd = latest_pt.breakdown
        lines = [
            f"`{_score_bar(bd.assumption_health, 40)}` Assumptions  **{bd.assumption_health:.1f}**/40",
            f"`{_score_bar(bd.catalyst_progress, 30)}` Catalysts     **{bd.catalyst_progress:.1f}**/30",
            f"`{_score_bar(bd.risk_reward, 20)}`      Risk/Reward   **{bd.risk_reward:.1f}**/20",
            f"`{_score_bar(bd.review_confidence, 10)}`      AI Confidence **{bd.review_confidence:.1f}**/10",
        ]
        embed.add_field(
            name="Score breakdown",
            value="\n".join(lines),
            inline=False,
        )

    # -- Nearest prior review verdict --
    if latest_pt and latest_pt.verdict:
        v_icon = VERDICT_ICON.get(latest_pt.verdict.upper(), "⚪")
        conf_pct = f"{latest_pt.confidence * 100:.0f}%" if latest_pt.confidence is not None else "—"
        ts_str = latest_pt.snapshotted_at.strftime("%d/%m/%Y")
        embed.add_field(
            name="Latest AI verdict",
            value=f"{v_icon} **{latest_pt.verdict}** · confidence {conf_pct}",
            inline=True,
        )
        embed.add_field(
            name="Snapshot date",
            value=ts_str,
            inline=True,
        )

    # -- Current price + PnL --
    if latest_pt and latest_pt.price:
        pnl_str = (
            f" · PnL {latest_pt.pnl_pct:+.1f}%" if latest_pt.pnl_pct is not None else ""
        )
        embed.add_field(
            name="Price at last snapshot",
            value=f"{latest_pt.price:,.0f} VND{pnl_str}",
            inline=False,
        )

    embed.set_footer(text=f"{result.total} snapshot(s) · /conviction {result.ticker}")
    return embed


def build_conviction_not_found_embed(
    ticker: str,
    thesis_id: int | None = None,
) -> discord.Embed:
    """Returned when no active thesis or no snapshots exist for ticker.

    Branches on thesis_id:
    - None  → no ACTIVE thesis found → guide user to /thesis add
    - int   → thesis exists but 0 snapshots → guide user to /review_thesis <id>
    """
    ticker_upper = ticker.upper()

    if thesis_id is None:
        description = (
            f"Không tìm thấy thesis đang **active** cho **{ticker_upper}**.\n"
            "→ Dùng `/thesis add` để tạo thesis mới."
        )
    else:
        description = (
            f"Thesis **#{thesis_id}** ({ticker_upper}) đang active nhưng chưa có snapshot nào.\n"
            f"→ Dùng `/review_thesis {thesis_id}` để chạy AI review đầu tiên và tạo snapshot."
        )

    return discord.Embed(
        title=f"⚪ No conviction data — {ticker_upper}",
        description=description,
        colour=0x95A5A6,
    )
