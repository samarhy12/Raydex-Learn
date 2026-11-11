"""Add Google OAuth identities and password-set state.

Revision ID: 91d2b63fa40c
Revises: 634521243346
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "91d2b63fa40c"
down_revision: Union[str, Sequence[str], None] = "634521243346"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_set", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject"),
    )


def downgrade() -> None:
    op.drop_table("oauth_identities")
    op.drop_column("users", "password_set")
