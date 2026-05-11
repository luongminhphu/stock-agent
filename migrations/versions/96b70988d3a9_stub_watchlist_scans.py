"""Stub: watchlist_scans / brief_snapshots branch (missing revision).

This revision was referenced by 0003_merge_heads but the original file was
never committed. This no-op stub restores the chain so Alembic can build its
revision map without raising KeyError: '96b70988d3a9'.

Revision ID: 96b70988d3a9
Revises: 0001_initial_schema
Create Date: 2026-04-22
"""
from __future__ import annotations

revision: str = "96b70988d3a9"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
