"""add direction to theses

Revision ID: 20260506_0016
Revises: 20260505_0015
Create Date: 2026-05-06

Adds nullable VARCHAR column `direction` to the theses table.
Corresponds to the ThesisDirection enum (LONG / SHORT / NEUTRAL)
added to the Thesis ORM model.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260506_0016"
down_revision: str = "20260505_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "theses",
        sa.Column("direction", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("theses", "direction")
