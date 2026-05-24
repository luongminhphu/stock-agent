"""rename ThesisDirection LONG/SHORT → BULLISH/BEARISH

Revision ID: 20260524_0028
Revises: 20260521_0027
Create Date: 2026-05-24

Migration strategy (PostgreSQL):
  1. ALTER TYPE ... RENAME VALUE — renames enum labels in-place, zero downtime.
  2. UPDATE theses — cleans up any dirty rows written before the enum existed
     (e.g. direction stored as 'bullish'/'bearish' raw strings).

No downgrade provided: renaming back would require a coordinated deploy.
If rollback is needed, restore from backup and redeploy previous app version.
"""

from alembic import op

# revision identifiers
revision = "20260524_0028"
down_revision = "20260521_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: rename enum values LONG → BULLISH, SHORT → BEARISH
    # PostgreSQL 10+ supports ALTER TYPE ... RENAME VALUE
    op.execute("ALTER TYPE thesisdirection RENAME VALUE 'LONG' TO 'BULLISH'")
    op.execute("ALTER TYPE thesisdirection RENAME VALUE 'SHORT' TO 'BEARISH'")

    # Step 2: clean up any dirty rows that stored raw alias strings
    # (written before the enum constraint was enforced)
    op.execute("""
        UPDATE theses
        SET direction = CASE
            WHEN lower(direction::text) IN ('long',  'bullish') THEN 'BULLISH'
            WHEN lower(direction::text) IN ('short', 'bearish') THEN 'BEARISH'
            WHEN lower(direction::text) = 'neutral'             THEN 'NEUTRAL'
            ELSE direction::text
        END::thesisdirection
        WHERE direction::text NOT IN ('BULLISH', 'BEARISH', 'NEUTRAL')
    """)


def downgrade() -> None:
    # Intentionally not implemented.
    # To roll back: restore DB from backup + redeploy previous app version.
    raise NotImplementedError(
        "Downgrade not supported. Restore from backup if rollback is needed."
    )
