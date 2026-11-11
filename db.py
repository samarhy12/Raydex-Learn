"""Database setup. Everything goes through SQLAlchemy.

DATABASE_URL selects the backend:
  sqlite:///instance/raydex.db        (default, dev)
  mysql+pymysql://user:pass@host/db   (production)

For MySQL, install PyMySQL:  pip install pymysql
"""
import os
from pathlib import Path

from flask import Flask, g
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE = Path(__file__).resolve().parent
INSTANCE = BASE / "instance"
INSTANCE.mkdir(exist_ok=True)


class Base(DeclarativeBase):
    pass


def default_url():
    return "sqlite:///" + str(INSTANCE / "raydex.db").replace("\\", "/")


def make_engine(url):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def init_db(app: Flask):
    url = app.config["DATABASE_URL"]
    engine = make_engine(url)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys = ON")
            cur.close()

    app.extensions["engine"] = engine
    app.extensions["session_factory"] = sessionmaker(bind=engine, expire_on_commit=False)

    Base.metadata.create_all(engine)
    _migrate(engine, app)


def _migrate(engine, app):
    """Add columns introduced after the first release. Cheap no-ops on fresh DBs."""
    from sqlalchemy import text

    alters = [
        ("progress", "done_count", "ALTER TABLE progress ADD COLUMN done_count INTEGER NOT NULL DEFAULT 0"),
        ("progress", "reset_epoch", "ALTER TABLE progress ADD COLUMN reset_epoch INTEGER NOT NULL DEFAULT 0"),
        ("users", "verified", "ALTER TABLE users ADD COLUMN verified INTEGER NOT NULL DEFAULT 1"),
        ("users", "password_set", "ALTER TABLE users ADD COLUMN password_set INTEGER NOT NULL DEFAULT 1"),
        ("users", "session_version", "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0"),
        ("users", "disabled", "ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0"),
        ("password_resets", "purpose", "ALTER TABLE password_resets ADD COLUMN purpose VARCHAR(16) NOT NULL DEFAULT 'reset'"),
    ]
    with engine.begin() as conn:
        for table, col, ddl in alters:
            try:
                conn.execute(text(ddl))
            except Exception:
                pass  # column already exists (or dialect quirk) - fine

    # Backfill done_count for rows saved before the column existed.
    try:
        from courses import all_courses, progress_info

        with engine.begin() as conn:
            rows = conn.execute(text("SELECT user_id, slug, state FROM progress WHERE done_count = 0")).fetchall()
            for uid, slug, state in rows:
                course = all_courses().get(slug)
                if not course:
                    continue
                done, _xp = progress_info(course, state)
                if done:
                    conn.execute(
                        text("UPDATE progress SET done_count = :d WHERE user_id = :u AND slug = :s"),
                        {"d": done, "u": uid, "s": slug},
                    )
    except Exception as e:  # never let a backfill break startup
        app.logger.warning("done_count backfill skipped: %s", e)


def get_session():
    return g.db_session


def register_db(app: Flask):
    init_db(app)

    @app.before_request
    def open_session():
        g.db_session = app.extensions["session_factory"]()

    @app.teardown_appcontext
    def close_session(_exc):
        s = g.pop("db_session", None)
        if s is not None:
            s.close()
