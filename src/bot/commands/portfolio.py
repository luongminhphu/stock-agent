"""Portfolio commands — bot adapter for portfolio segment.

Owner: bot segment (adapter only).
No orchestration logic — delegates buy/sell to TradeUseCase.
Other domain logic lives in PnlService and DashboardService.

Commands:
  /buy             <ticker> <qty> <price> [thesis_id] [rationale]  — open/add to position
  /sell            <ticker> <qty> <price> [thesis_id] [rationale]  — reduce/close position
  /correct_trade   <trade_id> <new_price>                          — fix buy price + recalculate avg_cost
  /dividend        <ticker> <qty> <dividend_per_share>             — record dividend received
  /portfolio       [ticker] [view]                                 — full portfolio or thesis-view
  /history         [ticker]                                        — realized trade history (shows trade_id)
  /link_thesis     <ticker> <thesis_id>                            — backfill thesis linkage on open position

Buy/sell orchestration (DecisionLog, auto-rationale, auto-wire thesis) lives in:
    src/portfolio/trade_usecase.py  ←  single source of truth
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service
from src.portfolio.models import DividendType
from src.portfolio.service import (
    InsufficientQtyError,
    InvalidOperationError,
    PortfolioService,
    PositionNotFoundError,
    TradeNotFoundError,
)
from src.portfolio.pnl_service import PnlService
from src.portfolio.trade_usecase import TradeUseCase
from src.readmodel.dashboard_service import DashboardService


def _is_buy(trade_type: object) -> bool:
    """Safe comparison: handles both TradeType enum and raw asyncpg string."""
    return str(trade_type).lower() == "buy"


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
        thesis_id="ID thesis liên kết (tuỳ chọn) — tự động tạo DecisionLog",
        rationale="Lý do mua (tuỳ chọn) — để trống sẽ dùng rationale mặc định nếu có thesis_id",
        note="Ghi chú tùy chọn",
    )
    async def buy(
        self,
        interaction: discord.Interaction,
        ticker: str,
        qty: float,
        price: float,
        thesis_id: int | None = None,
        rationale: str | None = None,
        note: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if qty <= 0 or price <= 0:
            await self.send_error(interaction, "Giá trị không hợp lệ", "qty và price phải > 0")
            return

        try:
            async with self.db_session() as session:
                uc = TradeUseCase(session=session, quote_service=get_quote_service())
                result = await uc.execute_buy(
                    user_id=user_id,
                    ticker=ticker,
                    qty=qty,
                    price=price,
                    thesis_id=thesis_id,
                    rationale=rationale,
                    note=note,
                    source="discord",
                )
        except ValueError as exc:
            await self.send_error(interaction, "Giá trị không hợp lệ", str(exc))
            return
        except Exception as exc:
            await self.send_error(interaction, "Lỗi hệ thống", str(exc))
            return

        embed = discord.Embed(
            title=f"✅ Mua {result.ticker}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Số cổ", value=f"{result.qty:,.0f}", inline=True)
        embed.add_field(name="Giá mua", value=self.fmt_vnd(result.price), inline=True)
        embed.add_field(name="Giá vốn TB mới", value=self.fmt_vnd(result.avg_cost), inline=True)
        embed.add_field(name="Tổng đang giữ", value=f"{result.position_qty:,.0f} cổ", inline=True)
        embed.add_field(
            name="Chi phí vốn",
            value=self.fmt_vnd(result.avg_cost * result.position_qty),
            inline=True,
        )
        # Show thesis linkage — explicit or auto-wired
        if result.thesis_auto_wired:
            embed.add_field(name="Thesis", value="🔗 Auto-linked từ thesis active", inline=True)
        elif thesis_id:
            decision_hint = "✅ DecisionLog đã ghi" if result.decision_logged else "⚠️ DecisionLog thất bại"
            embed.add_field(name="Decision Log", value=decision_hint, inline=True)
        if note:
            embed.add_field(name="Ghi chú", value=note, inline=False)
        embed.set_footer(
            text=f"Trade ID: #{result.trade_id} — dùng /correct_trade {result.trade_id} <new_price> nếu nhập sai giá"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /sell
    # ------------------------------------------------------------------

    @app_commands.command(name="sell", description="Ghi nhận lệnh bán khỏi portfolio")
    @app_commands.describe(
        ticker="Mã cổ phiếu (VD: VCB)",
        qty="Số cổ phiếu bán",
        price="Giá bán (VND)",
        thesis_id="ID thesis liên kết (tuỳ chọn) — tự động tạo DecisionLog",
        rationale="Lý do bán (tuỳ chọn) — để trống sẽ dùng rationale mặc định nếu có thesis_id",
        note="Ghi chú tùy chọn",
    )
    async def sell(
        self,
        interaction: discord.Interaction,
        ticker: str,
        qty: float,
        price: float,
        thesis_id: int | None = None,
        rationale: str | None = None,
        note: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if qty <= 0 or price <= 0:
            await self.send_error(interaction, "Giá trị không hợp lệ", "qty và price phải > 0")
            return

        try:
            async with self.db_session() as session:
                uc = TradeUseCase(session=session, quote_service=get_quote_service())
                result = await uc.execute_sell(
                    user_id=user_id,
                    ticker=ticker,
                    qty=qty,
                    price=price,
                    thesis_id=thesis_id,
                    rationale=rationale,
                    note=note,
                    source="discord",
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
        except Exception as exc:
            await self.send_error(interaction, "Lỗi hệ thống", str(exc))
            return

        pnl = result.realized_pnl or 0.0
        pnl_icon = "🟢" if pnl >= 0 else "🔴"

        embed = discord.Embed(
            title=f"{pnl_icon} Bán {result.ticker}{' — Vị thế đóng' if result.position_closed else ''}",
            color=discord.Color.green() if pnl >= 0 else discord.Color.red(),
        )
        embed.add_field(name="Số cổ bán", value=f"{result.qty:,.0f}", inline=True)
        embed.add_field(name="Giá bán", value=self.fmt_vnd(result.price), inline=True)
        embed.add_field(name="Giá vốn TB", value=self.fmt_vnd(result.avg_cost), inline=True)
        embed.add_field(
            name="Lời/Lỗ thực hiện",
            value=f"{pnl_icon} {self.fmt_vnd(abs(pnl))} ({'lời' if pnl >= 0 else 'lỗ'})",
            inline=True,
        )
        if not result.position_closed:
            embed.add_field(
                name="Còn giữ", value=f"{result.position_qty:,.0f} cổ", inline=True
            )
        if thesis_id:
            decision_hint = "✅ DecisionLog đã ghi" if result.decision_logged else "⚠️ DecisionLog thất bại"
            embed.add_field(name="Decision Log", value=decision_hint, inline=True)
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
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /link_thesis
    # ------------------------------------------------------------------

    @app_commands.command(
        name="link_thesis",
        description="Gắn thesis vào position đang mở — dùng để backfill khi mua chưa truyền thesis_id",
    )
    @app_commands.describe(
        ticker="Mã cổ phiếu (VD: DGC)",
        thesis_id="ID thesis cần gắn vào position này",
    )
    async def link_thesis(
        self,
        interaction: discord.Interaction,
        ticker: str,
        thesis_id: int,
    ) -> None:
        """Backfill thesis_id on an existing open position.

        Use case: position was created without thesis_id (e.g. imported data,
        or /buy without supplying the ID). This command wires the thesis
        retroactively so the dashboard Trades tab can display thesis info.

        Validation:
          - Position must be open (closed_at is None).
          - Thesis must exist, belong to the same user_id, and match the ticker.
          - thesis_id must not already be set to a different thesis
            (guards against accidental overwrite — user must confirm intent).
        """
        await self.defer(interaction)
        user_id = self.user_id(interaction)
        ticker_clean = ticker.upper().strip()

        try:
            async with self.db_session() as session:
                from src.portfolio.repository import PortfolioRepository  # noqa: PLC0415
                from src.thesis.repository import ThesisRepository  # noqa: PLC0415

                port_repo = PortfolioRepository(session)
                thesis_repo = ThesisRepository(session)

                # Validate open position exists
                position = await port_repo.get_open_position(user_id, ticker_clean)
                if position is None:
                    await self.send_error(
                        interaction,
                        "Không tìm thấy vị thế",
                        f"Bạn chưa có vị thế mở nào với **{ticker_clean}**.",
                    )
                    return

                # Guard: already linked to a different thesis
                if position.thesis_id is not None and position.thesis_id != thesis_id:
                    await self.send_error(
                        interaction,
                        "Vị thế đã được gắn thesis khác",
                        f"**{ticker_clean}** đang link thesis `#{position.thesis_id}`.\n"
                        f"Nếu muốn ghi đè, dùng lại lệnh với `thesis_id={thesis_id}` sau khi xác nhận.",
                    )
                    return

                # Validate thesis ownership + ticker match
                from src.thesis.models import Thesis  # noqa: PLC0415
                thesis = await session.get(Thesis, thesis_id)
                if thesis is None:
                    await self.send_error(
                        interaction,
                        "Thesis không tồn tại",
                        f"Không tìm thấy thesis `#{thesis_id}`.",
                    )
                    return
                if thesis.user_id != user_id:
                    await self.send_error(
                        interaction,
                        "Không có quyền",
                        f"Thesis `#{thesis_id}` không thuộc về bạn.",
                    )
                    return
                if thesis.ticker.upper() != ticker_clean:
                    await self.send_error(
                        interaction,
                        "Ticker không khớp",
                        f"Thesis `#{thesis_id}` là cho **{thesis.ticker}**, không phải **{ticker_clean}**.",
                    )
                    return

                # Patch + commit
                position.thesis_id = thesis_id
                await port_repo.save_position(position)
                await session.commit()

        except Exception as exc:
            await self.send_error(interaction, "Lỗi hệ thống", str(exc))
            return

        embed = discord.Embed(
            title=f"🔗 Đã gắn thesis vào {ticker_clean}",
            color=0x4F98A3,
        )
        embed.add_field(name="Ticker", value=ticker_clean, inline=True)
        embed.add_field(name="Thesis ID", value=f"#{thesis_id}", inline=True)
        embed.add_field(name="Thesis", value=thesis.title or f"#{thesis_id}", inline=True)
        embed.set_footer(text="Dashboard Trades tab sẽ hiển thị thesis info sau khi reload.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /dividend
    # ------------------------------------------------------------------

    @app_commands.command(name="dividend", description="Ghi nhận cổ tức nhận được")
    @app_commands.describe(
        ticker="Mã cổ phiếu",
        qty="Số cổ phiếu đang giữ lúc nhận cổ tức",
        dividend_per_share="Cổ tức trên mỗi cổ phiếu (VND)",
        note="Ghi chú tùy chọn",
    )
    async def dividend(
        self,
        interaction: discord.Interaction,
        ticker: str,
        qty: float,
        dividend_per_share: float,
        note: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if qty <= 0 or dividend_per_share <= 0:
            await self.send_error(interaction, "Giá trị không hợp lệ", "qty và dividend_per_share phải > 0")
            return

        try:
            async with self.db_session() as session:
                svc = PortfolioService(session)
                record = await svc.record_dividend(
                    user_id=user_id,
                    ticker=ticker,
                    qty=qty,
                    dividend_per_share=dividend_per_share,
                    dividend_type=DividendType.CASH,
                    note=note,
                )
        except Exception as exc:
            await self.send_error(interaction, "Lỗi hệ thống", str(exc))
            return

        total = record.qty * record.dividend_per_share
        embed = discord.Embed(title=f"💰 Cổ tức {record.ticker}", color=0x4F98A3)
        embed.add_field(name="Số cổ", value=f"{record.qty:,.0f}", inline=True)
        embed.add_field(name="Cổ tức/cp", value=self.fmt_vnd(record.dividend_per_share), inline=True)
        embed.add_field(name="Tổng nhận", value=self.fmt_vnd(total), inline=True)
        if note:
            embed.add_field(name="Ghi chú", value=note, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /portfolio
    # ------------------------------------------------------------------

    @app_commands.command(name="portfolio", description="Xem danh mục đầu tư")
    @app_commands.describe(
        view="trades (mặc định) hoặc thesis — chọn góc nhìn",
    )
    async def portfolio(
        self,
        interaction: discord.Interaction,
        view: str = "trades",
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if view.lower() == "thesis":
            await self._portfolio_thesis_view(interaction, user_id)
            return

        # Default: trades view
        async with self.db_session() as session:
            svc = PnlService(session=session, quote_service=get_quote_service())
            pnl = await svc.get_portfolio_pnl(user_id)

        if not pnl.positions:
            await self.send_info(
                interaction,
                "📊 Portfolio trống",
                "Bạn chưa có vị thế mở nào.\nDùng `/buy <ticker> <qty> <price>` để bắt đầu.",
            )
            return

        lines: list[str] = []
        for p in sorted(pnl.positions, key=lambda x: -(x.market_value or 0)):
            pnl_icon = "🟢" if (p.unrealized_pct or 0) >= 0 else "🔴"
            pnl_str = f"{pnl_icon} {self.fmt_pct(p.unrealized_pct / 100)}" if p.unrealized_pct is not None else "⚪"
            thesis_str = f" | thesis #{p.thesis_id}" if p.thesis_id else ""
            lines.append(
                f"**{p.ticker}** {p.qty:,.0f}cp @ {self.fmt_vnd(p.avg_cost)} → {self.fmt_vnd(p.current_price or 0)} {pnl_str}{thesis_str}"
            )

        body, footer = self.paginate_lines(lines)
        total_icon = "🟢" if (pnl.total_unrealized_pct or 0) >= 0 else "🔴"
        title = (
            f"📊 Portfolio — {total_icon} {self.fmt_pct((pnl.total_unrealized_pct or 0) / 100)}"
            if pnl.total_unrealized_pct is not None
            else "📊 Portfolio"
        )
        embed = discord.Embed(title=title, description=body, color=0x4F98A3)
        if pnl.total_market_value:
            embed.add_field(name="Thị giá", value=self.fmt_vnd(pnl.total_market_value), inline=True)
        if pnl.total_cost_basis:
            embed.add_field(name="Vốn", value=self.fmt_vnd(pnl.total_cost_basis), inline=True)
        if pnl.total_unrealized_pnl is not None:
            pnl_icon = "🟢" if pnl.total_unrealized_pnl >= 0 else "🔴"
            embed.add_field(
                name="Lời/Lỗ",
                value=f"{pnl_icon} {self.fmt_vnd(abs(pnl.total_unrealized_pnl))}",
                inline=True,
            )
        if footer:
            embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _portfolio_thesis_view(
        self,
        interaction: discord.Interaction,
        user_id: str,
    ) -> None:
        async with self.db_session() as session:
            dash = DashboardService(session)
            data = await dash.get_portfolio(user_id, quote_service=get_quote_service())

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
            is_buy = _is_buy(t.trade_type)
            if is_buy:
                icon = "🟢"
            else:
                icon = "🟢" if (t.realized_pnl or 0) >= 0 else "🔴"
            pnl_str = (
                f" | P&L {self.fmt_vnd(t.realized_pnl)}"
                if t.realized_pnl is not None
                else ""
            )
            date_str = t.traded_at.strftime("%d/%m %H:%M") if t.traded_at else "?"
            trade_id_hint = f" `#{t.id}`" if is_buy else ""
            lines.append(
                f"{icon} `{date_str}`{trade_id_hint} **{t.ticker}** {str(t.trade_type).upper()} "
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
