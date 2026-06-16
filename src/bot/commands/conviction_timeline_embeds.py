"""Embed builders for Conviction Score Timeline.

Owner: bot segment — presentation only.
No domain logic. No DB calls. No AI calls.
Inputs come from readmodel.ConvictionTimelineResponse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from src.bot.discord_helper import COLORS

if TYPE_CHECKING:
    from src.readmodel.schemas import ConvictionTimelineResponse

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

# TREND_META hex are intentionally distinct from COLORS.*:
# they encode conviction trajectory semantics, not market direction.
# Do not replace with COLORS.* — they carry different meaning.
TREND_META: dict[str, tuple[str, int]] = {
    "improving":         ("\U0001f4c8 Improving",        0x27AE60),
    "declining":         ("\U0001f4c9 Declining",        0xE74C3C),
    "stable":            ("\u27a1\ufe0f Stable",          0x3498DB),
    "insufficient_data": ("\u26aa Insufficient data",    COLORS.GREY),
}

TIER_ICON: dict[str, str] = {
    "Critical": "\U0001f534",
    "Weak":     "\U0001f7e0",
    "Moderate": "\U0001f7e1",
    "Healthy":  "\U0001f7e2",
    "Strong":   "\U0001f48e",
}

# NOTE: "WATCH" key is intentional — maps to conviction watchlist state,
# not present in discord_helper.VERDICT_ICONS. Keep local.
VERDICT_ICON: dict[str, str] = {
    "BULLISH": "\U0001f7e2",
    "BEARISH": "\U0001f534",
    "NEUTRAL": "\U0001f7e1",
    "WATCH":   "\U0001f440",
}

_BAR_FILLED = "\u2588"
_BAR_EMPTY  = "\u2591"
_BAR_WIDTH  = 8


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
    """Unicode block sparkline for up to 10 score points."""
    blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    if not scores:
        return "\u2014"
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
    """Build a Discord embed from ConvictionTimelineResponse."""
    trend_key = (result.trend or "insufficient_data").lower()
    trend_label, colour = TREND_META.get(trend_key, TREND_META["insufficient_data"])

    title = f"{trend_label}  \u00b7  {result.ticker}"
    description = f"> *{result.title}*" if result.title else ""

    embed = discord.Embed(
        title=title,
        description=description or discord.utils.MISSING,
        colour=colour,
    )

    if result.latest_score is not None:
        latest_pt = result.points[-1] if result.points else None
        tier_label = latest_pt.score_tier if latest_pt else "Unknown"
        tier_icon = TIER_ICON.get(tier_label, "\u26aa")
        embed.add_field(
            name="Current conviction",
            value=f"{tier_icon} **{result.latest_score:.1f} / 100** \u2014 {tier_label}",
            inline=True,
        )
        if result.earliest_score is not None and result.total >= 2:
            delta = result.latest_score - result.earliest_score
            sign = "+" if delta >= 0 else ""
            embed.add_field(
                name="\u0394 vs earliest",
                value=f"{sign}{delta:.1f} pts",
                inline=True,
            )

    if result.points:
        recent = result.points[-5:]
        scores = [p.score for p in recent]
        spark = _sparkline(scores)
        dates = [p.snapshotted_at.strftime("%d/%m") for p in recent]
        embed.add_field(
            name=f"Score history (last {len(recent)})",
            value=f"`{spark}`\n{' \u2192 '.join(dates)}",
            inline=False,
        )

    latest_pt = result.points[-1] if result.points else None
    if latest_pt and latest_pt.breakdown:
        bd = latest_pt.breakdown
        lines = [
            f"`{_score_bar(bd.assumption_health, 40)}` Assumptions  **{bd.assumption_health:.1f}**/40",
            f"`{_score_bar(bd.catalyst_progress, 30)}` Catalysts     **{bd.catalyst_progress:.1f}**/30",
            f"`{_score_bar(bd.risk_reward, 20)}`      Risk/Reward   **{bd.risk_reward:.1f}**/20",
            f"`{_score_bar(bd.review_confidence, 10)}`      AI Confidence **{bd.review_confidence:.1f}**/10",
        ]
        embed.add_field(name="Chi tiết điểm", value="\n".join(lines), inline=False)

    if latest_pt and latest_pt.verdict:
        v_icon = VERDICT_ICON.get(latest_pt.verdict.upper(), "\u26aa")
        conf_pct = f"{latest_pt.confidence * 100:.0f}%" if latest_pt.confidence is not None else "\u2014"
        ts_str = latest_pt.snapshotted_at.strftime("%d/%m/%Y")
        embed.add_field(
            name="Latest AI verdict",
            value=f"{v_icon} **{latest_pt.verdict}** \u00b7 confidence {conf_pct}",
            inline=True,
        )
        embed.add_field(name="Snapshot date", value=ts_str, inline=True)

    if latest_pt and latest_pt.price:
        pnl_str = (
            f" \u00b7 PnL {latest_pt.pnl_pct:+.1f}%" if latest_pt.pnl_pct is not None else ""
        )
        embed.add_field(
            name="Price at last snapshot",
            value=f"{latest_pt.price:,.0f} VND{pnl_str}",
            inline=False,
        )

    embed.set_footer(text=f"{result.total} snapshot(s) \u00b7 /conviction {result.ticker}")
    return embed


def build_conviction_not_found_embed(
    ticker: str,
    thesis_id: int | None = None,
) -> discord.Embed:
    """Returned when no active thesis or no snapshots exist for ticker."""
    ticker_upper = ticker.upper()

    if thesis_id is None:
        description = (
            f"Không tìm thấy thesis đang **active** cho **{ticker_upper}**.\n"
            "\u2192 Dùng `/thesis add` để tạo thesis mới."
        )
    else:
        description = (
            f"Thesis **#{thesis_id}** ({ticker_upper}) đang active nhưng chưa có snapshot nào.\n"
            f"\u2192 Dùng `/review_thesis {thesis_id}` để chạy AI review đầu tiên và tạo snapshot."
        )

    return discord.Embed(
        title=f"\u26aa No conviction data \u2014 {ticker_upper}",
        description=description,
        colour=COLORS.GREY,
    )
