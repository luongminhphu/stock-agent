"""Sector Rotation Agent.

Orchestrate: lấy sector flows → build prompt → call AI → parse SectorRotationOutput.
Owner: ai segment.
Callers: bot.commands.sector_rotation, briefing (context injection).
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.schemas import SectorRotationOutput, WatchlistCrosscheck
from src.ai.prompts.sector_rotation import build_sector_rotation_prompt
from src.market.sector_rotation_service import SectorRotationService
from src.market.quote_service import QuoteService

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Bạn là chuyên gia phân tích dòng tiền chứng khoán Việt Nam. "
    "Trả lời bằng JSON hợp lệ theo schema được cung cấp. "
    "Không định dạng markdown, không giải thích ngoài JSON."
)


class SectorRotationAgent:
    """Agent phân tích sector rotation cho thị trường Việt Nam.

    Flow:
        1. SectorRotationService.get_sector_flows() → list[SectorFlow]
        2. build_sector_rotation_prompt() → prompt str
        3. PerplexityClient.chat_completion() → raw JSON
        4. SectorRotationOutput.model_validate() → typed output
        5. Enrich watchlist_crosscheck bằng quote thực tế nếu cần
    """

    def __init__(
        self,
        ai_client: PerplexityClient,
        sector_service: SectorRotationService,
        quote_service: QuoteService,
    ) -> None:
        self._ai = ai_client
        self._sector_svc = sector_service
        self._quotes = quote_service

    async def analyze(
        self,
        watchlist_tickers: list[str] | None = None,
    ) -> SectorRotationOutput:
        """Chạy phân tích sector rotation.

        Args:
            watchlist_tickers: Tickers trong watchlist của user.
                Nếu None, phân tích toàn thị trường không có crosscheck.

        Returns:
            SectorRotationOutput — read-only, không persist.
        """
        sector_flows = await self._sector_svc.get_sector_flows(
            watchlist_tickers=watchlist_tickers
        )
        snapshot_date = await self._sector_svc.get_snapshot_date()

        if not sector_flows:
            logger.warning("sector_rotation_agent: no sector data, returning fallback")
            return _empty_output(snapshot_date)

        prompt = build_sector_rotation_prompt(
            sector_flows=sector_flows,
            snapshot_date=snapshot_date,
            watchlist_tickers=watchlist_tickers,
        )

        logger.info("sector_rotation_agent.start", snapshot_date=snapshot_date)
        try:
            response = await self._ai.chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "SectorRotationOutput",
                        "schema": SectorRotationOutput.model_json_schema(),
                        "strict": True,
                    },
                },
            )
            data = json.loads(self._ai.extract_text(response))
            result = SectorRotationOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("sector_rotation_agent.parse_error", error=str(exc))
            raise ValueError(f"Failed to parse SectorRotationAgent response: {exc}") from exc
        except PerplexityError:
            logger.error("sector_rotation_agent.api_error")
            raise

        logger.info(
            "sector_rotation_agent.complete",
            risk_regime=str(result.risk_regime),
            confidence=result.confidence,
        )

        if watchlist_tickers and not result.watchlist_crosscheck:
            result = await self._enrich_crosscheck(result, watchlist_tickers, sector_flows)

        return result

    async def _enrich_crosscheck(
        self,
        output: SectorRotationOutput,
        watchlist_tickers: list[str],
        sector_flows,
    ) -> SectorRotationOutput:
        """Bổ sung watchlist_crosscheck nếu AI bỏ sót."""
        try:
            raw = await self._quotes.get_bulk_quotes(watchlist_tickers)
        except Exception:
            logger.exception("sector_rotation_agent: enrich_crosscheck failed")
            return output

        quote_map = {q.ticker: q for q in raw}
        sector_avg_map = {sf.sector: sf.avg_change_pct_1d for sf in sector_flows}

        ticker_sector: dict[str, str] = {}
        for sf in sector_flows:
            for t in sf.top_movers:
                ticker_sector[t] = sf.sector

        crosscheck: list[WatchlistCrosscheck] = []
        for ticker in watchlist_tickers:
            ticker_upper = ticker.upper()
            q = quote_map.get(ticker_upper)
            if q is None or q.change_pct is None:
                continue
            sector = ticker_sector.get(ticker_upper)
            if sector is None:
                continue
            sector_avg = sector_avg_map.get(sector, 0.0)
            is_contrarian = (q.change_pct * sector_avg) < 0
            crosscheck.append(
                WatchlistCrosscheck(
                    ticker=ticker_upper,
                    sector=sector,
                    ticker_change_pct=round(q.change_pct, 2),
                    sector_avg_change_pct=round(sector_avg, 2),
                    is_contrarian=is_contrarian,
                    note=(
                        f"{ticker_upper} {q.change_pct:+.2f}% trong khi "
                        f"{sector} avg {sector_avg:+.2f}%"
                    ),
                )
            )

        return output.model_copy(update={"watchlist_crosscheck": crosscheck})


def _empty_output(snapshot_date: str) -> SectorRotationOutput:
    """Fallback khi không có dữ liệu sector."""
    from src.ai.schemas import RiskRegime

    return SectorRotationOutput(
        snapshot_date=snapshot_date,
        rotation_narrative="Không đủ dữ liệu sector để phân tích hôm nay.",
        risk_regime=RiskRegime.MIXED,
        leading_sectors=[],
        lagging_sectors=[],
        watchlist_crosscheck=[],
        actionable_insight="",
        confidence=0.0,
    )
