"""Discord helper — centralized rendering, sending, and embed building.

Owner: bot segment.

This module is the single source of truth for all Discord presentation
concerns in stock-agent. All *_embeds.py files should gradually migrate
their shared constants and utilities here.

Public surface:
    Constants     : COLORS, VERDICT_ICONS, STATUS_ICONS
    Utilities     : confidence_bar(), ict_now(), fmt_ict(), truncate()
    Safe send     : safe_send(), safe_followup(), safe_edit()
    Embed builder : EmbedBuilder (fluent)
    Ready-made    : build_engine_verdict_embed(), build_error_embed(),
                    build_loading_embed(), build_empty_embed(),
                    build_success_embed()
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Sequence

import discord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color palette — single source of truth for all embed sidebar colors
# ---------------------------------------------------------------------------

class COLORS:
    """Hex sidebar colors shared across all embed builders."""
    GREEN  = 0x57F287   # bullish, correct, success
    RED    = 0xED4245   # bearish, incorrect, error
    ORANGE = 0xFF6B35   # drift alert, warning, mixed
    TEAL   = 0x4F98A3   # neutral, info, default
    GOLD   = 0xD4A017   # review, conviction
    PURPLE = 0x9B59B6   # AI / engine verdict
    GREY   = 0x95A5A6   # inactive, paused, no-data
    BLUE   = 0x3498DB   # watchlist, lessons


# ---------------------------------------------------------------------------
# Emoji icon maps — shared across thesis, decision, engine segments
# ---------------------------------------------------------------------------

# Engine verdict type → icon
ENGINE_VERDICT_ICONS: dict[str, str] = {
    "BUY_SIGNAL":     "\U0001f7e2",   # 🟢
    "SELL_SIGNAL":    "\U0001f534",   # 🔴
    "HOLD":           "\U0001f7e1",   # 🟡
    "REVIEW_THESIS":  "\U0001f4cb",   # 📋
    "RISK_ALERT":     "\u26a0\ufe0f", # ⚠️
    "NO_ACTION":      "\u23f8\ufe0f", # ⏸️
}

ENGINE_VERDICT_COLORS: dict[str, int] = {
    "BUY_SIGNAL":     COLORS.GREEN,
    "SELL_SIGNAL":    COLORS.RED,
    "HOLD":           COLORS.TEAL,
    "REVIEW_THESIS":  COLORS.GOLD,
    "RISK_ALERT":     COLORS.ORANGE,
    "NO_ACTION":      COLORS.GREY,
}

# Review verdict → icon (mirrors thesis_embeds._VERDICT_ICON)
VERDICT_ICONS: dict[str, str] = {
    "BULLISH":   "\U0001f7e2",   # 🟢
    "BEARISH":   "\U0001f534",   # 🔴
    "NEUTRAL":   "\U0001f7e1",   # 🟡
    "WATCHLIST": "\U0001f535",   # 🔵
    "CORRECT":   "\u2705",       # ✅
    "INCORRECT": "\u274c",       # ❌
    "MIXED":     "\u2696\ufe0f", # ⚖️
}

# Thesis status → icon (mirrors thesis_embeds.STATUS_ICON)
STATUS_ICONS: dict[str, str] = {
    "ACTIVE":      "\U0001f7e2",     # 🟢
    "PAUSED":      "\u23f8\ufe0f",  # ⏸️
    "INVALIDATED": "\u274c",         # ❌
    "CLOSED":      "\u2705",          # ✅
}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

FOOTER_BRAND = "stock-agent"
_ICT_OFFSET = datetime.timezone(datetime.timedelta(hours=7))


def ict_now() -> datetime.datetime:
    """Return current time in ICT (UTC+7)."""
    return datetime.datetime.now(_ICT_OFFSET)


def fmt_ict(dt: datetime.datetime | None = None, fmt: str = "%H:%M ICT %d/%m/%Y") -> str:
    """Format a datetime as ICT string. Defaults to now if dt is None."""
    if dt is None:
        dt = ict_now()
    elif dt.tzinfo is None:
        # Assume naive datetime is UTC
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(_ICT_OFFSET)
    else:
        dt = dt.astimezone(_ICT_OFFSET)
    return dt.strftime(fmt)


def confidence_bar(confidence: float, length: int = 10) -> str:
    """Return a Unicode block progress bar for a 0..1 confidence value.

    Example: confidence_bar(0.7) → '███████░░░'
    """
    confidence = max(0.0, min(1.0, confidence))
    filled = round(confidence * length)
    return "\u2588" * filled + "\u2591" * (length - filled)


def truncate(text: str, limit: int = 1024, suffix: str = "…") -> str:
    """Truncate text to Discord field/description character limits."""
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix


def chunk_message(text: str, limit: int = 2000) -> list[str]:
    """Split a long message into chunks that fit within Discord's limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Safe send helpers — handle HTTPException, chunking, rate-limit retry
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_DELAY = 1.5  # seconds


