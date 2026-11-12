"""Initial schema baseline derived from the current SQLAlchemy metadata.

For an existing installation, this revision uses create_all(checkfirst=True):
it creates newly introduced tables but never drops or replaces existing tables.
Revision ID: 0001_baseline
"""
from alembic import op

from bot.db import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Baseline downgrade is intentionally non-destructive for user data.
    pass
