"""Add market_quote_cache table — persist QuoteService._last_known across restarts.

Owner: market segment (written by QuoteService), readmodel segment (ORM model).

Purpose:
    QuoteService._last_known is in-memory only. After a process restart outside
    trading hours, _last_known is empty → dashboard shows N/A for all tickers.
    This table persists the last-known quote per ticker so QuoteService can
    warm-load on startup.

Strategy:
    - 1 row per ticker (primary key)
    - Upsert on every successful adapter fetch (fire-and-forget)
    - warm_load() called in _warm_up_persisted_stores() at bootstrap

Revision ID: 20260702_0042
Revises: 20260619_0041
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260702_0042"
down_revision = "20260619_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_quote_cache",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("change", sa.Float, nullable=False, server_default="0"),
        sa.Column("change_pct", sa.Float, nullable=False, server_default="0"),
        sa.Column("volume", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("value", sa.Float, nullable=False, server_default="0"),
        sa.Column("open", sa.Float, nullable=False, server_default="0"),
        sa.Column("high", sa.Float, nullable=False, server_default="0"),
        sa.Column("low", sa.Float, nullable=False, server_default="0"),
        sa.Column("ref_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("ceiling", sa.Float, nullable=False, server_default="0"),
        sa.Column("floor", sa.Float, nullable=False, server_default="0"),
        sa.Column(
            "quote_ts",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp of the original quote from adapter",
        ),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Last time this row was upserted",
        ),
    )


def downgrade() -> None:
    op.drop_table("market_quote_cache")
