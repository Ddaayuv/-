"""Add columns missing from legacy installations without replacing data.

Revision ID: 0002_legacy_columns
Revises: 0001_baseline
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_legacy_columns"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return None
    return {column["name"] for column in inspector.get_columns(table)}


def _add_if_missing(table: str, name: str, column: sa.Column) -> None:
    columns = _columns(table)
    if columns is not None and name not in columns:
        op.add_column(table, column)


def upgrade() -> None:
    _add_if_missing("users", "is_active", sa.Column(
        "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
    ))
    _add_if_missing("subjects", "has_practical", sa.Column(
        "has_practical", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    _add_if_missing("items", "study_mode", sa.Column(
        "study_mode", sa.String(length=20), nullable=False, server_default=sa.text("'general'")
    ))
    _add_if_missing("quiz_questions", "explanation", sa.Column(
        "explanation", sa.Text(), nullable=True
    ))
    _add_if_missing("quiz_sessions", "thread_id", sa.Column(
        "thread_id", sa.BigInteger(), nullable=True
    ))
    _add_if_missing("quiz_sessions", "paused_at", sa.Column(
        "paused_at", sa.DateTime(), nullable=True
    ))
    _add_if_missing("quiz_sessions", "ends_at", sa.Column(
        "ends_at", sa.DateTime(), nullable=True
    ))
    _add_if_missing("quiz_sessions", "settings_json", sa.Column(
        "settings_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")
    ))


def downgrade() -> None:
    # No destructive downgrade: legacy data may now depend on these columns.
    pass
