"""ProactiveDiscoverySubscriber — bot segment (thin adapter).

Owner: bot segment. No domain logic.

Subscribes to ProactiveDiscoveryReadyEvent and sends a rich Discord embed
to the alert channel. Each pick gets its own field with colour-coded action.

Lifecycle:
    subscriber = ProactiveDiscoverySubscriber(bot)
    subscriber.register()   ← called in bot/app.py on_ready
"""
from __future__ import annotations

import json
from typing import Any

import discord

from src.platform.config import settings
from src.platform.event_bus import get_event_bus
from src.platform.events import ProactiveDiscoveryReadyEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Action → colour + emoji
_ACTION_META: dict[str, tuple[str, discord.Color]] = {
    "BUY_WATCH":  ("👀 Theo dõi vào",  discord.Color.green()),
    "ACCUMULATE": ("📈 Tích lũy thêm", discord.Color.blue()),
    "AVOID":      ("⚠️ Tránh hôm nay", discord.Color.red()),
}


def _pick_emoji(action: str) -> str:
    return _ACTION_META.get(action, ("❓", discord.Color.light_grey()))[0]


def _embed_colour(picks: list[dict[str, Any]]) -> discord.Color:
    """Top pick's action drives embed colour."""
    if not picks:
        return discord.Color.light_grey()
    top_action = picks[0].get("action", "")
    return _ACTION_META.get(top_action, ("", discord.Color.gold()))[1]


def _build_embed(event: ProactiveDiscoveryReadyEvent) -> discord.Embed:
    """Build rich Discord embed from ProactiveDiscoveryReadyEvent."""
    picks: list[dict[str, Any]] = []
    try:
        picks = json.loads(event.picks_json) if event.picks_json else []
    except Exception:
        picks = []

    colour = _embed_colour(picks)
    title  = f"🧠 Proactive Discovery — {event.trading_date or 'Today'}"
    desc   = event.market_regime_note or "Market scan completed."

    embed = discord.Embed(title=title, description=desc, colour=colour)

    # ── Picks ────────────────────────────────────────────────────────────────
    for pick in picks:
        ticker     = pick.get("ticker", "?")
        action     = pick.get("action", "BUY_WATCH")
        verdict    = pick.get("verdict", "")
        entry      = pick.get("entry_logic", "")
        fit        = pick.get("portfolio_fit", "")
        catalyst   = pick.get("upside_catalyst", "")
        invalidate = pick.get("invalidation_condition", "")
        confidence = pick.get("confidence", 0.0)
        signal     = pick.get("signal_basis", "")
        emoji      = _pick_emoji(action)
        conf_str   = f"{int(confidence * 100)}%"

        field_lines = [
            f"{emoji} **{action}**  ·  confidence {conf_str}  ·  signal: `{signal}`",
            f"**Verdict:** {verdict}",
            f"**Entry:** {entry}",
            f"**Portfolio fit:** {fit}",
        ]
        if catalyst:
            field_lines.append(f"**Catalyst:** {catalyst}")
        if invalidate:
            field_lines.append(f"**Invalidation:** {invalidate}")

        embed.add_field(
            name=f"── {ticker} ──",
            value="\n".join(field_lines)[:1000],
            inline=False,
        )

    # ── Portfolio gaps ───────────────────────────────────────────────────────
    if event.portfolio_gaps:
        embed.add_field(
            name="📭 Sector gaps trong danh mục",
            value=", ".join(f"`{g}`" for g in event.portfolio_gaps),
            inline=True,
        )

    # ── Avoid list ───────────────────────────────────────────────────────────
    if event.avoid_tickers:
        embed.add_field(
            name="🚫 Tránh hôm nay",
            value=", ".join(f"**{t}**" for t in event.avoid_tickers),
            inline=True,
        )

    embed.set_footer(
        text=f"{event.picks_count} picks  ·  stock-agent proactive discovery  ·  {event.trading_date}"
    )
    return embed


class ProactiveDiscoverySubscriber:
    """Push ProactiveDiscoveryReadyEvent as Discord embed to alert channel."""

    def __init__(self, bot: discord.ext.commands.Bot) -> None:
        self._bot = bot
        self._registered = False

    def register(self) -> None:
        """Subscribe to EventBus. Idempotent."""
        if self._registered:
            return
        get_event_bus().subscribe_handler(ProactiveDiscoveryReadyEvent, self._handle)
        self._registered = True
        logger.info("proactive_discovery_subscriber.registered")

    async def _handle(self, event: ProactiveDiscoveryReadyEvent) -> None:
        """Handle event — resolve channel, build embed, send."""
        channel = await self._resolve_channel()
        if channel is None:
            logger.warning(
                "proactive_discovery_subscriber.no_channel",
                hint="Set DISCORD_ALERT_CHANNEL_ID in .env",
            )
            return

        embed = _build_embed(event)
        try:
            await channel.send(embed=embed)
            logger.info(
                "proactive_discovery_subscriber.sent",
                user_id=event.user_id,
                picks_count=event.picks_count,
                channel_id=channel.id,
                trading_date=event.trading_date,
            )
        except discord.DiscordException as exc:
            logger.warning(
                "proactive_discovery_subscriber.send_failed",
                error=str(exc),
            )

    async def _resolve_channel(self) -> discord.TextChannel | None:
        """Resolve alert channel from settings. Returns None when not configured."""
        channel_id_str = settings.alert_channel_id or None
        if not channel_id_str:
            return None
        try:
            channel_id = int(channel_id_str)
        except (TypeError, ValueError):
            return None

        channel = self._bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except discord.NotFound:
                return None

        return channel if isinstance(channel, discord.TextChannel) else None
