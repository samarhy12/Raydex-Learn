"""Add users.disabled for admin account disable/enable.

Revision ID: e5f9a2b3c4d5
Revises: d4e8f1a2b3c4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f9a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d4e8f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("disabled", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "disabled")
