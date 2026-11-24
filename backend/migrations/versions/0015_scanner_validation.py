"""Aynpulse Scanner: the agent validates what was read from a screenshot.

Revision ID: 0015_scanner_validation
Revises: 0014_scanner_extraction
Create Date: 2026-11-24

`validated_at` is set when the agent confirms, on the review screen, that the entries match the
image (after any corrections). Reading the screenshot again clears it.
"""

from alembic import op

revision = "0015_scanner_validation"
down_revision = "0014_scanner_extraction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE scanner_extractions ADD COLUMN validated_at timestamptz;")


def downgrade() -> None:
    op.execute("ALTER TABLE scanner_extractions DROP COLUMN IF EXISTS validated_at;")
