"""SignalReactionListener — captures Discord emoji reactions as investor signals.

Owner: bot segment (thin adapter).
Domain logic owner: ai segment (MemoryService.log_user_signal).

Wave B rationale:
  Investors naturally react to AI briefings / scan alerts with emoji.
  This listener intercepts those reactions and converts them into
  structured UserBehaviorLog entries via MemoryService.log_user_signal().

  The mapping is intentionally minimal and unambiguous:
    ✅  → bought      (positive action taken)
    🔴  → sold        (exit action taken)
    👀  → watched     (acknowledged, monitoring)
    ⏭️  → ignored     (deliberate pass)
    🚩  → flagged     (needs attention / risk flag)

  Only reactions on messages sent by this bot are captured to avoid
  polluting the signal table with unrelated conversation reactions.

Contract:
  - Input:  discord.RawReactionActionEvent
  - Output: MemoryService.log_user_signal() call (fire-and-forget)
  - Zero domain logic here — emoji → signal string → forward to service

Side-effects:
  - Writes one UserBehaviorLog row per reaction
  - Back-fills AIInteractionLog.user_signal if message_id is linked
    (best-effort via MemoryService; no FK lookup in this adapter)
"""

from __future__ import annotations

import discord
from discord.ext import commands

from src.ai.memory.memory_service import MemoryService
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Emoji → user_signal mapping.
# Only reactions in this set are captured; all others are silently ignored.
EMOJI_SIGNAL_MAP: dict[str, str] = {
    "✅": "bought",
    "🔴": "sold",
    "👀": "watched",
    "⏭️": "ignored",
    "🚩": "flagged",
}


class SignalReactionListener:
    """Registers an on_raw_reaction_add listener on the Discord bot.

    Only thin adapter logic lives here:
      1. Filter: only bot messages, only mapped emoji, only human reactors.
      2. Map: emoji str → signal str.
      3. Forward: MemoryService.log_user_signal() — fire-and-forget.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self._bot = bot

    def register(self) -> None:
        """Attach on_raw_reaction_add to the bot event loop."""
        bot = self._bot

        @bot.event
        async def on_raw_reaction_add(event: discord.RawReactionActionEvent) -> None:
            await self._handle(event)

        logger.info("signal_reaction_listener.registered")

    async def _handle(self, event: discord.RawReactionActionEvent) -> None:
        """Core handler — filter, map, forward."""
        # 1. Skip bot's own reactions
        if event.user_id == self._bot.user.id:  # type: ignore[union-attr]
            return

        # 2. Only process mapped emoji
        emoji_str = str(event.emoji)
        signal = EMOJI_SIGNAL_MAP.get(emoji_str)
        if signal is None:
            return

        # 3. Verify the message was sent by this bot
        channel = self._bot.get_channel(event.channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(event.channel_id)
            except Exception:
                logger.debug(
                    "signal_reaction_listener.channel_fetch_failed",
                    channel_id=event.channel_id,
                )
                return

        try:
            message = await channel.fetch_message(event.message_id)  # type: ignore[union-attr]
        except Exception:
            logger.debug(
                "signal_reaction_listener.message_fetch_failed",
                message_id=event.message_id,
            )
            return

        if message.author.id != self._bot.user.id:  # type: ignore[union-attr]
            return  # not a bot message — ignore

        # 4. Extract ticker from message content (best-effort, no required)
        ticker = _extract_ticker(message.content)

        # 5. Fire-and-forget signal write
        user_id = str(event.user_id)
        ok = await MemoryService.log_user_signal(
            user_id=user_id,
            signal=signal,
            ticker=ticker,
            source="discord_reaction",
        )

        if ok:
            logger.info(
                "signal_reaction_listener.signal_logged",
                user_id=user_id,
                signal=signal,
                ticker=ticker,
                message_id=event.message_id,
            )
        else:
            logger.warning(
                "signal_reaction_listener.signal_failed",
                user_id=user_id,
                signal=signal,
                message_id=event.message_id,
            )


def _extract_ticker(content: str) -> str | None:
    """Best-effort: find first 2-4 uppercase alpha token in message.

    Matches common Vietnamese ticker patterns like VNM, HPG, TCB.
    Returns None if nothing looks like a ticker.
    """
    import re
    matches = re.findall(r'\b([A-Z]{2,4})\b', content or "")
    # Filter out common Discord / English words that look like tickers
    _STOP = {"AI", "OK", "DM", "ID", "API", "BOT", "VND", "USD", "ETF"}
    for m in matches:
        if m not in _STOP:
            return m
    return None
