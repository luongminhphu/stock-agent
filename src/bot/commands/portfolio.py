"""Portfolio commands — bot adapter for portfolio segment.

Owner: bot segment (adapter only).
Domain logic lives in PortfolioService and PnlService.

Commands:
  /buy  <ticker> <qty> <price>   — open/add to position
  /sell <ticker> <qty> <price>   — reduce/close position
  /portfolio [ticker]            — full portfolio or single position P&L
  /history [ticker]              — realized trade history
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service
from src.portfolio.service import InsufficientQtyError, PortfolioService, PositionNotFoundError
from src.portfolio.pnl_service import PnlService


class PortfolioCog(BaseCog):
    """Slash commands for portfolio tracking."""

    # ------------------------------------------------------------------
    # /buy
    # ------------------------------------------------------------------

    @app_commands.command(name="buy", description="Ghi nhận lệnh mua vào portfolio")
    @app_commands.describe(
        ticker="Mã cổ phiếu (VD: VCB)",
        qty="Số cổ phiếu",
        price="Giá mua (VND)",
        note="Ghi chú tùy chọn",
    )
    async def buy(
        self,
        interaction: discord.Interaction,
        ticker: str,
        qty: float,
        price: float,
        note: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if qty <= 0 or price <= 0:
            await self.send_error(interaction, "Giá trị không hợp lệ", "qty và price phải > 0")
            return

        async with self.db_session() as session:
            svc = PortfolioService(session)
            position, trade = await svc.buy(
                user_id=user_id,
                ticker=ticker,
                qty=qty,
                price=price,
                note=note,
            )

        embed = discord.Embed(
            title=f"✅ Mua {position.ticker}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Số cổ", value=f"{trade.qty:,.0f}", inline=True)
        embed.add_field(name="Giá mua", value=self.fmt_vnd(trade.price), inline=True)
        embed.add_field(name="Giá vốn TB mới", value=self.fmt_vnd(position.avg_cost), inline=True)
        embed.add_field(name="Tổng đang giữ", value=f"{position.qty:,.0f} cổ", inline=True)
        embed.add_field(
            name="Chi phí vốn",
            value=self.fmt_vnd(position.avg_cost * position.qty),
            inline=True,
        )
        if note:
            embed.add_field(name="Ghi chú", value=note, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /sell
    # ------------------------------------------------------------------

    @app_commands.command(name="sell", description="Ghi nhận lệnh bán khỏi portfolio")
    @app_commands.describe(
        ticker="Mã cổ phiếu (VD: VCB)",
        qty="Số cổ phiếu bán",
        price="Giá bán (VND)",
        note="Ghi chú tùy chọn",
    )
    async def sell(
        self,
        interaction: discord.Interaction,
        ticker: str,
        qty: float,
        price: float,
        note: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if qty <= 0 or price <= 0:
            await self.send_error(interaction, "Giá trị không hợp lệ", "qty và price phải > 0")
            return

        try:
            async with self.db_session() as session:
                svc = PortfolioService(session)
                position, trade = await svc.sell(
                    user_id=user_id,
                    ticker=ticker,
                    qty=qty,
                    price=price,
                    note=note,
                )
        except PositionNotFoundError:
            await self.send_error(
                interaction,
                "Không tìm thấy vị thế",
                f"Bạn chưa có vị thế mở nào với **{ticker.upper()}**.",
            )
            return
        except InsufficientQtyError as exc:
            await self.send_error(interaction, "Số cổ không đủ", str(exc))
            return

        pnl = trade.realized_pnl or 0.0
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        is_closed = position.closed_at is not None

        embed = discord.Embed(
            title=f"{pnl_icon} Bán {position.ticker}{' — Vị thế đóng' if is_closed else ''}",
            color=discord.Color.green() if pnl >= 0 else discord.Color.red(),
        )
        embed.add_field(name="Số cổ bán", value=f"{trade.qty:,.0f}", inline=True)
        embed.add_field(name="Giá bán", value=self.fmt_vnd(trade.price), inline=True)
        embed.add_field(name="Giá vốn TB", value=self.fmt_vnd(position.avg_cost), inline=True)
        embed.add_field(
            name="Lời/Lỗ thực hiện",
            value=f"{pnl_icon} {self.fmt_vnd(abs(pnl))} ({'lời' if pnl >= 0 else 'lỗ'})",
            inline=True,
        )
        if not is_closed:
            embed.add_field(
                name="Còn giữ", value=f"{position.qty:,.0f} cổ", inline=True
            )
        if note:
            embed.add_field(name="Ghi chú", value=note, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /portfolio
    # ------------------------------------------------------------------

    @app_commands.command(name="portfolio", description="Xem P&L danh mục hiện tại")
    @app_commands.describe(ticker="Để trống để xem tất cả, hoặc nhập mã cổ phiếu cụ thể")
    async def portfolio(
        self,
        interaction: discord.Interaction,
        ticker: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        async with self.db_session() as session:
            svc = PnlService(session=session, quote_service=get_quote_service())

            if ticker:
                await self._send_single_position(interaction, svc, user_id, ticker)
            else:
                await self._send_full_portfolio(interaction, svc, user_id)

    async def _send_full_portfolio(
        self,
        interaction: discord.Interaction,
        svc: PnlService,
        user_id: str,
    ) -> None:
        pnl = await svc.get_portfolio_pnl(user_id)

        if not pnl.positions and not pnl.errors:
            await self.send_info(
                interaction,
                "💼 Portfolio trống",
                "Bạn chưa có vị thế nào đang mở.\nDùng `/buy <ticker> <qty> <price>` để bắt đầu.",
            )
            return

        total_icon = "🟢" if pnl.total_unrealized_pnl >= 0 else "🔴"
        lines: list[str] = []
        for p in pnl.positions:
            icon = "🟢" if p.unrealized_pnl >= 0 else "🔴"
            lines.append(
                f"{icon} **{p.ticker}** {p.qty:,.0f} cổ • "
                f"GB {self.fmt_vnd(p.avg_cost)} → HT {self.fmt_vnd(p.current_price)} • "
                f"P&L {self.fmt_vnd(p.unrealized_pnl)} ({self.fmt_pct(p.unrealized_pct)})"
            )

        for ticker_err, err in pnl.errors.items():
            lines.append(f"⚠️ **{ticker_err}** — không lấy được giá: {err}")

        body, footer = self.paginate_lines(lines)
        embed = discord.Embed(
            title=f"💼 Portfolio — {total_icon} {self.fmt_vnd(pnl.total_unrealized_pnl)} ({self.fmt_pct(pnl.total_unrealized_pct)})",
            description=body,
            color=discord.Color.green() if pnl.total_unrealized_pnl >= 0 else discord.Color.red(),
        )
        embed.add_field(name="Vốn", value=self.fmt_vnd(pnl.total_cost_basis), inline=True)
        embed.add_field(name="Thị giá", value=self.fmt_vnd(pnl.total_market_value), inline=True)
        embed.add_field(name="Vị thế", value=str(len(pnl.positions)), inline=True)
        if footer:
            embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _send_single_position(
        self,
        interaction: discord.Interaction,
        svc: PnlService,
        user_id: str,
        ticker: str,
    ) -> None:
        p = await svc.get_position_pnl(user_id, ticker)

        if p is None:
            await self.send_info(
                interaction,
                f"💼 {ticker.upper()} — Không có vị thế",
                f"Bạn chưa có vị thế mở nào với **{ticker.upper()}**.",
            )
            return

        icon = "🟢" if p.unrealized_pnl >= 0 else "🔴"
        embed = discord.Embed(
            title=f"💼 {p.ticker} — {icon} {self.fmt_vnd(p.unrealized_pnl)} ({self.fmt_pct(p.unrealized_pct)})",
            color=discord.Color.green() if p.unrealized_pnl >= 0 else discord.Color.red(),
        )
        embed.add_field(name="Số cổ đang giữ", value=f"{p.qty:,.0f}", inline=True)
        embed.add_field(name="Giá vốn TB", value=self.fmt_vnd(p.avg_cost), inline=True)
        embed.add_field(name="Giá hiện tại", value=self.fmt_vnd(p.current_price), inline=True)
        embed.add_field(name="Chi phí vốn", value=self.fmt_vnd(p.cost_basis), inline=True)
        embed.add_field(name="Thị giá", value=self.fmt_vnd(p.market_value), inline=True)
        if p.thesis_id:
            embed.add_field(name="Thesis", value=f"#{p.thesis_id}", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /history
    # ------------------------------------------------------------------

    @app_commands.command(name="history", description="Xem lịch sử giao dịch đã thực hiện")
    @app_commands.describe(ticker="Để trống để xem tất cả, hoặc nhập mã cổ phiếu cụ thể")
    async def history(
        self,
        interaction: discord.Interaction,
        ticker: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        async with self.db_session() as session:
            svc = PnlService(session=session, quote_service=get_quote_service())
            trades = await svc.get_trade_history(user_id, ticker=ticker, limit=20)
            summary = await svc.get_realized_summary(user_id, ticker=ticker)

        if not trades:
            await self.send_info(
                interaction,
                "📜 Lịch sử trống",
                "Chưa có giao dịch nàođược ghi nhận.",
            )
            return

        lines: list[str] = []
        for t in trades:
            icon = "🟢" if t.trade_type == "buy" else ("🟢" if (t.realized_pnl or 0) >= 0 else "🔴")
            pnl_str = (
                f" | P&L {self.fmt_vnd(t.realized_pnl)}"
                if t.realized_pnl is not None
                else ""
            )
            date_str = t.traded_at.strftime("%d/%m %H:%M") if t.traded_at else "?"
            lines.append(
                f"{icon} `{date_str}` **{t.ticker}** {t.trade_type.upper()} "
                f"{t.qty:,.0f} cổ @ {self.fmt_vnd(t.price)}{pnl_str}"
            )

        body, footer_hint = self.paginate_lines(lines)

        wr = summary.win_rate
        wr_str = f"{wr:.0%}" if wr is not None else "N/A"
        summary_line = (
            f"Realized P&L: **{self.fmt_vnd(summary.total_realized_pnl)}** • "
            f"Win rate: **{wr_str}** ({summary.win_trades}W / {summary.loss_trades}L)"
        )

        title = f"📜 Lịch sử{f' {ticker.upper()}' if ticker else ''}"
        embed = discord.Embed(title=title, description=body, color=0x4F98A3)
        embed.add_field(name="Tổng kết", value=summary_line, inline=False)
        footer = " — ".join(filter(None, [footer_hint, f"{summary.total_trades} giao dịch"]))
        if footer:
            embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PortfolioCog(bot))
