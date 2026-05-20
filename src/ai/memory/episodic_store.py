"""EpisodicStore — high-level facade cho episodic memory (ai segment).

Ghi nhận sự kiện từ bot/briefing/decision và fill outcome từ scheduler.

Callers:
  - bot reaction handlers          → record_signal()
  - briefing / pretrade agents     → chỉ dùng repository.save() trực tiếp
  - outcome_filler scheduler job   → get_pending_outcome() + fill_outcome()
  - MemoryContextBuilder           → get_recent_by_symbols()

Not a repository: chứa business logic gọi event (idempotency guard,
logging, pct_change calculation). Persistence delegate sang
InteractionLogRepository.

Owner: ai segment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.memory.models import AIInteractionLog
from src.ai.memory.repository import InteractionLogRepository

logger = logging.getLogger(__name__)

UserSignal = Literal["bought", "sold", "ignored", "flagged", "watched"]


class EpisodicStore:
    """Facade: ghi episodic event + fill outcome.

    Khởi tạo với một InteractionLogRepository được inject (dễ test).
    Mỗi method nhận AsyncSession để có thể chạy trong transaction của caller.
    """

    def __init__(self, repo: InteractionLogRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Bot reaction handler
    # ------------------------------------------------------------------

    async def record_signal(
        self,
        session: AsyncSession,
        *,
        interaction_log_id: int,
        signal: UserSignal,
    ) -> None:
        """Ghi user_signal vào log đã tồn tại.

        Idempotent: ghi đè nếu signal được cập nhật (e.g. ignored → bought).
        Caller: bot reaction handler sau mỗi briefing/analysis message.
        """
        existing = await self._repo.get_by_id(interaction_log_id)
        if existing is None:
            logger.warning(
                "episodic_store.record_signal: id=%s not found, skipping",
                interaction_log_id,
            )
            return
        await self._repo.set_user_signal(interaction_log_id, signal)
        logger.info(
            "episodic_store.signal_recorded id=%s signal=%s (prev=%s)",
            interaction_log_id,
            signal,
            existing.user_signal,
        )

    # ------------------------------------------------------------------
    # Outcome filler (scheduler job)
    # ------------------------------------------------------------------

    async def fill_outcome(
        self,
        session: AsyncSession,
        *,
        interaction_log_id: int,
        price_at_signal: float,
        price_now: float,
        thesis_status: str | None = None,
    ) -> bool:
        """Fill outcome_json sau N ngày — gọi từ scheduler.

        Không override nếu outcome đã có (idempotency guard).
        Returns True nếu đã fill, False nếu skip (already filled / not found).
        """
        existing = await self._repo.get_by_id(interaction_log_id)
        if existing is None:
            logger.warning(
                "episodic_store.fill_outcome: id=%s not found", interaction_log_id
            )
            return False
        if existing.outcome_json is not None:
            return False  # đã fill rồi, không ghi đè

        pct_change: float | None = None
        if price_at_signal and price_at_signal != 0:
            pct_change = round(
                (price_now - price_at_signal) / price_at_signal * 100, 2
            )

        outcome = {
            "price_at_signal": price_at_signal,
            "price_now": price_now,
            "pct_change": pct_change,
            "thesis_status": thesis_status,
            "filled_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._repo.set_outcome(interaction_log_id, outcome)
        logger.info(
            "episodic_store.outcome_filled id=%s pct_change=%s thesis_status=%s",
            interaction_log_id,
            pct_change,
            thesis_status,
        )
        return True

    # ------------------------------------------------------------------
    # Query helpers (MemoryContextBuilder, bot commands)
    # ------------------------------------------------------------------

    async def get_pending_outcome(
        self,
        session: AsyncSession,
        *,
        older_than_days: int = 7,
        limit: int = 50,
    ) -> list[AIInteractionLog]:
        """Logs có user_signal nhưng chưa có outcome — scheduler dùng để fill.

        Chỉ trả logs đủ cũ (≥ older_than_days) để price đã settle.
        """
        return await self._repo.get_pending_outcome(
            older_than_days=older_than_days, limit=limit
        )

    async def get_recent_by_symbols(
        self,
        session: AsyncSession,
        *,
        symbols: list[str],
        limit: int = 20,
    ) -> list[AIInteractionLog]:
        """Episodes gần nhất liên quan tới symbols — cho MemoryContextBuilder."""
        return await self._repo.get_by_symbols(symbols=symbols, limit=limit)
