"""Discord helper — single source of truth for all Discord presentation concerns.

Owner: bot segment.

═══════════════════════════════════════════════════════════════════════════════
 DESIGN CONTRACT
═══════════════════════════════════════════════════════════════════════════════

 1. COLORS         — all embed sidebar colors. Never use discord.Color.* or
                     raw hex literals in command files.

 2. Icon maps       — ENGINE_VERDICT_ICONS, ENGINE_VERDICT_COLORS,
                     VERDICT_ICONS, VERDICT_COLORS, STATUS_ICONS, STATUS_COLORS

 3. Format helpers  — fmt_vnd(), fmt_pct(), fmt_ict(), ict_now(),
                     confidence_bar(), truncate(), chunk_message(),
                     paginate_lines()

 4. Safe send       — safe_defer(), safe_send(), safe_followup(), safe_edit(),
                     safe_response()
                     All raise nothing — log and return None on failure.

 5. Interaction shortcuts — send_ok(), send_error(), send_info(),
                     send_warning(), send_loading()
                     Wrap the common (defer → build embed → followup) pattern.
                     Use these in ALL command handlers instead of calling
                     followup.send() directly.

 6. EmbedBuilder    — fluent builder with auto-footer, auto-truncation.

 7. Ready-made embeds — build_engine_verdict_embed(), build_thesis_review_embed(),
                     build_proactive_alert_embed(), build_error_embed(),
                     build_loading_embed(), build_empty_embed(), build_success_embed()

═══════════════════════════════════════════════════════════════════════════════
 fmt_pct / fmt_vnd CONTRACT
═══════════════════════════════════════════════════════════════════════════════

 fmt_pct(value):   value is a RATIO (0.082 → '+8.2%').
                   If your value is already a percentage (8.2), divide by 100
                   or use  f'{value:+.1f}%'  directly.

 fmt_vnd(price):   price is raw VND integer (25_400 → '25,400').
                   Outputs K/M/B suffix for large values.
                   For 'N/A' fallback pass None.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Sequence
from typing import Any

import discord

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Color palette — SSOT for all embed sidebar colors
# ─────────────────────────────────────────────────────────────────────────────

class COLORS:
    """Hex sidebar colors — never use discord.Color.* or raw hex in commands.

    Semantic mapping:
        GREEN   → bullish / correct / success / ACTIVE / portfolio up
        RED     → bearish / incorrect / error / INVALIDATED / portfolio down
        ORANGE  → drift / warning / mixed / WEAKENING / risk alert
        TEAL    → neutral / info / default / HOLD / portfolio neutral
        GOLD    → review / conviction / pending action
        PURPLE  → AI / engine / memory / intelligence
        GREY    → inactive / paused / no-data / loading / no action
        BLUE    → watchlist / lessons / scanner / info detail
    """
    GREEN  = 0x57F287
    RED    = 0xED4245
    ORANGE = 0xFF6B35
    TEAL   = 0x4F98A3
    GOLD   = 0xD4A017
    PURPLE = 0x9B59B6
    GREY   = 0x95A5A6
    BLUE   = 0x3498DB

    # Semantic aliases — prefer these in new code
    BULLISH    = GREEN
    BEARISH    = RED
    WEAKENING  = ORANGE
    NEUTRAL    = TEAL
    SUCCESS    = GREEN
    ERROR      = RED
    WARNING    = ORANGE
    LOADING    = GREY
    INFO       = BLUE
    AI         = PURPLE


# ─────────────────────────────────────────────────────────────────────────────
# Emoji / icon maps
# ─────────────────────────────────────────────────────────────────────────────

ENGINE_VERDICT_ICONS: dict[str, str] = {
    "BUY_SIGNAL":    "🟢",
    "SELL_SIGNAL":   "🔴",
    "HOLD":          "🟡",
    "REVIEW_THESIS": "📋",
    "RISK_ALERT":    "⚠️",
    "NO_ACTION":     "⏸️",
}

ENGINE_VERDICT_COLORS: dict[str, int] = {
    "BUY_SIGNAL":    COLORS.GREEN,
    "SELL_SIGNAL":   COLORS.RED,
    "HOLD":          COLORS.TEAL,
    "REVIEW_THESIS": COLORS.GOLD,
    "RISK_ALERT":    COLORS.ORANGE,
    "NO_ACTION":     COLORS.GREY,
}

VERDICT_ICONS: dict[str, str] = {
    "BULLISH":     "🟢",
    "BEARISH":     "🔴",
    "WEAKENING":   "🟠",
    "NEUTRAL":     "🟡",
    "INVALIDATED": "❌",
    "WATCHLIST":   "🔵",
    "CORRECT":     "✅",
    "INCORRECT":   "❌",
    "MIXED":       "⚖️",
}

VERDICT_COLORS: dict[str, int] = {
    "BULLISH":     COLORS.GREEN,
    "BEARISH":     COLORS.RED,
    "WEAKENING":   COLORS.ORANGE,
    "NEUTRAL":     COLORS.TEAL,
    "INVALIDATED": COLORS.RED,
    "WATCHLIST":   COLORS.BLUE,
    "CORRECT":     COLORS.GREEN,
    "INCORRECT":   COLORS.RED,
    "MIXED":       COLORS.ORANGE,
}

STATUS_ICONS: dict[str, str] = {
    "ACTIVE":      "🟢",
    "PAUSED":      "⏸️",
    "WEAKENING":   "🟠",
    "INVALIDATED": "❌",
    "CLOSED":      "✅",
}

STATUS_COLORS: dict[str, int] = {
    "ACTIVE":      COLORS.GREEN,
    "PAUSED":      COLORS.GREY,
    "WEAKENING":   COLORS.ORANGE,
    "INVALIDATED": COLORS.RED,
    "CLOSED":      COLORS.TEAL,
}

_AGENT_STATUS_ICONS: dict[str, str] = {
    "ran":     "✅",
    "failed":  "❌",
    "skipped": "⏭️",
}

_URGENCY_PREFIX: dict[str, str] = {
    "CRITICAL": "🚨",
    "HIGH":     "🔴",
    "MEDIUM":   "🟠",
    "LOW":      "🟡",
    "NORMAL":   "🟡",
}


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

FOOTER_BRAND  = "stock-agent"
_ICT_OFFSET   = datetime.timezone(datetime.timedelta(hours=7))
_MAX_RETRIES  = 3
_RETRY_DELAY  = 1.5   # seconds


# ─────────────────────────────────────────────────────────────────────────────
# Format helpers
# ─────────────────────────────────────────────────────────────────────────────

def ict_now() -> datetime.datetime:
    """Return current time in ICT (UTC+7)."""
    return datetime.datetime.now(_ICT_OFFSET)


def fmt_ict(dt: datetime.datetime | None = None, fmt: str = "%H:%M ICT %d/%m/%Y") -> str:
    """Format a datetime as ICT string. Defaults to now if dt is None."""
    if dt is None:
        dt = ict_now()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(_ICT_OFFSET)
    else:
        dt = dt.astimezone(_ICT_OFFSET)
    return dt.strftime(fmt)


def fmt_vnd(price: float | int | None, decimals: int = 0) -> str:
    """Format a raw VND price with K/M/B suffix for compact display.

    CONTRACT: ``price`` is a raw VND integer (e.g. 25_400 for 25,400 VND).
    For large values uses suffix; for moderate values uses comma grouping.

    Examples::
        fmt_vnd(25_400)        → '25,400'
        fmt_vnd(1_500_000)     → '1.50M'
        fmt_vnd(85_000_000)    → '85.00M'
        fmt_vnd(1_200_000_000) → '1.20B'
        fmt_vnd(None)          → 'N/A'
    """
    if price is None:
        return "N/A"
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "N/A"
    if abs(p) >= 1_000_000_000:
        return f"{p / 1_000_000_000:.{decimals or 2}f}B"
    if abs(p) >= 1_000_000:
        return f"{p / 1_000_000:.{decimals or 2}f}M"
    if abs(p) >= 10_000:
        return f"{p:,.{decimals}f}"
    return f"{p:.{decimals}f}"


def fmt_vnd_full(price: float | int | None) -> str:
    """Format VND price with full comma grouping + 'VND' suffix.

    Use for portfolio values where the full number + unit matters.

    Examples::
        fmt_vnd_full(50_000)   → '50,000 VND'
        fmt_vnd_full(1_250_000)→ '1,250,000 VND'
        fmt_vnd_full(None)     → 'N/A'
    """
    if price is None:
        return "N/A"
    try:
        return f"{float(price):,.0f} VND"
    except (TypeError, ValueError):
        return "N/A"


def fmt_pct(
    value: float | None,
    decimals: int = 1,
    *,
    sign: bool = True,
) -> str:
    """Format a decimal RATIO as a percentage string.

    CONTRACT: ``value`` is a ratio (0.082 = 8.2%). NOT an already-multiplied
    percentage. If your value is already a percentage (e.g. 8.2), divide by
    100 first or use  ``f'{value:+.1f}%'``  directly.

    Examples::
        fmt_pct(0.0823)          → '+8.2%'
        fmt_pct(-0.034)          → '-3.4%'
        fmt_pct(0.0, sign=False) → '0.0%'
        fmt_pct(None)            → 'N/A'
    """
    if value is None:
        return "N/A"
    try:
        pct = float(value) * 100
    except (TypeError, ValueError):
        return "N/A"
    prefix = "+" if sign and pct > 0 else ""
    return f"{prefix}{pct:.{decimals}f}%"


def fmt_pct_direct(
    value: float | None,
    decimals: int = 1,
    *,
    sign: bool = True,
) -> str:
    """Format an already-multiplied percentage (e.g. 8.2 → '+8.2%').

    Use when the source value is already a percentage, not a ratio.
    Most domain objects store ratios — prefer ``fmt_pct`` unless certain.
    """
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    prefix = "+" if sign and v > 0 else ""
    return f"{prefix}{v:.{decimals}f}%"


def fmt_rr(rr: float | None, *, fallback: str = "N/A") -> str:
    """Format risk/reward ratio: 2.5 → '2.50x'."""
    if rr is None:
        return fallback
    try:
        return f"{float(rr):.2f}x"
    except (TypeError, ValueError):
        return fallback


def confidence_bar(confidence: float, length: int = 10) -> str:
    """Return a Unicode block progress bar for a 0..1 confidence value.

    Example: confidence_bar(0.7) → '███████░░░'
    """
    confidence = max(0.0, min(1.0, confidence))
    filled = round(confidence * length)
    return "█" * filled + "░" * (length - filled)


def truncate(text: str, limit: int = 1024, suffix: str = "…") -> str:
    """Truncate text to Discord field / description character limits.

    Common limits:
        embed.description: 4096
        embed field value:  1024
        embed field name:    256
        message content:    2000
    """
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix


def chunk_message(text: str, limit: int = 2000) -> list[str]:
    """Split a long message into chunks that fit within Discord's limit.

    Splits at newlines when possible to preserve readability.
    """
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


def paginate_lines(
    lines: Sequence[str],
    *,
    max_items: int = 20,
    max_chars: int = 4096,
) -> tuple[str, str]:
    """Truncate a list of lines to fit Discord embed limits.

    Returns:
        (body_text, footer_hint)
        footer_hint is non-empty when truncation happened — show it in the embed footer.

    Usage::
        body, hint = paginate_lines(rows, max_items=15)
        builder.description(body).footer(hint)
    """
    shown = list(lines[:max_items])
    body = "\n".join(shown)
    if len(body) > max_chars:
        body = body[: max_chars - 3] + "..."
    hidden = len(lines) - len(shown)
    footer_hint = f"Showing {len(shown)}/{len(lines)}" if hidden > 0 else ""
    return body, footer_hint


# ─────────────────────────────────────────────────────────────────────────────
# Safe send primitives — handle rate-limit, HTTPException, never raise
# ─────────────────────────────────────────────────────────────────────────────

async def safe_defer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = True,
    thinking: bool = True,
) -> bool:
    """Defer a slash command interaction safely.

    Call at the start of any handler that takes > 3 seconds.
    After deferring, use safe_followup() to send the actual response.

    Args:
        interaction: The discord.Interaction from the slash command.
        ephemeral:   If True, the indicator is only visible to the caller.
        thinking:    If True, shows Discord's "Bot is thinking…" indicator.

    Returns:
        True if deferred (or already responded), False on failure.
    """
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except discord.InteractionResponded:
        logger.debug("discord_helper.defer_already_responded")
        return True
    except discord.HTTPException as exc:
        logger.warning("discord_helper.defer_failed", extra={"status": exc.status, "text": exc.text})
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("discord_helper.defer_unexpected", extra={"error": str(exc)})
        return False


async def safe_response(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    ephemeral: bool = True,
) -> bool:
    """Send an immediate response to an interaction (no prior defer needed).

    Use only when you can respond within 3 seconds (e.g. input validation errors,
    quick owner checks). For slow operations use safe_defer() + safe_followup().

    Returns:
        True if sent, False on failure. Never raises.
    """
    kwargs: dict[str, Any] = {"ephemeral": ephemeral}
    if content:
        kwargs["content"] = truncate(content, 2000)
    if embed is not None:
        kwargs["embed"] = embed
    try:
        await interaction.response.send_message(**kwargs)
        return True
    except discord.InteractionResponded:
        # Already responded — fall back to followup
        try:
            await interaction.followup.send(**kwargs)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("discord_helper.response_followup_failed", extra={"error": str(exc)})
            return False
    except discord.HTTPException as exc:
        logger.error("discord_helper.response_failed", extra={"status": exc.status, "text": exc.text})
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("discord_helper.response_unexpected", extra={"error": str(exc)})
        return False


async def safe_followup(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> discord.WebhookMessage | None:
    """Send a followup message to a deferred slash command interaction.

    - Defaults to ephemeral=True (only visible to command caller).
    - Truncates content to 2000 chars automatically.
    - Retries up to _MAX_RETRIES times on rate-limit (429).
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
                    extra={"status": exc.status, "text": exc.text, "attempt": attempt + 1},
                )
                break
        except Exception as exc:  # noqa: BLE001
            logger.error("discord_helper.followup_unexpected", extra={"error": str(exc)})
            break
    return None


