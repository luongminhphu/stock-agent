"""Reviews command cog — surface recent AI thesis review output.

Owner: bot segment (adapter only).
Commands:
    /reviews   — list AI judge verdicts from the last N hours,
                 optionally filtered by ticker.

Adapter contract:
    - Calls RecentReviewsStore.get_recent() via bootstrap getter.
    - No business logic here — all query logic lives in readmodel segment.
    - ephemeral=True: output is private to the requesting user.
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_recent_reviews_store
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Verdict → Discord color
_VERDICT_COLOR: dict[str, discord.Color] = {
    "BULLISH": discord.Color.green(),
    "BEARISH": discord.Color.red(),
    "NEUTRAL": discord.Color.light_grey(),
    "INVALIDATED": discord.Color.dark_red(),
    "INSUFFICIENT_DATA": discord.Color.greyple(),
}

# Verdict → emoji prefix
_VERDICT_ICON: dict[str, str] = {
    "BULLISH": "🟢",
    "BEARISH": "🔴",
    "NEUTRAL": "⚪",
    "INVALIDATED": "💀",
    "INSUFFICIENT_DATA": "❓",
}


class ReviewsCog(BaseCog):
    """Slash command: /reviews."""

    @app_commands.command(
        name="reviews",
        description="Show recent AI thesis review verdicts (last 24h by default)",
    )
    @app_commands.describe(
        ticker="Filter by ticker (e.g. VNM). Leave blank for all tickers.",
        hours="Look-back window in hours (default 24, max 168)",
        limit="Max number of reviews to show (default 10, max 25)",
    )
    async def reviews(
        self,
        interaction: discord.Interaction,
        ticker: Optional[str] = None,
        hours: Optional[int] = 24,
        limit: Optional[int] = 10,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        since_hours = max(1, min(hours or 24, 168))
        page_limit = max(1, min(limit or 10, 25))
        ticker_filter = ticker.upper().strip() if ticker else None

        try:
            store = get_recent_reviews_store()
            result = await store.get_recent(
                user_id=user_id,
                since_hours=since_hours,
                limit=page_limit,
                ticker=ticker_filter,
            )
        except Exception as exc:
            logger.error("bot.reviews.error", user_id=user_id, error=str(exc))
            await self.send_error(
                interaction,
                title="Could not fetch reviews",
                description=f"An error occurred while loading recent reviews.\n`{exc}`",
            )
            return

        if not result.rows:
            ticker_msg = f" for **{ticker_filter}**" if ticker_filter else ""
            await self.send_ok(
                interaction,
                title=f"📭 No reviews in the last {since_hours}h{ticker_msg}",
                description=(
                    "No AI thesis reviews have run in this window.\n"
                    "Reviews are triggered automatically by the SignalEngine, "
                    "or manually via `/review_thesis`."
                ),
            )
            return

        ticker_label = f" · {ticker_filter}" if ticker_filter else ""
        # Pick embed color from the most recent verdict
        top_verdict = result.rows[0].verdict.upper() if result.rows else "NEUTRAL"
        embed_color = _VERDICT_COLOR.get(top_verdict, discord.Color.light_grey())

        embed = discord.Embed(
            title=f"🤖 Recent AI Reviews{ticker_label} · last {since_hours}h",
            description=(
                f"**{result.total}** review(s) found · showing {len(result.rows)}"
            ),
            color=embed_color,
        )

        for row in result.rows:
            verdict_up = row.verdict.upper()
            icon = _VERDICT_ICON.get(verdict_up, "•")
            confidence_str = f"{row.confidence_pct}%" if row.confidence_pct else "—"

            # Timestamp: HH:MM if today, else dd/mm
            if row.reviewed_at:
                from datetime import datetime, timezone
                now = datetime.now(tz=timezone.utc)
                reviewed = row.reviewed_at
                if hasattr(reviewed, 'tzinfo') and reviewed.tzinfo is None:
                    from datetime import timezone as tz
                    reviewed = reviewed.replace(tzinfo=tz.utc)
                if reviewed.date() == now.date():
                    time_str = reviewed.strftime("%H:%M")
                else:
                    time_str = reviewed.strftime("%d/%m %H:%M")
            else:
                time_str = "—"

            field_name = (
                f"{icon} {row.ticker} · {verdict_up} · {confidence_str} · {time_str}"
            )

            # Summary line (prefer summary, fallback to reasoning[:120])
            summary_text = (
                row.summary
                or (row.reasoning[:120] + "…" if row.reasoning and len(row.reasoning) > 120 else row.reasoning)
                or "_No summary available_"
            )

            # Top 2 risk signals
            risk_lines = ""
            if row.risk_signals:
                top_risks = row.risk_signals[:2]
                risk_lines = "\n" + "\n".join(f"⚠️ {r}" for r in top_risks)

            field_value = f"{summary_text}{risk_lines}"
            embed.add_field(name=field_name, value=field_value[:1024], inline=False)

        embed.set_footer(
            text=(
                f"Use /review_thesis <id> to trigger a new review · "
                f"/recommendations <id> to act on AI suggestions"
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
