"""Separate email verification tokens from password resets.

Revision ID: a6c9431d27be
Revises: 91d2b63fa40c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6c9431d27be"
down_revision: Union[str, Sequence[str], None] = "91d2b63fa40c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "password_resets",
        sa.Column("purpose", sa.String(length=16), nullable=False, server_default="reset"),
    )


def downgrade() -> None:
    op.drop_column("password_resets", "purpose")
