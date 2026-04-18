"""Watchlist commands cog.

Owner: bot segment.
Commands:
    /watchlist add     — add ticker
    /watchlist remove  — remove ticker
    /watchlist list    — show watchlist with live prices
    /watchlist scan    — run signal scan across watchlist
    /watchlist alert   — set price/change alert for a ticker

No business logic. All rules live in watchlist/market/scan segments.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service
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

_CONDITION_LABEL = {
    AlertConditionType.PRICE_ABOVE: "Price ≥",
    AlertConditionType.PRICE_BELOW: "Price ≤",
    AlertConditionType.CHANGE_PCT_UP: "Change ≥ +",
    AlertConditionType.CHANGE_PCT_DOWN: "Change ≤ -",
    AlertConditionType.VOLUME_SPIKE: "Volume ≥",
}


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
            await self.send_error(interaction, title="Error", description=str(exc))
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
                title="Not found",
                description=f"**{ticker.upper()}** is not in your watchlist.",
            )
            return
        except Exception as exc:
            logger.error("watchlist_remove.error", ticker=ticker, error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
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
                items = await svc.list_items(user_id)
        except Exception as exc:
            logger.error("watchlist_list.error", error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        if not items:
            await self.send_ok(
                interaction,
                title="📋 Your Watchlist",
                description="Your watchlist is empty.\nUse `/watchlist add <ticker>` to start.",
            )
            return

        # Fetch live prices in bulk
        qs = get_quote_service()
        tickers = [i.ticker for i in items]
        try:
            quotes = await qs.get_bulk_quotes(tickers)
            price_map = {q.ticker: q for q in quotes}
        except Exception:
            price_map = {}

        lines = []
        for item in items:
            q = price_map.get(item.ticker)
            if q:
                change_icon = "🔺" if q.change >= 0 else "🔻"
                price_str = f"{q.price:,.0f} ({change_icon}{q.change_pct:+.1f}%)"
            else:
                price_str = "N/A"
            note_part = f" · {item.note[:30]}" if item.note else ""
            lines.append(f"• **{item.ticker}** {price_str}{note_part}")

        embed = discord.Embed(
            title="📋 Your Watchlist",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{len(items)} ticker(s) · prices may be delayed")
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
                result = await svc.scan_for_user(user_id=user_id)
                await session.commit()
        except Exception as exc:
            logger.error("watchlist_scan.error", user_id=user_id, error=str(exc))
            await self.send_error(
                interaction,
                title="Scan failed",
                description=f"Could not complete watchlist scan.\n`{exc}`",
            )
            return

        if not result.signals and not result.triggered_alerts:
            await self.send_ok(
                interaction,
                title="📡 Watchlist Scan",
                description="No signals or triggered alerts at this time.",
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

        embed.set_footer(text="Scan complete · Use /watchlist alert to add price alerts")
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
            await self.send_error(interaction, title="Error", description=str(exc))
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
