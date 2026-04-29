"""Help command cog.

Owner: bot segment.
Commands:
    /help   — show paginated embed with all available commands grouped by module.

Adapter only: no business logic, no DB access.
Data (HELP_DATA) lives in help_data.py — update that file when commands change.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.bot.commands.help_data import (
    HELP_DATA,
    CommandEntry,
    GroupEntry,
    _OVERVIEW_COLOUR,
)

# Re-export so existing imports from help.py stay valid
__all__ = ["HelpCog", "HelpView", "HELP_DATA", "CommandEntry", "GroupEntry"]


# ---------------------------------------------------------------------------
# Dropdown View
# ---------------------------------------------------------------------------


class _GroupSelect(discord.ui.Select):
    """Dropdown chọn nhóm command."""

    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label=data["label"],
                value=key,
                emoji=data["emoji"],
                description=data["intro"][:100],
            )
            for key, data in HELP_DATA.items()
        ]
        super().__init__(
            placeholder="📂 Chọn nhóm lệnh để xem chi tiết…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        embed = _build_group_embed(key)
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    """View chứa dropdown; timeout 120s để tự cleanup."""

    def __init__(self) -> None:
        super().__init__(timeout=120)
        self.add_item(_GroupSelect())

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


def _build_overview_embed() -> discord.Embed:
    """Embed tổng quan khi mới gõ /help."""
    lines = [
        f"{data['emoji']} **{data['label']}** — {len(data['commands'])} lệnh"
        for _key, data in HELP_DATA.items()
    ]
    embed = discord.Embed(
        title="📖 stock-agent — Danh sách lệnh",
        description=(
            "AI-native platform phân tích chứng khoán Việt Nam.\n"
            "Chọn nhóm lệnh từ dropdown bên dưới để xem chi tiết.\n\n"
            + "\n".join(lines)
        ),
        color=_OVERVIEW_COLOUR,
    )
    embed.set_footer(text="Thị trường: HOSE · HNX · UPCoM · stock-agent")
    return embed


def _build_group_embed(group_key: str) -> discord.Embed:
    """Embed chi tiết cho một nhóm command."""
    data = HELP_DATA[group_key]
    embed = discord.Embed(
        title=f"{data['emoji']} {data['label']}",
        description=data["intro"],
        color=data["colour"],
    )
    for cmd in data["commands"]:
        value = cmd["description"]
        if cmd.get("example"):
            value += f"\n> **Ví dụ:** `{cmd['example']}`"
        embed.add_field(
            name=f"`{cmd['usage']}`",
            value=value,
            inline=False,
        )
    embed.set_footer(text="← Dùng dropdown để xem nhóm khác · stock-agent")
    return embed


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class HelpCog(BaseCog):
    """Slash command: /help"""

    @app_commands.command(name="help", description="Xem danh sách và hướng dẫn tất cả các lệnh")
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = _build_overview_embed()
        view = HelpView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
