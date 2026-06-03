"""SignalReactionListener — captures Discord emoji reactions as investor signals.

Owner: bot segment (thin adapter).
Domain logic owner:
  - ai segment (MemoryService.log_user_signal)
  - watchlist segment (AlertService.record_reaction — Wave D)
  - thesis segment (DecisionService.record_execution_signal — Wave E)

Wave B rationale:
  Investors naturally react to AI briefings / scan alerts with emoji.
  This listener intercepts those reactions and converts them into
  structured UserBehaviorLog entries via MemoryService.log_user_signal()

  The mapping is intentionally minimal and unambiguous:
    ✅  → bought      (positive action taken)
    🔴  → sold        (exit action taken)
    👀  → watched     (acknowledged, monitoring)
    ⏭️  → ignored     (deliberate pass)
    🚩  → flagged     (needs attention / risk flag)

  Only reactions on messages sent by this bot are captured to avoid
  polluting the signal table with unrelated conversation reactions.

Wave D addition:
  After logging to MemoryService, this listener also calls
  AlertService.record_reaction() in a fire-and-forget isolated session
  so that repeated "ignored" reactions on the same alert trigger adaptive
  cooldown escalation.

  Alert ID linkage: the bot embeds alert_id in the message footer as
  "alert:{id}" when sending scan alerts (see bot/commands/scan.py).
  This listener parses that footer. If no alert_id is found, the
  MemoryService log still proceeds — only the cooldown feedback is skipped.

Wave E addition:
  For "bought" and "sold" signals, also notifies thesis.DecisionService
  so the thesis lifecycle can track confirmed position changes. This
  closes the feedback loop: reaction → memory log → thesis update.
  Runs in a separate isolated AsyncSession; never blocks Wave B or Wave D.

Contract:
  - Input:  discord.RawReactionActionEvent
  - Output: MemoryService.log_user_signal()
            + AlertService.record_reaction()       [Wave D, alert msgs only]
            + DecisionService.record_execution_signal() [Wave E, bought/sold]
  - Zero domain logic here — emoji → signal string → forward to services
"""

from __future__ import annotations

import re

import discord
from discord.ext import commands

from src.ai.memory.memory_service import MemoryService
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Emoji → user_signal mapping.
EMOJI_SIGNAL_MAP: dict[str, str] = {
    "✅": "bought",
    "🔴": "sold",
    "👀": "watched",
    "⏭️": "ignored",
    "🚩": "flagged",
}

# Signals that represent a confirmed position change — forward to DecisionService.
_EXECUTION_SIGNALS = {"bought", "sold"}

# Pattern for alert_id embedded in message footer by bot/commands/scan.py.
# Example footer text: "alert:42" or "... | alert:42"
_ALERT_ID_RE = re.compile(r"alert:(\d+)")

# Common Vietnamese finance / generic terms to exclude from ticker extraction.
_TICKER_STOP: frozenset[str] = frozenset({
    "AI", "OK", "DM", "ID", "API", "BOT", "VND", "USD", "ETF",
    "TP", "MR", "NN", "SX", "VN", "HN", "PE", "ROE", "EPS",
    "NW", "PB", "PS", "BUY", "SELL", "HOLD", "SL", "NAV",
    "TT", "TK", "KL", "GT", "GD", "TC", "CE", "FL",
})


class SignalReactionListener:
    """Registers an on_raw_reaction_add listener on the Discord bot."""

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
        """Core handler — filter, map, forward to MemoryService + AlertService + DecisionService."""
        if event.user_id == self._bot.user.id:  # type: ignore[union-attr]
            return

        emoji_str = str(event.emoji)
        signal = EMOJI_SIGNAL_MAP.get(emoji_str)
        if signal is None:
            return

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
            return

        ticker = _extract_ticker(message)
        user_id = str(event.user_id)

        # Step 1: log to MemoryService (existing Wave B behaviour).
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

        # Step 2: Wave D — record reaction on the alert for adaptive cooldown.
        # Parse alert_id from message footer/content. Non-fatal if absent.
        alert_id = _extract_alert_id(message)
        if alert_id is not None:
            await _record_alert_reaction(user_id=user_id, alert_id=alert_id, signal=signal)

        # Step 3: Wave E — forward execution signals to thesis DecisionService.
        # Only "bought" and "sold" carry enough intent to update thesis lifecycle.
        if signal in _EXECUTION_SIGNALS and ticker:
            await _forward_execution_to_thesis(
                user_id=user_id,
                signal=signal,
                ticker=ticker,
                source_message_id=event.message_id,
            )