async def safe_send(
    channel: discord.abc.Messageable,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    view: discord.ui.View | None = None,
    split_long: bool = True,
) -> discord.Message | None:
    """Send a message to a channel with error handling and auto-chunking.

    - Splits content > 2000 chars into multiple messages if split_long=True.
    - Retries up to _MAX_RETRIES times on rate-limit (429) errors.
    - Never raises — logs and returns None on failure.

    Returns:
        The last discord.Message sent, or None on failure.
    """
    kwargs: dict[str, Any] = {}
    if embed is not None:
        kwargs["embed"] = embed
    if embeds is not None:
        kwargs["embeds"] = embeds[:10]
    if view is not None:
        kwargs["view"] = view

    chunks = chunk_message(content) if content and split_long else [content]
    last_message: discord.Message | None = None

    for i, chunk in enumerate(chunks):
        send_kwargs = dict(kwargs) if i == len(chunks) - 1 else {}
        if chunk:
            send_kwargs["content"] = chunk

        for attempt in range(_MAX_RETRIES):
            try:
                last_message = await channel.send(**send_kwargs)
                break
            except discord.HTTPException as exc:
                if exc.status == 429:
                    retry_after = getattr(exc, "retry_after", _RETRY_DELAY)
                    logger.warning(
                        "discord_helper.rate_limited",
                        extra={"retry_after": retry_after, "attempt": attempt + 1},
                    )
                    await asyncio.sleep(retry_after or _RETRY_DELAY)
                else:
                    logger.error(
                        "discord_helper.send_failed",
                        extra={"status": exc.status, "text": exc.text, "attempt": attempt + 1},
                    )
                    break
            except Exception as exc:  # noqa: BLE001
                logger.error("discord_helper.send_unexpected", extra={"error": str(exc)})
                break

    return last_message


