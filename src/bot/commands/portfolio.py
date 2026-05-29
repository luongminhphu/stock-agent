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

Buy/sell orchestration (DecisionLog, auto-rationale) lives in:
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
        if thesis_id:
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
        embed.add_field(name="Tổng đang giữ", value=f"{position.qty:,.0f} cổ", inline=True)
        embed.add_field(
            name="Chi phí vốn mới",
            value=self.fmt_vnd(position.avg_cost * position.qty),
            inline=True,
        )
        embed.set_footer(text="avg_cost đã được tính lại VWAP từ toàn bộ BUY trades")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /dividend
    # ------------------------------------------------------------------

    @app_commands.command(name="dividend", description="Ghi nhận cổ tức nhận được")
    @app_commands.describe(
        ticker="Mã cổ phiếu (VD: VCB)",
        qty="Số cổ phiếu được hưởng cổ tức",
        dividend_per_share="Cổ tức mỗi cổ phiếu — tiền mặt: VND/cổ | cổ phiếu: tỷ lệ (VD: 0.10 = 10%)",
        dividend_type="Loại cổ tức: cash (tiền mặt) hoặc cổ phiếu (stock)",
        ex_date="Ngày chốt quyền (tuỳ chọn, định dạng YYYY-MM-DD)",
        note="Ghi chú tùy chọn",
    )
    @app_commands.choices(dividend_type=[
        app_commands.Choice(name="cash — Tiền mặt (VND/cổ)", value="cash"),
        app_commands.Choice(name="stock — Cổ phiếu (tỷ lệ, VD: 0.10 = 10%)", value="stock"),
    ])
    async def dividend(
        self,
        interaction: discord.Interaction,
        ticker: str,
        qty: float,
        dividend_per_share: float,
        dividend_type: str = "cash",
        ex_date: str | None = None,
        note: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        if qty <= 0 or dividend_per_share <= 0:
            await self.send_error(
                interaction,
                "Giá trị không hợp lệ",
                "qty và dividend_per_share phải > 0",
            )
            return

        parsed_ex_date = None
        if ex_date:
            try:
                from datetime import date, timezone
                d = date.fromisoformat(ex_date)
                from datetime import datetime as _dt
                parsed_ex_date = _dt(d.year, d.month, d.day, tzinfo=timezone.utc)
            except ValueError:
                await self.send_error(
                    interaction,
                    "Ngày không hợp lệ",
                    f"ex_date phải có định dạng YYYY-MM-DD, nhận được: `{ex_date}`",
                )
                return

        div_type = DividendType.CASH if dividend_type == "cash" else DividendType.STOCK

        async with self.db_session() as session:
            svc = PortfolioService(session)
            record = await svc.record_dividend(
                user_id=user_id,
                ticker=ticker,
                qty=qty,
                dividend_per_share=dividend_per_share,
                dividend_type=div_type,
                ex_date=parsed_ex_date,
                note=note,
            )

        ticker_upper = ticker.upper()
        is_cash = div_type == DividendType.CASH

        embed = discord.Embed(
            title=f"💰 Cổ tức {ticker_upper} — {'Tiền mặt' if is_cash else 'Cổ phiếu'}",
            color=0x6DAA45,
        )
        embed.add_field(name="Số cổ hưởng", value=f"{record.qty:,.0f}", inline=True)
        if is_cash:
            embed.add_field(
                name="Cổ tức/cổ",
                value=self.fmt_vnd(record.dividend_per_share),
                inline=True,
            )
            embed.add_field(
                name="Tổng nhận",
                value=self.fmt_vnd(record.total_amount),
                inline=True,
            )
        else:
            embed.add_field(
                name="Tỷ lệ",
                value=f"{record.dividend_per_share:.1%}",
                inline=True,
            )
            embed.add_field(
                name="Cổ phiếu thưởng (~)",
                value=f"{record.total_amount:,.0f} cổ",
                inline=True,
            )
        if parsed_ex_date:
            embed.add_field(
                name="Ngày chốt quyền",
                value=parsed_ex_date.strftime("%d/%m/%Y"),
                inline=True,
            )
        if record.position_id:
            embed.add_field(name="Position ID", value=f"#{record.position_id}", inline=True)
        if note:
            embed.add_field(name="Ghi chú", value=note, inline=False)
        embed.set_footer(
            text=f"Record ID: #{record.id} — dùng /dividend_history {ticker_upper} để xem lịch sử"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /dividend_history
    # ------------------------------------------------------------------

    @app_commands.command(
        name="dividend_history",
        description="Xem lịch sử cổ tức đã nhận",
    )
    @app_commands.describe(ticker="Để trống để xem tất cả, hoặc nhập mã cổ phiếu cụ thể")
    async def dividend_history(
        self,
        interaction: discord.Interaction,
        ticker: str | None = None,
    ) -> None:
        await self.defer(interaction)
        user_id = self.user_id(interaction)

        async with self.db_session() as session:
            svc = PnlService(session=session, quote_service=get_quote_service())
            summary = await svc.get_dividend_summary(user_id, ticker=ticker, limit=20)

        if not summary.records:
            label = f" **{ticker.upper()}**" if ticker else ""
            await self.send_info(
                interaction,
                "💰 Chưa có cổ tức",
                f"Chưa ghi nhận cổ tức nào{label}.\nDùng `/dividend <ticker> <qty> <dps>` để thêm.",
            )
            return

        lines: list[str] = []
        for r in summary.records:
            date_str = r.paid_at.strftime("%d/%m/%Y") if r.paid_at else "?"
            ex_str = f" | ex {r.ex_date.strftime('%d/%m/%Y')}" if r.ex_date else ""
            if r.dividend_type == "cash":
                amount_str = self.fmt_vnd(r.total_amount)
                dps_str = f"{self.fmt_vnd(r.dividend_per_share)}/cổ"
            else:
                amount_str = f"{r.total_amount:,.0f} cổ"
                dps_str = f"{r.dividend_per_share:.1%}"
            lines.append(
                f"💰 `{date_str}`{ex_str} **{r.ticker}** {r.qty:,.0f} cổ × {dps_str} = **{amount_str}**"
            )

        body, footer_hint = self.paginate_lines(lines)
        title_ticker = f" {ticker.upper()}" if ticker else ""
        embed = discord.Embed(
            title=f"💰 Lịch sử cổ tức{title_ticker}",
            description=body,
            color=0x6DAA45,
        )
        embed.add_field(
            name="Tổng tiền mặt nhận",
            value=self.fmt_vnd(summary.total_cash_received),
            inline=True,
        )
        embed.add_field(name="Số lần", value=str(summary.record_count), inline=True)
        footer_parts = list(filter(None, [footer_hint, f"{summary.record_count} bản ghi"]))
        embed.set_footer(text=" — ".join(footer_parts))
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
