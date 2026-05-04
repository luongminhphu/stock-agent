"""Add auto_reactivate column to alerts table.

Revision ID: 20260504_0008
Revises: 20260504_0007
Create Date: 2026-05-04

Problem
-------
The Alert ORM model (src/watchlist/models.py) defines an ``auto_reactivate``
Boolean column but the initial schema migration (0001) never included it.
This caused::

    asyncpg.exceptions.UndefinedColumnError: column alerts.auto_reactivate
    does not exist

on any query that SELECTs from the alerts table — including the morning
brief watchlist context fetch — making the brief generation fail entirely.

Fix
---
Add the missing column with ``server_default='false'`` so existing rows
receive a safe, backwards-compatible default without a table rewrite.
The NOT NULL constraint is preserved.

Behaviour after migration
-------------------------
- Existing alerts: auto_reactivate = False (one-shot, existing behaviour)
- New alerts: default False unless explicitly set to True at creation time
- AlertService.create() already accepts ``auto_reactivate`` param — no
  service-layer changes required.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_0008"
down_revision: str = "20260504_0007"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column(
            "auto_reactivate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("alerts", "auto_reactivate")