async def safe_send(
    channel: discord.abc.Messageable,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = False,
    split_long: bool = True,
) -> discord.Message | None:
    """Send a message to a channel with error handling and auto-chunking.

    - Splits content > 2000 chars into multiple messages if split_long=True.
    - Retries up to _MAX_RETRIES times on rate-limit (429) errors.
    - Logs and returns None on unrecoverable errors (never raises).

    Args:
        channel:    Any Discord messageable (TextChannel, DMChannel, etc.).
        content:    Text content. Auto-chunked if > 2000 chars.
        embed:      Single embed (mutually exclusive with embeds).
        embeds:     List of embeds (max 10 per Discord API).
        view:       Optional discord.ui.View attached to final message.
        ephemeral:  Ignored for channels (only meaningful for interactions).
        split_long: If True, split long content into multiple messages.

    Returns:
        The last discord.Message sent, or None if all attempts failed.
    """
    kwargs: dict[str, Any] = {}
    if embed is not None:
        kwargs["embed"] = embed
    if embeds is not None:
        kwargs["embeds"] = embeds[:10]  # Discord hard limit
    if view is not None:
        kwargs["view"] = view

    messages_to_send: list[str | None] = []
    if content and split_long:
        chunks = chunk_message(content)
        messages_to_send = chunks
    else:
        messages_to_send = [content]

    last_message: discord.Message | None = None
    for i, chunk in enumerate(messages_to_send):
        send_kwargs = dict(kwargs) if i == len(messages_to_send) - 1 else {}
        if chunk:
            send_kwargs["content"] = chunk

        for attempt in range(_MAX_RETRIES):
            try:
                last_message = await channel.send(**send_kwargs)
                break
            except discord.HTTPException as exc:
                if exc.status == 429:  # rate limited
                    retry_after = getattr(exc, "retry_after", _RETRY_DELAY)
                    logger.warning(
                        "discord_helper.rate_limited",
                        retry_after=retry_after,
                        attempt=attempt + 1,
                    )
                    await asyncio.sleep(retry_after or _RETRY_DELAY)
                else:
                    logger.error(
                        "discord_helper.send_failed",
                        status=exc.status,
                        text=exc.text,
                        attempt=attempt + 1,
                    )
                    break
            except Exception as exc:  # noqa: BLE001
                logger.error("discord_helper.send_unexpected", error=str(exc))
                break

    return last_message


