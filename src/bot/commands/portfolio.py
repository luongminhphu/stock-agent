"""Portfolio commands — bot adapter for portfolio segment.

Owner: bot segment (adapter only).
Domain logic lives in PortfolioService, PnlService, and DashboardService.

Commands:
  /buy             <ticker> <qty> <price>          — open/add to position
  /sell            <ticker> <qty> <price>          — reduce/close position
  /correct_trade   <trade_id> <new_price>          — fix buy price + recalculate avg_cost
  /portfolio       [ticker] [view]                 — full portfolio or thesis-view
  /history         [ticker]                        — realized trade history (shows trade_id)
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service
from src.portfolio.service import (
    InsufficientQtyError,
    InvalidOperationError,
    PortfolioService,
    PositionNotFoundError,
    TradeNotFoundError,
)
from src.portfolio.pnl_service import PnlService
from src.readmodel.dashboard_service import DashboardService


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
        embed.set_footer(text=f"Trade ID: #{trade.id} — dùng /correct_trade {trade.id} <new_price> nếu nhập sai giá")
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
    # /correct_trade
    # ------------------------------------------------------------------

    @app_commands.command(
        name="correct_trade",
        description="Sửa giá mua sai của một BUY trade và tính lại giá vốn trung bình",
    )
    @app_commands.describe(
        trade_id="ID của trade cần sửa (xem trong /history hoặc footer của /buy)",
        new_price="Giá mua đúng (VND)",
    )
    async def correct_trade(
        self,
        interaction: discord.Interaction,
        trade_id: int,
        new_price: float,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if new_price <= 0:
            await self.send_error(interaction, "Giá không hợp lệ", "new_price phải > 0")
            return

        try:
            async with self.db_session() as session:
                svc = PortfolioService(session)
                position, trade = await svc.correct_trade(
                    user_id=user_id,
                    trade_id=trade_id,
                    new_price=new_price,
                )
        except TradeNotFoundError as exc:
            await self.send_error(interaction, "Không tìm thấy trade", str(exc))
            return
        except InvalidOperationError as exc:
            await self.send_error(interaction, "Không thể sửa trade này", str(exc))
            return

        embed = discord.Embed(
            title=f"✏️ Đã sửa Trade #{trade_id} — {trade.ticker}",
            color=0x4F98A3,
        )
        embed.add_field(name="Số cổ", value=f"{trade.qty:,.0f}", inline=True)
        embed.add_field(name="Giá mua mới", value=self.fmt_vnd(trade.price), inline=True)
        embed.add_field(name="Giá vốn TB sau sửa", value=self.fmt_vnd(position.avg_cost), inline=True)
        embed.add_field(name="Tổng đang giữ", value=f"{position.qty:,.0f} cổ", inline=True)
        embed.add_field(
            name="Chi phí vốn mới",
            value=self.fmt_vnd(position.avg_cost * position.qty),
            inline=True,
        )
        embed.set_footer(text="avg_cost đã được tính lại VWAP từ toàn bộ BUY trades")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /portfolio
    # ------------------------------------------------------------------

    @app_commands.command(name="portfolio", description="Xem P&L danh mục hiện tại")
    @app_commands.describe(
        ticker="Để trống để xem tất cả, hoặc nhập mã cổ phiếu cụ thể",
        view="trades = giao dịch thực tế (mặc định) | thesis = góc nhìn thesis + conviction",
    )
    @app_commands.choices(view=[
        app_commands.Choice(name="trades — P&L giao dịch thực tế", value="trades"),
        app_commands.Choice(name="thesis — Conviction + score từ thesis", value="thesis"),
    ])
    async def portfolio(
        self,
        interaction: discord.Interaction,
        ticker: str | None = None,
        view: str = "trades",
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if view == "thesis":
            await self._send_thesis_portfolio(interaction, user_id)
            return

        # view == "trades" (default)
        async with self.db_session() as session:
            svc = PnlService(session=session, quote_service=get_quote_service())
            if ticker:
                await self._send_single_position(interaction, svc, user_id, ticker)
            else:
                await self._send_full_portfolio(interaction, svc, user_id)

    # ------------------------------------------------------------------
    # view=trades helpers
    # ------------------------------------------------------------------

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
    # view=thesis helper
    # ------------------------------------------------------------------

    async def _send_thesis_portfolio(
        self,
        interaction: discord.Interaction,
        user_id: str,
    ) -> None:
        async with self.db_session() as session:
            dash = DashboardService(session)
            theses = await dash.get_theses_list(user_id, status="active", limit=500)
            tickers = list({t["ticker"] for t in theses if t.get("ticker")})

            price_map: dict[str, float] = {}
            if tickers:
                try:
                    quote_svc = get_quote_service()
                    quotes = await quote_svc.get_quotes(tickers)
                    price_map = {q.ticker: q.close for q in quotes if q.close is not None}
                except Exception:
                    pass

            data = await dash.get_portfolio(user_id, price_map=price_map)

        positions = data.get("positions", [])
        if not positions:
            await self.send_info(
                interaction,
                "📊 Thesis Portfolio trống",
                "Bạn chưa có thesis active nào.\nDùng `/thesis new <ticker>` để bắt đầu.",
            )
            return

        lines: list[str] = []
        for pos in positions:
            ticker = pos["ticker"]
            pnl_pct = pos.get("pnl_pct")
            verdict = pos.get("last_verdict") or "—"
            score = pos.get("score")
            tier_icon = pos.get("score_tier_icon") or ""

            if pnl_pct is not None:
                p_icon = "🟢" if pnl_pct >= 0 else "🔴"
                pnl_str = f"{p_icon} {self.fmt_pct(pnl_pct / 100)}"
            else:
                pnl_str = "⚪ N/A"

            score_str = f"{tier_icon} {score}" if score is not None else "—"

            verdict_badge = {
                "BULLISH": "🐂",
                "BEARISH": "🐻",
                "NEUTRAL": "⚖️",
                "WATCHLIST": "👁",
            }.get(verdict, "❓")

            entry = pos.get("entry_price")
            current = pos.get("current_price")
            price_str = (
                f"{self.fmt_vnd(entry)} → {self.fmt_vnd(current)}"
                if entry and current
                else (self.fmt_vnd(entry) if entry else "—")
            )

            lines.append(
                f"{verdict_badge} **{ticker}** {pnl_str} • {price_str} • Score {score_str}"
            )

        body, footer = self.paginate_lines(lines)

        total_pnl_pct = data.get("total_pnl_pct")
        winning = data.get("winning_count", 0)
        losing = data.get("losing_count", 0)
        n = data.get("position_count", 0)

        if total_pnl_pct is not None:
            t_icon = "🟢" if total_pnl_pct >= 0 else "🔴"
            title = f"📊 Thesis Portfolio — {t_icon} {self.fmt_pct(total_pnl_pct / 100)}"
            color = discord.Color.green() if total_pnl_pct >= 0 else discord.Color.red()
        else:
            title = "📊 Thesis Portfolio"
            color = discord.Color.blurple()

        embed = discord.Embed(title=title, description=body, color=color)
        embed.add_field(name="Thesis", value=str(n), inline=True)
        embed.add_field(name="Đang lời", value=str(winning), inline=True)
        embed.add_field(name="Đang lỗ", value=str(losing), inline=True)

        if data.get("total_market_value"):
            embed.add_field(name="Thị giá", value=self.fmt_vnd(data["total_market_value"]), inline=True)
        if data.get("total_cost_basis"):
            embed.add_field(name="Vốn", value=self.fmt_vnd(data["total_cost_basis"]), inline=True)
        if not data.get("has_quantity_data"):
            embed.set_footer(text="⚠️ Một số thesis chưa có quantity — thị giá/vốn có thể không đầy đủ")
        elif footer:
            embed.set_footer(text=footer)

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
                "Chưa có giao dịch nào được ghi nhận.",
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
            # Show trade_id for BUY trades so user can reference /correct_trade
            trade_id_hint = f" `#{t.id}`" if t.trade_type == "buy" else ""
            lines.append(
                f"{icon} `{date_str}`{trade_id_hint} **{t.ticker}** {t.trade_type.upper()} "
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
        footer_parts = list(filter(None, [
            footer_hint,
            f"{summary.total_trades} giao dịch",
            "BUY trade hiển thị #ID — dùng /correct_trade <id> <new_price> để sửa giá",
        ]))
        embed.set_footer(text=" — ".join(footer_parts))
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PortfolioCog(bot))
