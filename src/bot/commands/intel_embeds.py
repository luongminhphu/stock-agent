"""Discord embed builders for intelligence snapshot.

Owner: bot segment.
Input: dict from DashboardService.get_intelligence()
Output: list[discord.Embed] — max 3 embeds (Discord allows up to 10 per message).

Embed layout:
  [0] Verdict + Confidence   — colour-coded by verdict
  [1] Priority Actions       — top 3 action items
  [2] Risk Flags             — only included when risk_flags is non-empty

A stale-data footer is appended to embed[0] when is_stale=True.
"""

from __future__ import annotations

import discord

_VERDICT_COLOR: dict[str, discord.Color] = {
    "bullish":  discord.Color.green(),
    "cautious": discord.Color.orange(),
    "bearish":  discord.Color.red(),
    "neutral":  discord.Color.light_grey(),
}

_VERDICT_EMOJI: dict[str, str] = {
    "bullish":  "🟢",
    "cautious": "🟡",
    "bearish":  "🔴",
    "neutral":  "⚪",
}


def build_intel_embeds(data: dict) -> list[discord.Embed]:
    """Convert intelligence dict → list of Discord Embeds."""
    embeds: list[discord.Embed] = []

    embeds.append(_build_verdict_embed(data))

    actions_embed = _build_actions_embed(data)
    if actions_embed is not None:
        embeds.append(actions_embed)

    risk_embed = _build_risk_embed(data)
    if risk_embed is not None:
        embeds.append(risk_embed)

    return embeds


# ---------------------------------------------------------------------------
# Private builders
# ---------------------------------------------------------------------------

def _build_verdict_embed(data: dict) -> discord.Embed:
    verdict_raw = (data.get("overall_verdict") or "neutral").lower()
    verdict = verdict_raw if verdict_raw in _VERDICT_COLOR else "neutral"
    emoji = _VERDICT_EMOJI[verdict]
    color = _VERDICT_COLOR[verdict]
    confidence = data.get("confidence")  # float 0-1 or None

    title = f"{emoji} Intelligence Snapshot — {verdict.capitalize()}"
    lines: list[str] = []

    if confidence is not None:
        pct = int(round(confidence * 100))
        lines.append(f"**Confidence:** {pct}%")

    reasoning = data.get("reasoning_summary") or data.get("summary")
    if reasoning:
        lines.append(f"\n{reasoning}")

    embed = discord.Embed(
        title=title,
        description="\n".join(lines) if lines else "Không có mô tả.",
        color=color,
    )

    generated_at = data.get("generated_at")
    is_stale = data.get("is_stale", False)

    footer_parts: list[str] = []
    if generated_at:
        footer_parts.append(f"Cập nhật: {generated_at}")
    if is_stale:
        footer_parts.append("⚠️ Dữ liệu cũ — đang chờ refresh")

    if footer_parts:
        embed.set_footer(text=" · ".join(footer_parts))

    return embed


def _build_actions_embed(data: dict) -> discord.Embed | None:
    actions: list = data.get("priority_actions") or []
    if not actions:
        return None

    top = actions[:3]
    lines = [f"{i + 1}. {_fmt_action(a)}" for i, a in enumerate(top)]

    embed = discord.Embed(
        title="⚡ Priority Actions",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )

    hidden = len(actions) - len(top)
    if hidden > 0:
        embed.set_footer(text=f"+{hidden} action khác")

    return embed


def _build_risk_embed(data: dict) -> discord.Embed | None:
    flags: list = data.get("risk_flags") or []
    if not flags:
        return None

    lines = [f"• {_fmt_flag(f)}" for f in flags]
    embed = discord.Embed(
        title="⚠️ Risk Flags",
        description="\n".join(lines),
        color=discord.Color.red(),
    )
    return embed


# ---------------------------------------------------------------------------
# Micro-formatters  (handle both str and dict action/flag shapes)
# ---------------------------------------------------------------------------

def _fmt_action(action: str | dict) -> str:
    if isinstance(action, str):
        return action
    symbol = action.get("symbol", "")
    text   = action.get("action") or action.get("text") or str(action)
    return f"**{symbol}** — {text}" if symbol else text


def _fmt_flag(flag: str | dict) -> str:
    if isinstance(flag, str):
        return flag
    symbol   = flag.get("symbol", "")
    text     = flag.get("flag") or flag.get("text") or str(flag)
    severity = flag.get("severity", "")
    parts = []
    if symbol:
        parts.append(f"**{symbol}**")
    if severity:
        parts.append(f"[{severity.upper()}]")
    parts.append(text)
    return " ".join(parts)
