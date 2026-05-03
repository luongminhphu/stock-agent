"""add quantity to theses

Revision ID: 20260504_0006
Revises: 20260503_0005
Create Date: 2026-05-04

Adds nullable FLOAT column `quantity` to the theses table.
This column was added to the ORM model (Thesis) but the corresponding
migration was missing, causing UndefinedColumnError on dashboard/theses endpoint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_0006"
down_revision: str = "20260503_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "theses",
        sa.Column("quantity", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("theses", "quantity")