def _extract_ticker(message: discord.Message) -> str | None:
    """Extract ticker symbol from a bot message.

    Resolution order (most reliable → least reliable):
      1. Embed fields whose name contains "ticker", "mã", or "symbol".
      2. Embed title — tickers often appear as the first ALL-CAPS word.
      3. Regex on raw message content (fallback, filtered by STOP list).
    """
    # 1. Structured embed fields
    for embed in message.embeds:
        for field in embed.fields:
            if field.name and any(
                kw in field.name.lower() for kw in ("ticker", "mã", "symbol")
            ):
                value = (field.value or "").strip().upper()
                if value and value not in _TICKER_STOP:
                    return value

        # 2. Embed title — first ALL-CAPS token of length 2-5
        if embed.title:
            m = re.match(r"^([A-Z]{2,5})\b", embed.title)
            if m and m.group(1) not in _TICKER_STOP:
                return m.group(1)

    # 3. Raw content regex fallback
    matches = re.findall(r"\b([A-Z]{2,5})\b", message.content or "")
    for candidate in matches:
        if candidate not in _TICKER_STOP:
            return candidate

    return None


def _extract_alert_id(message: discord.Message) -> int | None:
    """Parse alert_id from message embeds footer or raw content.

    Bot commands/scan.py embeds alert_id in the footer as "alert:{id}".
    Falls back to searching message.content so plain-text scan messages
    also work.
    """
    # Check embed footers first (structured)
    for embed in message.embeds:
        if embed.footer and embed.footer.text:
            m = _ALERT_ID_RE.search(embed.footer.text)
            if m:
                return int(m.group(1))

    # Fallback: raw content
    if message.content:
        m = _ALERT_ID_RE.search(message.content)
        if m:
            return int(m.group(1))

    return None


async def _record_alert_reaction(user_id: str, alert_id: int, signal: str) -> None:
    """Fire-and-forget: call AlertService.record_reaction() in isolated session.

    Runs in its own AsyncSession so a DB error here never affects the
    MemoryService write above or the Discord event loop.
    """
    try:
        from src.platform.bootstrap import get_session_factory  # noqa: PLC0415
        from src.watchlist.alert_service import AlertService  # noqa: PLC0415

        session_factory = get_session_factory()
        if session_factory is None:
            return

        async with session_factory() as session:
            svc = AlertService(session)
            await svc.record_reaction(
                alert_id=alert_id,
                user_id=user_id,
                reaction=signal,
            )
            await session.commit()

        logger.debug(
            "signal_reaction_listener.alert_reaction_recorded",
            alert_id=alert_id,
            user_id=user_id,
            signal=signal,
        )

    except Exception as exc:
        logger.warning(
            "signal_reaction_listener.alert_reaction_failed",
            alert_id=alert_id,
            user_id=user_id,
            signal=signal,
            error=str(exc),
        )


async def _forward_execution_to_thesis(
    user_id: str,
    signal: str,
    ticker: str,
    source_message_id: int,
) -> None:
    """Fire-and-forget: notify thesis.DecisionService of a confirmed position change.

    Runs in its own AsyncSession so a DB error here never affects Wave B
    (MemoryService) or Wave D (AlertService) paths.

    Signals forwarded:
      "bought" → DecisionService.record_execution_signal(action="buy", ...)
      "sold"   → DecisionService.record_execution_signal(action="sell", ...)
    """
    action = "buy" if signal == "bought" else "sell"
    try:
        from src.platform.bootstrap import get_session_factory  # noqa: PLC0415
        from src.thesis.decision_service import DecisionService  # noqa: PLC0415

        session_factory = get_session_factory()
        if session_factory is None:
            return

        async with session_factory() as session:
            svc = DecisionService(session)
            await svc.record_execution_signal(
                user_id=user_id,
                ticker=ticker,
                action=action,
                source="discord_reaction",
                source_ref=str(source_message_id),
            )
            await session.commit()

        logger.info(
            "signal_reaction_listener.thesis_execution_recorded",
            user_id=user_id,
            ticker=ticker,
            action=action,
            source_message_id=source_message_id,
        )

    except Exception as exc:
        logger.warning(
            "signal_reaction_listener.thesis_forward_failed",
            user_id=user_id,
            ticker=ticker,
            action=action,
            error=str(exc),
        )
