"""add sector column to positions table

Revision ID: 20260505_0015
Revises: 20260505_0014
Create Date: 2026-05-05

Adds nullable sector VARCHAR(64) to the positions table.

Motivation:
    ContextBuilder._fetch_portfolio_bias() reads pos.sector to compute
    real sector-weight strings (e.g. "t\u00e0i ch\u00ednh 65%, ng. v. li\u1ec7u 25%").
    Without this column every position fell back to 'Unknown', making
    the portfolio_bias block useless for AI context.

Backward compatibility:
    Column is nullable with no server_default. All existing positions
    retain sector=NULL. ContextBuilder skips NULL-sector positions when
    computing weights, so old rows do not corrupt output.
    New /buy calls may pass sector= to populate it.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260505_0015"
down_revision = "20260505_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("sector", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("positions", "sector")