async def safe_followup(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> discord.WebhookMessage | None:
    """Send a followup message to a slash command interaction.

    - Defaults to ephemeral=True (only visible to command caller).
    - Truncates content to 2000 chars automatically.
    - Never raises — logs and returns None on failure.
    """
    if content and len(content) > 2000:
        content = truncate(content, 2000)

    kwargs: dict[str, Any] = {"ephemeral": ephemeral}
    if content:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if embeds is not None:
        kwargs["embeds"] = embeds[:10]
    if view is not None:
        kwargs["view"] = view

    for attempt in range(_MAX_RETRIES):
        try:
            return await interaction.followup.send(**kwargs)
        except discord.HTTPException as exc:
            if exc.status == 429:
                retry_after = getattr(exc, "retry_after", _RETRY_DELAY)
                await asyncio.sleep(retry_after or _RETRY_DELAY)
            else:
                logger.error(
                    "discord_helper.followup_failed",
                    status=exc.status,
                    text=exc.text,
                    attempt=attempt + 1,
                )
                break
        except Exception as exc:  # noqa: BLE001
            logger.error("discord_helper.followup_unexpected", error=str(exc))
            break
    return None


async def safe_edit(
    message: discord.Message,
    content: str | None = discord.utils.MISSING,
    *,
    embed: discord.Embed | None = discord.utils.MISSING,
    view: discord.ui.View | None = discord.utils.MISSING,
) -> discord.Message | None:
    """Edit an existing message safely.

    Only passes kwargs that are explicitly set (not MISSING) to avoid
    unintentionally clearing fields.
    """
    kwargs: dict[str, Any] = {}
    if content is not discord.utils.MISSING:
        kwargs["content"] = content
    if embed is not discord.utils.MISSING:
        kwargs["embed"] = embed
    if view is not discord.utils.MISSING:
        kwargs["view"] = view

    for attempt in range(_MAX_RETRIES):
        try:
            return await message.edit(**kwargs)
        except discord.HTTPException as exc:
            if exc.status == 429:
                await asyncio.sleep(getattr(exc, "retry_after", _RETRY_DELAY) or _RETRY_DELAY)
            else:
                logger.error(
                    "discord_helper.edit_failed",
                    status=exc.status,
                    text=exc.text,
                    attempt=attempt + 1,
                )
                break
        except Exception as exc:  # noqa: BLE001
            logger.error("discord_helper.edit_unexpected", error=str(exc))
            break
    return None


# ---------------------------------------------------------------------------
# EmbedBuilder — fluent builder with standardized footer and truncation
# ---------------------------------------------------------------------------

class EmbedBuilder:
    """Fluent Discord embed builder with stock-agent brand standards.

    Usage:
        embed = (
            EmbedBuilder(title="📊 Market Update", color=COLORS.TEAL)
            .description("VN-Index tăng 0.8% trong phiên sáng.")
            .field("VNINDEX", "1,285.4 (+0.8%)", inline=True)
            .field("Volume", "420M cp", inline=True)
            .footer("Auto-generated")
            .build()
        )
    """

    def __init__(
        self,
        title: str = "",
        color: int | discord.Color = COLORS.TEAL,
    ) -> None:
        self._embed = discord.Embed(title=title, color=color)

    def description(self, text: str, limit: int = 4096) -> "EmbedBuilder":
        self._embed.description = truncate(text, limit)
        return self

    def field(
        self,
        name: str,
        value: str,
        *,
        inline: bool = False,
        limit: int = 1024,
    ) -> "EmbedBuilder":
        """Add a field, auto-truncating value to Discord's 1024-char limit."""
        self._embed.add_field(
            name=truncate(name, 256),
            value=truncate(value, limit),
            inline=inline,
        )
        return self

    def footer(
        self,
        text: str = "",
        *,
        brand: bool = True,
        timestamp: bool = True,
    ) -> "EmbedBuilder":
        """Set footer with optional brand tag and ICT timestamp."""
        parts: list[str] = []
        if text:
            parts.append(text)
        if brand:
            parts.append(FOOTER_BRAND)
        if timestamp:
            parts.append(fmt_ict(fmt="%H:%M ICT"))
        self._embed.set_footer(text=" · ".join(parts))
        return self

    def timestamp(self, dt: datetime.datetime | None = None) -> "EmbedBuilder":
        """Set Discord embed timestamp (shown as relative time in Discord UI)."""
        self._embed.timestamp = dt or datetime.datetime.now(datetime.timezone.utc)
        return self

    def thumbnail(self, url: str) -> "EmbedBuilder":
        self._embed.set_thumbnail(url=url)
        return self

    def image(self, url: str) -> "EmbedBuilder":
        self._embed.set_image(url=url)
        return self

    def author(self, name: str, icon_url: str | None = None) -> "EmbedBuilder":
        self._embed.set_author(name=name, icon_url=icon_url)
        return self

    def build(self) -> discord.Embed:
        """Return the constructed discord.Embed."""
        return self._embed


# ---------------------------------------------------------------------------
# Ready-made embed builders — standardized UX states
# ---------------------------------------------------------------------------

def build_engine_verdict_embed(verdict: Any) -> discord.Embed:
    """Build a rich embed from an EngineVerdict (src.core.schemas.EngineVerdict).

    Compatible with both ORM objects and Pydantic models — uses getattr.

    Args:
        verdict: EngineVerdict instance with fields:
                 verdict, confidence, risk_signals, next_watch_items,
                 action, reasoning_summary, sources.

    Returns:
        discord.Embed ready to send.
    """
    verdict_type = str(getattr(verdict, "verdict", "NO_ACTION")).upper()
    confidence = float(getattr(verdict, "confidence", 0.0))
    risk_signals: list[str] = getattr(verdict, "risk_signals", []) or []
    next_watch: list[str] = getattr(verdict, "next_watch_items", []) or []
    action: str = getattr(verdict, "action", "") or ""
    reasoning: str = getattr(verdict, "reasoning_summary", "") or ""
    sources: list[str] = getattr(verdict, "sources", []) or []

    icon = ENGINE_VERDICT_ICONS.get(verdict_type, "\U0001f9e0")
    color = ENGINE_VERDICT_COLORS.get(verdict_type, COLORS.PURPLE)

    builder = (
        EmbedBuilder(
            title=f"{icon} Intelligence Engine — {verdict_type.replace('_', ' ')}",
            color=color,
        )
        .description(reasoning or "_Không có reasoning summary._")
    )

    # Confidence bar
    bar = confidence_bar(confidence)
    builder.field("Confidence", f"{bar} `{confidence:.0%}`", inline=True)

    # Action
    if action:
        builder.field("\U0001f3af Action", action, inline=False)

    # Risk signals
    if risk_signals:
        builder.field(
            "\u26a0\ufe0f Risk Signals",
            "\n".join(f"• {r}" for r in risk_signals[:5]),
            inline=False,
        )

    # Next watch items
    if next_watch:
        builder.field(
            "\U0001f441\ufe0f Watch Next",
            "\n".join(f"• {w}" for w in next_watch[:5]),
            inline=False,
        )

    # Sources
    if sources:
        builder.field(
            "\U0001f4e1 Sources",
            ", ".join(sources[:8]),
            inline=True,
        )

    builder.footer("Engine verdict", brand=True, timestamp=True)
    return builder.build()


def build_error_embed(
    title: str = "\u274c Lỗi",
    description: str = "Đã xảy ra lỗi không mong muốn. Vui lòng thử lại sau.",
    *,
    context: str | None = None,
) -> discord.Embed:
    """Build a standardized error embed.

    Args:
        title:       Embed title. Defaults to '❌ Lỗi'.
        description: Human-readable error message.
        context:     Optional technical context (command name, ticker, etc.)
                     shown as a footer field.

    Returns:
        discord.Embed with red sidebar color.
    """
    builder = (
        EmbedBuilder(title=title, color=COLORS.RED)
        .description(description)
        .footer(context or "", brand=True, timestamp=True)
    )
    return builder.build()


def build_loading_embed(
    title: str = "\u23f3 Đang xử lý…",
    description: str = "AI đang phân tích. Vui lòng chờ.",
) -> discord.Embed:
    """Build a standardized loading/thinking state embed.

    Returns:
        discord.Embed with grey sidebar and no timestamp (state is transient).
    """
    return (
        EmbedBuilder(title=title, color=COLORS.GREY)
        .description(description)
        .footer(brand=True, timestamp=False)
        .build()
    )


def build_empty_embed(
    entity: str = "dữ liệu",
    hint: str | None = None,
) -> discord.Embed:
    """Build a standardized empty-state embed.

    Args:
        entity: What is empty (e.g. 'watchlist', 'thesis', 'tín hiệu').
        hint:   Optional action hint shown in description.

    Returns:
        discord.Embed with grey sidebar.
    """
    description = f"Chưa có {entity} nào."
    if hint:
        description += f"\n\n_{hint}_"
    return (
        EmbedBuilder(title=f"\U0001f4ed {entity.capitalize()} trống", color=COLORS.GREY)
        .description(description)
        .footer(brand=True, timestamp=True)
        .build()
    )


def build_success_embed(
    title: str = "\u2705 Thành công",
    description: str = "",
    *,
    context: str | None = None,
) -> discord.Embed:
    """Build a standardized success confirmation embed.

    Returns:
        discord.Embed with green sidebar.
    """
    return (
        EmbedBuilder(title=title, color=COLORS.GREEN)
        .description(description)
        .footer(context or "", brand=True, timestamp=True)
        .build()
    )
