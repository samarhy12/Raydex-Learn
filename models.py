"""SQLAlchemy models. Tables match schema.sql plus the newer columns."""
import time
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


def now_ms():
    return int(time.time() * 1000)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    pw_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_set: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))
    # 1 = verified (existing accounts default to this); 0 = awaiting email verify
    verified: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Bumped on password change/reset; sessions carrying an older value are dropped.
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 1 = account disabled by an admin; sessions are dropped and login is refused.
    disabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OAuthIdentity(Base):
    __tablename__ = "oauth_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class Progress(Base):
    __tablename__ = "progress"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    done_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    # Bumped on "reset progress". Clients wipe their local copy when the
    # server epoch is newer than the one they last saw.
    reset_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class LoginAttempt(Base):
    """DB-backed throttle so limits survive restarts and work across workers."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    attempted_at: Mapped[int] = mapped_column(Integer, nullable=False)


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="reset")
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=now_ms)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    course_title: Mapped[str] = mapped_column(String(255), nullable=False)
    lesson: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    admin_notified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=now_ms)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=now_ms)
    closed_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(80), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    notification_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=now_ms)
