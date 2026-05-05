"""create investor_profiles table

Revision ID: 20260505_0014
Revises: 20260504_0013
Create Date: 2026-05-05

Wave 1 — Blueprint V2: InvestorProfile persistent self-knowledge layer.

New table investor_profiles stores daily snapshots of the investor’s
behavioral patterns, decision performance metrics, and portfolio bias.
Built each morning by InvestorProfileService.build_snapshot() before
the morning brief. Consumed read-only by ai.ContextBuilder (Wave 2).

All JSON/text columns default to '' (empty string) — never NULL —
so _parse_json_list() can safely json.loads without None checks.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260505_0014"
down_revision = "20260504_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investor_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "snapshot_date",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        # Behavioral insights — JSON-encoded list[str]
        sa.Column("behavioral_patterns", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confirmed_biases",    sa.Text(), nullable=False, server_default="[]"),
        sa.Column("top_lessons",         sa.Text(), nullable=False, server_default="[]"),
        # Portfolio state snapshot
        sa.Column("portfolio_bias",       sa.String(512), nullable=False, server_default=""),
        sa.Column("active_thesis_count",  sa.Integer(),   nullable=False, server_default="0"),
        # Decision performance
        sa.Column("win_rate_30d",  sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_hold_days", sa.Float(), nullable=False, server_default="0"),
        # Pre-rendered AI prompt block
        sa.Column("summary_for_ai", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_investor_profiles_snapshot_date",
        "investor_profiles",
        ["snapshot_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_investor_profiles_snapshot_date", table_name="investor_profiles")
    op.drop_table("investor_profiles")
