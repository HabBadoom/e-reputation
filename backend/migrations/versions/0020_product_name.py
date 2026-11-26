"""Final product name: AynPulse (decision of 2026-11-26).

Revision ID: 0020_product_name
Revises: 0019_social_sources
Create Date: 2026-11-26

Only the display name of the Scanner connector policy changes; keys and identifiers
(`aynpulse_scanner`, `anypulse_access`, …) stay as they are.
"""

from alembic import op

revision = "0020_product_name"
down_revision = "0019_social_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE connector_policies SET name = 'AynPulse Scanner (agent screenshots)' "
               "WHERE key = 'aynpulse_scanner'")


def downgrade() -> None:
    op.execute("UPDATE connector_policies SET name = 'Aynpulse Scanner (agent screenshots)' "
               "WHERE key = 'aynpulse_scanner'")
