"""Help command cog.

Owner: bot segment.
Commands:
    /help   — show paginated embed with all available commands grouped by module.

Adapter only: no business logic, no DB access.
Data is defined inline; update HELP_DATA when commands change.
"""

from __future__ import annotations

from typing import TypedDict

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog

# ---------------------------------------------------------------------------
# Help data — update này khi thêm/bớt command
# ---------------------------------------------------------------------------

class CommandEntry(TypedDict):
    usage: str
    description: str
    example: str | None

class GroupEntry(TypedDict):
    label: str       # tên hiển thị trên dropdown
    emoji: str
    colour: int      # discord.Color int
    intro: str       # 1-line mô tả nhóm
    commands: list[CommandEntry]

HELP_DATA: dict[str, GroupEntry] = {
    "market": {
        "label": "Market · Giá cổ phiếu",
        "emoji": "📈",
        "colour": 0x0078D7,
        "intro": "Tra giá realtime cho một hoặc nhiều mã cổ phiếu.",
        "commands": [
            {
                "usage": "/quote <ticker>",
                "description": "Giá realtime cho một mã (HOSE/HNX/UPCoM).",
                "example": "/quote HPG",
            },
            {
                "usage": "/quote_bulk <tickers>",
                "description": "Giá cho nhiều mã cùng lúc, cách nhau dấu phẩy (tối đa 10).",
                "example": "/quote_bulk HPG,VNM,FPT",
            },
        ],
    },
    "watchlist": {
        "label": "Watchlist · Danh sách theo dõi",
        "emoji": "👁️",
        "colour": 0x00B96B,
        "intro": "Quản lý danh sách theo dõi, cảnh báo giá, và quét tín hiệu.",
        "commands": [
            {
                "usage": "/watchlist add <ticker> [note]",
                "description": "Thêm mã vào watchlist, kèm ghi chú tuỳ chọn.",
                "example": "/watchlist add VNM Theo dõi breakout",
            },
            {
                "usage": "/watchlist remove <ticker>",
                "description": "Xoá mã khỏi watchlist.",
                "example": "/watchlist remove VNM",
            },
            {
                "usage": "/watchlist list",
                "description": "Hiển thị toàn bộ watchlist kèm giá realtime.",
                "example": None,
            },
            {
                "usage": "/watchlist scan",
                "description": "Quét tín hiệu kỹ thuật và kiểm tra cảnh báo đang active trên toàn bộ watchlist.",
                "example": None,
            },
            {
                "usage": "/watchlist alert <ticker> <condition> <threshold>",
                "description": (
                    "Đặt cảnh báo giá/thay đổi/volume. Condition: "
                    "`price_above`, `price_below`, `change_pct_up`, `change_pct_down`, `volume_spike`."
                ),
                "example": "/watchlist alert HPG price_above 55000",
            },
        ],
    },
    "thesis": {
        "label": "Thesis · Investment thesis",
        "emoji": "📝",
        "colour": 0x7B2FBE,
        "intro": "Tạo, theo dõi, và review AI cho investment thesis của bạn.",
        "commands": [
            {
                "usage": "/thesis add <ticker> <title> <entry_price> <target_price> <stop_loss> [summary]",
                "description": "Tạo investment thesis mới với giá vào, mục tiêu, và stop-loss.",
                "example": "/thesis add HPG Thesis Q2 45000 60000 40000",
            },
            {
                "usage": "/thesis list [status]",
                "description": "Xem danh sách thesis. Status: `active` (default), `paused`, `closed`, `invalidated`, `all`.",
                "example": "/thesis list active",
            },
            {
                "usage": "/thesis close <thesis_id> <reason>",
                "description": "Đóng thesis (`closed` = đạt target/exit) hoặc huỷ (`invalidated` = thesis không còn valid).",
                "example": "/thesis close 12 closed",
            },
            {
                "usage": "/review_thesis <thesis_id>",
                "description": "Chạy AI review: verdict (Bullish/Bearish/Neutral/Watchlist), risk signals, next watch items, confidence score.",
                "example": "/review_thesis 12",
            },
        ],
    },
    "briefing": {
        "label": "Briefing · Bản tin thị trường",
        "emoji": "🗞️",
        "colour": 0xF5A623,
        "intro": "Bản tin AI tổng hợp thị trường, cá nhân hoá theo watchlist của bạn.",
        "commands": [
            {
                "usage": "/morning_brief",
                "description": "Bản tin buổi sáng: tổng quan thị trường, watchlist highlight, macro/sector context.",
                "example": None,
            },
            {
                "usage": "/eod_brief",
                "description": "Bản tin cuối phiên: diễn biến ngày, phân tích watchlist, điểm cần theo dõi ngày mai.",
                "example": None,
            },
        ],
    },
    "analysis": {
        "label": "Analysis · Phân tích biến động",
        "emoji": "🔍",
        "colour": 0xE8534A,
        "intro": "AI phân tích nguyên nhân tăng/giảm đột biến của một mã cổ phiếu.",
        "commands": [
            {
                "usage": "/why <ticker>",
                "description": (
                    "Giải thích nguyên nhân tăng/giảm đột biến: "
                    "nguyên nhân kỹ thuật, cơ bản, macro context, risk flags và độ tin cậy phân tích."
                ),
                "example": "/why HPG",
            },
        ],
    },
}

_OVERVIEW_COLOUR = 0x4F8EF7


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
    lines = []
    for key, data in HELP_DATA.items():
        count = len(data["commands"])
        lines.append(f"{data['emoji']} **{data['label']}** — {count} lệnh")

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
