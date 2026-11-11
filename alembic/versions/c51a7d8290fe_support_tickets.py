"""Add course support tickets and conversation messages.

Revision ID: c51a7d8290fe
Revises: a6c9431d27be
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c51a7d8290fe"
down_revision: Union[str, Sequence[str], None] = "a6c9431d27be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_slug", sa.String(length=64), nullable=False),
        sa.Column("course_title", sa.String(length=255), nullable=False),
        sa.Column("lesson", sa.String(length=160), server_default="", nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="open", nullable=False),
        sa.Column("admin_notified", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("closed_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "support_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("sender_type", sa.String(length=16), nullable=False),
        sa.Column("sender_name", sa.String(length=80), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("notification_sent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_messages_ticket_id", "support_messages", ["ticket_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_support_messages_ticket_id", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_table("support_tickets")
