"""ORM models cho Wave D.1 — persisted in-memory stores.

Owner: readmodel segment.

Tables:
    daily_agendas           — AgendaCache (briefing)

Wave E1a: ``market_quote_cache``, ``trend_snapshots``, ``trend_predictions`` chuyển
sang ``src/market/models.py`` (owner market). Wave E1b: ``intelligence_snapshots``, ``global_risk_snapshots`` → ``src/core/models.py``.
Re-export bên dưới giữ tương thích 1 wave.

Tất cả các bảng đều dùng upsert pattern (ON CONFLICT DO UPDATE)
để keep it simple — mỗi symbol/user chỉ có 1 row hiện tại.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Date,
    DateTime,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import Base


class DailyAgenda(Base):
    """Persisted CachedAgenda per user per date — AgendaCache.

    Scoped by date so we never restore yesterday's agenda.
    On load: only restore if date == today (Asia/Bangkok).

    PK: (user_id, date) — one agenda per user per day.
    """

    __tablename__ = "daily_agendas"
    __table_args__ = (
        UniqueConstraint("user_id", "agenda_date", name="uq_daily_agendas_user_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agenda_date: Mapped[datetime] = mapped_column(
        Date,
        nullable=False,
        comment="Date (UTC) this agenda was built for",
    )
    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Compact multi-line agenda string for Discord embed prefix",
    )
    buckets_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON: {decide: [...], watch: [...], defer: [...]}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


# ── Wave E1 compat re-export (xoá ở wave sau) ────────────────────────────────
from src.core.models import GlobalRiskSnapshot, IntelligenceSnapshot  # noqa: E402
from src.market.models import (  # noqa: E402
    MarketQuoteCache,
    TrendPrediction,
    TrendSnapshot,
)

__all__ = [
    "DailyAgenda",
    "GlobalRiskSnapshot",
    "IntelligenceSnapshot",
    "MarketQuoteCache",
    "TrendPrediction",
    "TrendSnapshot",
]