async def safe_edit(
    message: discord.Message,
    content: str | None = discord.utils.MISSING,
    *,
    embed: discord.Embed | None = discord.utils.MISSING,
    view: discord.ui.View | None = discord.utils.MISSING,
) -> discord.Message | None:
    """Edit an existing message safely.

    Only passes kwargs that are explicitly set (not MISSING) to avoid
    unintentionally clearing existing fields.

    Returns:
        Updated discord.Message, or None on failure.
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
                    extra={"status": exc.status, "text": exc.text, "attempt": attempt + 1},
                )
                break
        except Exception as exc:  # noqa: BLE001
            logger.error("discord_helper.edit_unexpected", extra={"error": str(exc)})
            break
    return None


# ─────────────────────────────────────────────────────────────────────────────
# High-level interaction shortcuts
# ─────────────────────────────────────────────────────────────────────────────
# These wrap the common  (build embed → safe_followup)  pattern.
# Call after safe_defer() in command handlers.
#
# ALL command handlers SHOULD use these instead of calling
# followup.send() / discord.Embed() directly.
# ─────────────────────────────────────────────────────────────────────────────

async def send_ok(
    interaction: discord.Interaction,
    title: str,
    description: str = "",
    *,
    ephemeral: bool = True,
    context: str | None = None,
) -> None:
    """Send a green success embed as followup.

    Usage::
        await safe_defer(interaction)
        ...
        await send_ok(interaction, "✅ Đã tạo thesis", f"Thesis **{ticker}** đã được tạo.")
    """
    embed = (
        EmbedBuilder(title=title, color=COLORS.GREEN)
        .description(description)
        .footer(context or "", brand=True, timestamp=True)
        .build()
    )
    await safe_followup(interaction, embed=embed, ephemeral=ephemeral)


async def send_error(
    interaction: discord.Interaction,
    title: str = "❌ Lỗi",
    description: str = "Đã xảy ra lỗi không mong muốn.",
    *,
    ephemeral: bool = True,
    context: str | None = None,
) -> None:
    """Send a red error embed as followup.

    Usage::
        await safe_defer(interaction)
        ...
        await send_error(interaction, "❌ Không tìm thấy", f"Ticker **{ticker}** không có trong watchlist.")
    """
    embed = (
        EmbedBuilder(title=title, color=COLORS.RED)
        .description(truncate(description, 4096))
        .footer(context or "", brand=True, timestamp=True)
        .build()
    )
    await safe_followup(interaction, embed=embed, ephemeral=ephemeral)


async def send_info(
    interaction: discord.Interaction,
    title: str,
    description: str = "",
    *,
    ephemeral: bool = True,
    context: str | None = None,
    view: discord.ui.View | None = None,
) -> None:
    """Send a blue informational embed as followup.

    Use for neutral results, lists, scan results, reference data.
    """
    embed = (
        EmbedBuilder(title=title, color=COLORS.BLUE)
        .description(description)
        .footer(context or "", brand=True, timestamp=True)
        .build()
    )
    await safe_followup(interaction, embed=embed, view=view, ephemeral=ephemeral)


async def send_warning(
    interaction: discord.Interaction,
    title: str,
    description: str = "",
    *,
    ephemeral: bool = True,
    context: str | None = None,
) -> None:
    """Send an orange warning embed as followup.

    Use for partial results, rate-limit notices, degraded state.
    """
    embed = (
        EmbedBuilder(title=f"⚠️ {title}" if not title.startswith("⚠️") else title, color=COLORS.ORANGE)
        .description(description)
        .footer(context or "", brand=True, timestamp=True)
        .build()
    )
    await safe_followup(interaction, embed=embed, ephemeral=ephemeral)


async def send_loading(
    interaction: discord.Interaction,
    title: str = "⏳ Đang xử lý…",
    description: str = "AI đang phân tích. Vui lòng chờ.",
    *,
    ephemeral: bool = True,
) -> None:
    """Send a grey loading-state embed as an immediate response (no defer needed).

    Use in the rare case where you send a loading placeholder first, then
    edit it. For most commands just use safe_defer() — Discord shows
    "thinking…" automatically.
    """
    embed = (
        EmbedBuilder(title=title, color=COLORS.GREY)
        .description(description)
        .footer(brand=True, timestamp=False)
        .build()
    )
    await safe_response(interaction, embed=embed, ephemeral=ephemeral)


# ─────────────────────────────────────────────────────────────────────────────
# EmbedBuilder — fluent, auto-footer, auto-truncation
# ─────────────────────────────────────────────────────────────────────────────

class EmbedBuilder:
    """Fluent Discord embed builder with stock-agent brand standards.

    Usage::

        embed = (
            EmbedBuilder(title="📊 Market Update", color=COLORS.TEAL)
            .description("VN-Index tăng 0.8% trong phiên sáng.")
            .field("VNINDEX", "1,285.4 (+0.8%)", inline=True)
            .field("Volume", "420M cp", inline=True)
            .footer("Auto-generated")
            .build()
        )

    Limits enforced automatically:
        - description:    4096 chars
        - field value:    1024 chars
        - field name:      256 chars
        - footer text:    2048 chars
    """

    def __init__(
        self,
        title: str = "",
        color: int | discord.Color = COLORS.TEAL,
    ) -> None:
        self._embed = discord.Embed(title=truncate(title, 256), color=color)

    def description(self, text: str, limit: int = 4096) -> "EmbedBuilder":
        """Set embed description, auto-truncating to limit."""
        self._embed.description = truncate(text, limit) if text else ""
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
            value=truncate(value or "\u200b", limit),  # zero-width space if empty
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
        """Set footer with optional brand tag and ICT timestamp.

        Parts are joined with ' · '. Empty parts are skipped.
        """
        parts: list[str] = [p for p in [text, FOOTER_BRAND if brand else "", fmt_ict(fmt="%H:%M ICT") if timestamp else ""] if p]
        self._embed.set_footer(text=truncate(" · ".join(parts), 2048))
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
        self._embed.set_author(name=truncate(name, 256), icon_url=icon_url)
        return self

    def build(self) -> discord.Embed:
        """Return the constructed discord.Embed."""
        return self._embed


# ─────────────────────────────────────────────────────────────────────────────
# Internal rendering helpers
# ─────────────────────────────────────────────────────────────────────────────

def _render_priority_actions(actions: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """Render priority_actions list into a compact Discord field value.

    Shows at most 3 items. Handles missing keys gracefully.
    Each action dict should have: action, ticker, urgency, reasoning.
    """
    lines: list[str] = []
    for item in list(actions)[:3]:
        if not isinstance(item, dict):
            continue
        urgency    = str(item.get("urgency", "NORMAL")).upper()
        ticker     = str(item.get("ticker", "")).upper()
        action_txt = str(item.get("action", item.get("reasoning", "")))
        prefix     = _URGENCY_PREFIX.get(urgency, "🟡")
        ticker_lbl = f" **{ticker}**" if ticker else ""
        lines.append(f"{prefix}{ticker_lbl} — {truncate(action_txt, 120)}")
    return "\n".join(lines) if lines else "_Không có action cụ thể._"


def _render_agent_slots(slots: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """Render agent_slots into a compact status string.

    Skips heuristic_engine slot (internal). Shows agent short-name + status icon.
    """
    _SHORT: dict[str, str] = {
        "thesis_judge":            "thesis",
        "invalidation_detector":   "invalidation",
        "next_action_suggester":   "next_action",
        "portfolio_risk_narrator": "portfolio_risk",
    }
    parts: list[str] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        name   = str(slot.get("agent_name", ""))
        if name == "heuristic_engine":
            continue
        status = str(slot.get("status", "skipped")).lower()
        icon   = _AGENT_STATUS_ICONS.get(status, "❓")
        short  = _SHORT.get(name, name)
        parts.append(f"{short} {icon}")
    return "  ".join(parts) if parts else "_heuristic only_"


# ─────────────────────────────────────────────────────────────────────────────
# Ready-made embed builders
# ─────────────────────────────────────────────────────────────────────────────

def build_engine_verdict_embed(verdict: Any) -> discord.Embed:
    """Build a rich embed from an EngineVerdict / IntelligenceEngineCompletedEvent.

    Reads Wave C fields (agent_slots, priority_actions) when available.
    Falls back to legacy ``action`` string on heuristic path.
    Compatible with both ORM objects and Pydantic models via getattr.
    """
    verdict_type      = str(getattr(verdict, "verdict", "NO_ACTION")).upper()
    confidence        = float(getattr(verdict, "confidence", 0.0))
    risk_signals      = list(getattr(verdict, "risk_signals", []) or [])
    next_watch        = list(getattr(verdict, "next_watch_items", []) or [])
    action            = getattr(verdict, "action", "") or getattr(verdict, "summary", "") or ""
    reasoning         = getattr(verdict, "reasoning_summary", "") or ""
    sources           = list(getattr(verdict, "sources", []) or [])
    agent_slots       = getattr(verdict, "agent_slots", ()) or ()
    priority_actions  = getattr(verdict, "priority_actions", ()) or ()

    icon  = ENGINE_VERDICT_ICONS.get(verdict_type, "🧠")
    color = ENGINE_VERDICT_COLORS.get(verdict_type, COLORS.PURPLE)

    builder = (
        EmbedBuilder(
            title=f"{icon} Intelligence Engine — {verdict_type.replace('_', ' ')}",
            color=color,
        )
        .description(reasoning or "_Không có reasoning summary._")
    )

    builder.field("Confidence", f"{confidence_bar(confidence)} `{confidence:.0%}`", inline=True)

    if agent_slots:
        builder.field("🤖 Agents", _render_agent_slots(agent_slots), inline=True)

    if priority_actions:
        builder.field("🎯 Priority Actions", _render_priority_actions(priority_actions), inline=False)
    elif action:
        builder.field("🎯 Action", action, inline=False)

    if risk_signals:
        builder.field("⚠️ Risk Signals", "\n".join(f"• {r}" for r in risk_signals[:5]), inline=False)

    if next_watch:
        builder.field("👁️ Watch Next", "\n".join(f"• {w}" for w in next_watch[:5]), inline=False)

    if sources:
        builder.field("📡 Sources", ", ".join(sources[:8]), inline=True)

    builder.footer("Engine verdict", brand=True, timestamp=True)
    return builder.build()


def build_thesis_review_embed(review: Any, *, ticker: str = "") -> discord.Embed:
    """Build a standardized embed from a ThesisReview ORM/Pydantic object.

    Compatible with both ORM (ThesisReview) and Pydantic output models.
    Uses getattr — never raises on missing fields.
    """
    verdict_raw = str(getattr(review, "verdict", "NEUTRAL"))
    verdict     = verdict_raw.upper()
    if hasattr(verdict_raw, "value"):           # enum support
        verdict = str(verdict_raw.value).upper()

    confidence  = float(getattr(review, "confidence", 0.0) or 0.0)
    reasoning   = getattr(review, "reasoning", "") or ""
    risk_signals= getattr(review, "risk_signals", []) or []
    next_watch  = getattr(review, "next_watch_items", []) or []
    catalysts   = getattr(review, "catalysts_status", []) or []
    assumptions = getattr(review, "assumptions_status", []) or []
    created_at  = getattr(review, "created_at", None)

    icon  = VERDICT_ICONS.get(verdict, "🟡")
    color = VERDICT_COLORS.get(verdict, COLORS.TEAL)
    ticker_label = f" {ticker.upper()}" if ticker else ""

    builder = (
        EmbedBuilder(title=f"{icon} Thesis Review{ticker_label} — {verdict}", color=color)
        .description(reasoning or "_Không có reasoning._")
    )

    builder.field("Confidence", f"{confidence_bar(confidence)} `{confidence:.0%}`", inline=True)
    builder.field("Verdict", f"{icon} {verdict}", inline=True)

    if risk_signals:
        builder.field("⚠️ Risk Signals", "\n".join(f"• {r}" for r in risk_signals[:5]), inline=False)

    if next_watch:
        builder.field("👁️ Watch Next", "\n".join(f"• {w}" for w in next_watch[:5]), inline=False)

    if catalysts:
        lines: list[str] = []
        for c in catalysts[:5]:
            if isinstance(c, dict):
                status, name = c.get("status", ""), c.get("name", c.get("catalyst", ""))
            else:
                status = str(getattr(c, "status", ""))
                name   = str(getattr(c, "name", getattr(c, "catalyst", "")))
            s_icon = "✅" if "MET" in status.upper() else "⏳" if "PENDING" in status.upper() else "❌"
            lines.append(f"{s_icon} {name}")
        if lines:
            builder.field("📍 Catalysts", "\n".join(lines), inline=False)

    if assumptions:
        lines = []
        for a in assumptions[:5]:
            if isinstance(a, dict):
                status, name = a.get("status", ""), a.get("name", a.get("assumption", ""))
            else:
                status = str(getattr(a, "status", ""))
                name   = str(getattr(a, "name", getattr(a, "assumption", "")))
            s_icon = "✅" if "HOLD" in status.upper() else "❌" if "BROKEN" in status.upper() else "❓"
            lines.append(f"{s_icon} {name}")
        if lines:
            builder.field("📌 Assumptions", "\n".join(lines), inline=False)

    footer_text = fmt_ict(created_at, fmt="%H:%M ICT %d/%m/%Y") if created_at else ""
    builder.footer(footer_text, brand=True, timestamp=not bool(created_at))
    return builder.build()


def build_proactive_alert_embed(alert: Any, *, ticker: str = "") -> discord.Embed:
    """Build a standardized embed for a proactive watchlist/thesis alert.

    Compatible with ProactiveAlert, WatchlistTrigger, or any dict-like
    object with alert_type, message, ticker, urgency, reasons fields.
    """
    def _get(key: str, default: Any = "") -> Any:
        if isinstance(alert, dict):
            return alert.get(key, default)
        return getattr(alert, key, default) or default

    alert_type = str(_get("alert_type", "WATCH")).upper()
    message    = str(_get("message", _get("summary", "")))
    ticker_val = str(_get("ticker", ticker)).upper()
    urgency    = str(_get("urgency", "NORMAL")).upper()
    reasons    = _get("reasons", []) or []
    created_at = _get("created_at", None)

    urgency_map: dict[str, tuple[int, str]] = {
        "HIGH":   (COLORS.RED,    "🚨"),
        "MEDIUM": (COLORS.ORANGE, "⚠️"),
        "NORMAL": (COLORS.TEAL,   "📡"),
        "LOW":    (COLORS.GREY,   "🔔"),
    }
    color, icon = urgency_map.get(urgency, (COLORS.TEAL, "📡"))

    ticker_label = f" {ticker_val}" if ticker_val else ""
    title = f"{icon} Proactive Alert{ticker_label}"
    if alert_type and alert_type != "WATCH":
        title += f" — {alert_type.replace('_', ' ')}"

    builder = (
        EmbedBuilder(title=title, color=color)
        .description(message or "_Không có nội dung cảnh báo._")
    )
    builder.field("Urgency", urgency, inline=True)
    if ticker_val:
        builder.field("Ticker", ticker_val, inline=True)
    if reasons:
        builder.field("📋 Reasons", "\n".join(f"• {r}" for r in reasons[:5]), inline=False)

    footer_text = fmt_ict(created_at) if created_at else ""
    builder.footer(footer_text, brand=True, timestamp=not bool(created_at))
    return builder.build()


def build_error_embed(
    title: str = "❌ Lỗi",
    description: str = "Đã xảy ra lỗi không mong muốn. Vui lòng thử lại sau.",
    *,
    context: str | None = None,
) -> discord.Embed:
    """Build a standardized error embed.

    For command handlers prefer ``send_error(interaction, ...)`` instead.
    Use this when you need the Embed object itself (e.g. to send to a channel).
    """
    return (
        EmbedBuilder(title=title, color=COLORS.RED)
        .description(truncate(description, 4096))
        .footer(context or "", brand=True, timestamp=True)
        .build()
    )


def build_loading_embed(
    title: str = "⏳ Đang xử lý…",
    description: str = "AI đang phân tích. Vui lòng chờ.",
) -> discord.Embed:
    """Build a standardized loading/thinking state embed."""
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
        entity: What is empty — e.g. 'watchlist', 'thesis', 'tín hiệu'.
        hint:   Optional action hint shown in description.
    """
    description = f"Chưa có {entity} nào."
    if hint:
        description += f"\n\n_{hint}_"
    return (
        EmbedBuilder(title=f"📭 {entity.capitalize()} trống", color=COLORS.GREY)
        .description(description)
        .footer(brand=True, timestamp=True)
        .build()
    )


def build_success_embed(
    title: str = "✅ Thành công",
    description: str = "",
    *,
    context: str | None = None,
) -> discord.Embed:
    """Build a standardized success confirmation embed.

    For command handlers prefer ``send_ok(interaction, ...)`` instead.
    """
    return (
        EmbedBuilder(title=title, color=COLORS.GREEN)
        .description(description)
        .footer(context or "", brand=True, timestamp=True)
        .build()
    )
