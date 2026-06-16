"""Watchlist commands cog.

Owner: bot segment.
Commands:
    /watchlist add     — add ticker
    /watchlist remove  — remove ticker
    /watchlist list    — show watchlist with live prices + latest AI verdict
    /watchlist scan    — run signal scan across watchlist
    /watchlist alert   — set price/change alert for a ticker

No business logic. All rules live in watchlist/market/scan segments.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service, get_session_factory
from src.platform.logging import get_logger
from src.watchlist.models import AlertConditionType
from src.watchlist.service import (
    AddAlertInput,
    AddToWatchlistInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
    WatchlistService,
)

logger = get_logger(__name__)

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

_CONDITION_LABEL = {
    AlertConditionType.PRICE_ABOVE: "Price ≥",
    AlertConditionType.PRICE_BELOW: "Price ≤",
    AlertConditionType.CHANGE_PCT_UP: "Change ≥ +",
    AlertConditionType.CHANGE_PCT_DOWN: "Change ≤ -",
    AlertConditionType.VOLUME_SPIKE: "Volume ≥",
}

_VERDICT_ICON: dict[str, str] = {
    "BULLISH": "📈",
    "HOLD": "⏸️",
    "NEUTRAL": "⏸️",
    "WEAKENING": "⚠️",
    "BEARISH": "📉",
    "INVALIDATED": "❌",
}


def _verdict_suffix(verdict: str, confidence_pct: int) -> str:
    """Return a compact verdict label for inline display, e.g. '📈 BULLISH 82%'."""
    icon = _VERDICT_ICON.get(verdict.upper(), "🔍")
    return f" · {icon} {verdict} {confidence_pct}%"


async def _fetch_verdict_map(user_id: str) -> dict[str, tuple[str, int]]:
    """Return {ticker: (verdict, confidence_pct)} for latest review per ticker.

    Uses RecentReviewsStore with a 7-day window — newest-first, so first hit
    per ticker is always the latest review.
    Silent: returns {} on any error so /watchlist list never crashes.
    """
    try:
        from src.readmodel.recent_reviews_store import RecentReviewsStore

        store = RecentReviewsStore(session_factory=get_session_factory())
        response = await store.get_recent(user_id=user_id, since_hours=168, limit=50)
        verdict_map: dict[str, tuple[str, int]] = {}
        for row in response.rows:
            ticker = row.ticker.upper()
            if ticker not in verdict_map:
                verdict_map[ticker] = (row.verdict, row.confidence_pct)
        return verdict_map
    except Exception as exc:
        logger.warning("watchlist_list.verdict_fetch_failed", user_id=user_id, error=str(exc))
        return {}


class WatchlistCog(BaseCog):
    """Slash commands: /watchlist group"""

    group = app_commands.Group(name="watchlist", description="Manage your watchlist")

    # ------------------------------------------------------------------
    # /watchlist add
    # ------------------------------------------------------------------

    @group.command(name="add", description="Add a ticker to your watchlist")
    @app_commands.describe(
        ticker="Stock ticker (e.g. VNM)",
        note="Optional note about this position",
    )
    async def watchlist_add(
        self,
        interaction: discord.Interaction,
        ticker: str,
        note: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = WatchlistService(session)
                await svc.add(
                    AddToWatchlistInput(
                        user_id=user_id,
                        ticker=ticker.upper(),
                        note=note or None,
                    )
                )
        except WatchlistItemAlreadyExistsError:
            await self.send_error(
                interaction,
                title="Already in watchlist",
                description=f"**{ticker.upper()}** is already in your watchlist.",
            )
            return
        except Exception as exc:
            logger.error("watchlist_add.error", ticker=ticker, error=str(exc))
            await self.send_error(interaction, title="Lỗi", description=str(exc))
            return

        await self.send_ok(
            interaction,
            title="✅ Added to watchlist",
            description=f"**{ticker.upper()}** has been added.\nUse `/watchlist scan` to check signals.",
        )

    # ------------------------------------------------------------------
    # /watchlist remove
    # ------------------------------------------------------------------

    @group.command(name="remove", description="Remove a ticker from your watchlist")
    @app_commands.describe(ticker="Stock ticker to remove")
    async def watchlist_remove(
        self,
        interaction: discord.Interaction,
        ticker: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = WatchlistService(session)
                await svc.remove(user_id=user_id, ticker=ticker.upper())
        except WatchlistItemNotFoundError:
            await self.send_error(
                interaction,
                title="Không tìm thấy",
                description=f"**{ticker.upper()}** is not in your watchlist.",
            )
            return
        except Exception as exc:
            logger.error("watchlist_remove.error", ticker=ticker, error=str(exc))
            await self.send_error(interaction, title="Lỗi", description=str(exc))
            return

        await self.send_ok(
            interaction,
            title="Removed",
            description=f"**{ticker.upper()}** removed from your watchlist.",
        )

    # ------------------------------------------------------------------
    # /watchlist list
    # ------------------------------------------------------------------

    @group.command(name="list", description="Show your watchlist with live prices")
    async def watchlist_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = WatchlistService(session)
                items = await svc.list_items_with_prices(user_id, get_quote_service())
        except Exception as exc:
            logger.error("watchlist_list.error", error=str(exc))
            await self.send_error(interaction, title="Lỗi", description=str(exc))
            return

        if not items:
            await self.send_ok(
                interaction,
                title="📋 Your Watchlist",
                description="Your watchlist is empty.\nUse `/watchlist add <ticker>` to start.",
            )
            return

        # Fetch latest AI verdict per ticker — silent fallback to {} on error
        verdict_map = await _fetch_verdict_map(user_id)

        lines = []
        for item in items:
            ticker = item.ticker.upper()
            note_part = f" · {item.note[:30]}" if item.note else ""
            verdict_part = ""
            if ticker in verdict_map:
                v, c = verdict_map[ticker]
                verdict_part = _verdict_suffix(v, c)
            lines.append(f"• **{ticker}** {item.price_str}{verdict_part}{note_part}")

        embed = discord.Embed(
            title="📋 Your Watchlist",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        has_verdict = any(item.ticker.upper() in verdict_map for item in items)
        footer_suffix = " · verdict = last 7d AI review" if has_verdict else " · no AI review yet — use /thesis review"
        embed.set_footer(text=f"{len(items)} ticker(s) · prices may be delayed{footer_suffix}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /watchlist scan
    # ------------------------------------------------------------------

    @group.command(name="scan", description="Scan your watchlist for signals & triggered alerts")
    async def watchlist_scan(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                from src.watchlist.scan_service import ScanService

                svc = ScanService(
                    session=session,
                    quote_service=get_quote_service(),
                )
                result = await svc.scan_user(user_id)
                await session.commit()
        except Exception as exc:
            logger.error("watchlist_scan.error", user_id=user_id, error=str(exc))
            await self.send_error(
                interaction,
                title="Scan failed",
                description=f"Could not complete watchlist scan.\n`{exc}`",
            )
            return

        scanned_at_str = result.scanned_at.astimezone(_VN_TZ).strftime("%d/%m/%Y %H:%M")

        if not result.signals and not result.triggered_alerts:
            await self.send_ok(
                interaction,
                title="📡 Watchlist Scan",
                description=f"No signals or triggered alerts at this time.\n🕐 Scanned at {scanned_at_str}",
            )
            return

        embed = discord.Embed(
            title="📡 Watchlist Scan Results",
            color=discord.Color.orange(),
        )

        if result.triggered_alerts:
            alert_lines = [
                f"🔔 **{a.ticker}** — {_CONDITION_LABEL.get(AlertConditionType(a.condition_type), a.condition_type)} {a.threshold:,.0f}"
                for a in result.triggered_alerts[:10]
            ]
            embed.add_field(
                name=f"Triggered Alerts ({len(result.triggered_alerts)})",
                value="\n".join(alert_lines),
                inline=False,
            )

        if result.signals:
            signal_lines = [
                f"📊 **{s.ticker}** — {s.signal_type}: {s.description[:60]}"
                for s in result.signals[:10]
            ]
            embed.add_field(
                name=f"Signals ({len(result.signals)})",
                value="\n".join(signal_lines),
                inline=False,
            )

        embed.set_footer(text=f"🕐 Latest scan: {scanned_at_str} · /watchlist alert to add alerts")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /watchlist alert
    # ------------------------------------------------------------------

    @group.command(name="alert", description="Set a price or volume alert for a ticker")
    @app_commands.describe(
        ticker="Stock ticker to monitor",
        condition="Alert condition type",
        threshold="Threshold value (price in VND, % for change, ratio for volume)",
    )
    @app_commands.choices(
        condition=[
            app_commands.Choice(name="Price above (VND)", value="price_above"),
            app_commands.Choice(name="Price below (VND)", value="price_below"),
            app_commands.Choice(name="Daily change up (%)", value="change_pct_up"),
            app_commands.Choice(name="Daily change down (%)", value="change_pct_down"),
            app_commands.Choice(name="Volume spike (ratio)", value="volume_spike"),
        ]
    )
    async def watchlist_alert(
        self,
        interaction: discord.Interaction,
        ticker: str,
        condition: str,
        threshold: float,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)
        condition_type = AlertConditionType(condition)

        try:
            async with self.db_session() as session:
                svc = WatchlistService(session)
                alert = await svc.add_alert(
                    AddAlertInput(
                        user_id=user_id,
                        ticker=ticker.upper(),
                        condition_type=condition_type,
                        threshold=threshold,
                    )
                )
        except WatchlistItemNotFoundError:
            await self.send_error(
                interaction,
                title="Ticker not in watchlist",
                description=(
                    f"**{ticker.upper()}** is not in your watchlist.\nUse `/watchlist add` first."
                ),
            )
            return
        except Exception as exc:
            logger.error("watchlist_alert.error", ticker=ticker, error=str(exc))
            await self.send_error(interaction, title="Lỗi", description=str(exc))
            return

        label = _CONDITION_LABEL.get(condition_type, condition)
        unit = "VND" if "price" in condition else ("%" if "pct" in condition else "x")
        await self.send_ok(
            interaction,
            title="🔔 Alert set",
            description=(
                f"**{ticker.upper()}** · {label} {threshold:,.0f} {unit}\n"
                f"Alert ID: #{alert.id} · Active"
            ),
        )
