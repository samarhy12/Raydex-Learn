"""Raydex Learn: one Flask app that holds all the courses.

Public site: marketing homepage, course catalogue and details, about,
contact, privacy, terms. Student area (dashboard, my courses, progress,
profile, settings) lives behind login. Courses themselves are
self-contained HTML files in /courses with a sync script injected.

Config via environment (see .env):
  DATABASE_URL   sqlite:///... (default) or mysql+pymysql://user:pass@host/db
  SECRET_KEY     long random string (a dev one is generated otherwise)
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (optional Google sign-in)
  COOKIE_SECURE  "1" once the site runs on https
  ADMIN_EMAILS   comma-separated emails allowed to open /admin
"""
import gzip
import html
import json
import os
import re
import secrets
import time
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, url_for)
from sqlalchemy import func, select
from werkzeug.middleware.proxy_fix import ProxyFix

import auth as auth_mod
import courses as courses_mod
import db as db_mod
import progress as progress_mod
import student as student_mod
import support as support_mod
from auth import admin_required, check_csrf, csrf_token, current_user
from db import get_session as db_session
from models import Progress, SupportTicket, User

BASE = Path(__file__).resolve().parent
INSTANCE = BASE / "instance"
INSTANCE.mkdir(exist_ok=True)

load_dotenv(BASE / ".env")

SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")


def load_secret_key():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    # Dev fallback: make one and keep it, so logins survive restarts.
    f = INSTANCE / "secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


def default_database_url():
    return db_mod.default_url()


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=load_secret_key(),
        DATABASE_URL=os.environ.get("DATABASE_URL", default_database_url()),
        SITE_URL=SITE_URL,
        GOOGLE_CLIENT_ID=os.environ.get("GOOGLE_CLIENT_ID"),
        GOOGLE_CLIENT_SECRET=os.environ.get("GOOGLE_CLIENT_SECRET"),
        SUPPORT_EMAIL=os.environ.get("SUPPORT_EMAIL", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=60),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SEND_FILE_MAX_AGE_DEFAULT=3600,
    )
    if config:
        app.config.update(config)
    # Trust X-Forwarded-* when behind a reverse proxy (Render, Nginx, etc.)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    db_mod.register_db(app)
    auth_mod.init_oauth(app)

    sentry_dsn = os.environ.get("SENTRY_DSN")
    if sentry_dsn:
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=sentry_dsn, traces_sample_rate=0.1)
        except ImportError:
            app.logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
    app.register_blueprint(auth_mod.bp)
    app.register_blueprint(progress_mod.bp)
    app.register_blueprint(student_mod.bp)
    app.register_blueprint(support_mod.bp)

    @app.before_request
    def csrf_protect():
        if request.method == "POST" and not request.path.startswith("/api/"):
            if not check_csrf(request.form.get("csrf")):
                abort(400, "Your session expired. Go back, refresh the page and try again.")

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: http: https:; "
            "connect-src 'self'; "
            "worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        return resp

    @app.after_request
    def gzip_text(resp):
        """Compress large text responses; course pages are ~1.7 MB of HTML."""
        if (
            resp.status_code >= 400
            or resp.headers.get("Content-Encoding")
            or "gzip" not in request.headers.get("Accept-Encoding", "")
            or resp.mimetype
            not in ("text/html", "text/css", "text/plain", "application/javascript",
                    "text/javascript", "application/json", "image/svg+xml",
                    "application/xml")
        ):
            return resp
        resp.direct_passthrough = False
        data = resp.get_data()
        if len(data) < 1024:
            return resp
        resp.set_data(gzip.compress(data, compresslevel=6))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(resp.get_data()))
        vary = resp.headers.get("Vary", "")
        if "accept-encoding" not in vary.lower():
            resp.headers["Vary"] = (vary + ", Accept-Encoding").strip(", ")
        return resp

    @app.context_processor
    def inject_globals():
        courses = courses_mod.all_courses()
        u = current_user()
        prog = student_mod.user_progress(u.id) if u else {}
        return {"user": current_user(), "csrf_token": csrf_token,
                "all_courses": courses.values(), "prog": prog,
                "now_year": time.gmtime().tm_year,
                "site_url": app.config["SITE_URL"] or request.url_root.rstrip("/"),
                "google_oauth_enabled": bool(app.extensions.get("google_oauth"))}

    # ------------------------------------------------------------- public
    @app.route("/")
    def index():
        courses = courses_mod.all_courses()
        return render_template(
            "index.html",
            courses=list(courses.values()),
            total_lessons=sum(c["lessons"] for c in courses.values()),
        )

    @app.route("/courses")
    def catalogue():
        return render_template("courses.html",
                               courses=list(courses_mod.all_courses().values()))

    @app.route("/courses/<slug>")
    def course_detail(slug):
        c = courses_mod.all_courses().get(slug)
        if not c:
            abort(404)
        p = None
        part_done = None
        u = current_user()
        if u:
            from models import Progress as _P
            row = db_session().get(_P, (u.id, slug))
            if row:
                done, xp = courses_mod.progress_info(c, row.state)
                if row.done_count:
                    done = min(row.done_count, c["units"])
                p = {"done": done, "xp": xp or row.xp,
                     "pct": round(100 * done / c["units"])}
                flags = courses_mod.lesson_flags(c, row.state)
                if flags:
                    part_done = []
                    idx = 0
                    for part in c.get("parts", []):
                        n = part.get("lessons", 0)
                        part_done.append(sum(flags[idx:idx + n]))
                        idx += n
        return render_template("course_detail.html", c=c, prog=p,
                               part_done=part_done)

    @app.route("/about")
    def about():
        return render_template("about.html")

    @app.route("/contact")
    def contact():
        return render_template("contact.html")

    @app.route("/privacy")
    def privacy():
        return render_template("privacy.html")

    @app.route("/terms")
    def terms():
        return render_template("terms.html")

    # ------------------------------------------------------- course player
    @app.route("/learn/<slug>")
    def course_redirect(slug):
        return redirect(url_for("course", slug=slug), code=301)

    @app.route("/learn/<slug>/")
    def course(slug):
        courses = courses_mod.all_courses()
        c = courses.get(slug)
        if not c:
            abort(404)
        page_html = courses_mod.course_html(c)
        canonical_base = app.config["SITE_URL"] or request.url_root.rstrip("/")
        canonical_url = canonical_base + url_for("course_detail", slug=slug)
        meta_snippet = render_template(
            "course_player_meta.html",
            c=c,
            canonical_url=canonical_url,
            social_image=canonical_base + url_for("static", filename=c["thumb"]),
        )
        title = c.get("seo_title", c["title"])
        page_html = re.sub(
            r"<title\b[^>]*>.*?</title>",
            f"<title>{html.escape(title)}</title>",
            page_html,
            count=1,
            flags=re.I | re.S,
        )
        head = re.search(r"<head[^>]*>", page_html, re.I)
        if head:
            page_html = page_html[:head.end()] + meta_snippet + page_html[head.end():]

        u = current_user()
        server_state, updated, reset_epoch = None, 0, 0
        if u:
            row = db_session().get(Progress, (u.id, slug))
            if row:
                server_state, updated = row.state, row.updated_at
                reset_epoch = row.reset_epoch

        cfg = {
            "slug": slug,
            "key": c["storage_key"],
            "title": c["title"],
            "home": url_for("index"),
            "login": url_for("auth.login", next=request.path),
            "dash": url_for("student.dashboard") if u else None,
            "api": url_for("progress.api", slug=slug),
            "csrf": csrf_token(),
            "uid": u.id if u else None,
            "state": server_state,
            "updated": updated,
            "reset_epoch": reset_epoch,
        }
        support_stylesheet = (
            '<link rel="stylesheet" href="'
            + url_for("static", filename="support-widget.css")
            + '?v=1">'
        )
        snippet = support_stylesheet + render_template(
            "sync_snippet.html", cfg_json=safe_json(cfg)
        )

        support_widget = render_template(
            "support/widget.html", course_context=c, lesson=""
        )
        # Course pages embed example HTML inside inline <script> strings, so a
        # literal "</body>" can appear mid-document. The real closing tag is
        # the last one; injecting earlier would break the script block.
        body_ends = list(re.finditer(r"</body\s*>", page_html, re.I))
        if body_ends:
            body_end = body_ends[-1]
            page_html = (
                page_html[: body_end.start()]
                + support_widget
                + page_html[body_end.start() :]
            )
        else:
            page_html += support_widget

        # Insert right after the opening <head> so the restore runs before
        # the course script reads localStorage.
        m = re.search(r"<head[^>]*>", page_html, re.I)
        if m:
            page_html = page_html[: m.end()] + snippet + page_html[m.end():]
        else:
            page_html = snippet + page_html
        resp = Response(page_html, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-store"  # page carries this user's state
        return resp

    # ---------------------------------------------------------------- admin
    @app.route("/admin")
    @admin_required
    def admin():
        s = db_session()
        users = s.execute(select(User).order_by(User.created_at.desc())).scalars().all()
        courses = courses_mod.all_courses()
        stats = {}
        for r in s.query(Progress.slug, func.count(), func.sum(Progress.xp)).group_by(Progress.slug):
            stats[r[0]] = {"learners": r[1], "xp": r[2] or 0}
        rows = []
        for u in users:
            prog = s.query(Progress).filter_by(user_id=u.id).all()
            rows.append({
                "user": u,
                "courses": len(prog),
                "xp": sum(p.xp for p in prog),
                "done": sum(p.done_count for p in prog),
            })
        return render_template("admin.html", users=rows, course_stats=stats,
                               courses=courses, total_users=len(users),
                               verified=s.query(func.count(User.id)).filter(User.verified == 1).scalar() or 0,
                               active_tickets=s.query(func.count(SupportTicket.id)).filter(
                                   SupportTicket.status != "closed"
                               ).scalar() or 0)

    @app.route("/admin/users/<int:user_id>/verify", methods=["POST"])
    @admin_required
    def admin_user_verify(user_id):
        s = db_session()
        user = s.get(User, user_id)
        if not user:
            abort(404)
        user.verified = 0 if user.verified else 1
        s.commit()
        flash(f"{user.email} is now {'verified' if user.verified else 'unverified'}.", "ok")
        return redirect(url_for("admin"))

    @app.route("/admin/users/<int:user_id>/reset-link", methods=["POST"])
    @admin_required
    def admin_user_reset_link(user_id):
        """Email the user a fresh password-reset link (support tool)."""
        s = db_session()
        user = s.get(User, user_id)
        if not user:
            abort(404)
        token = auth_mod._issue_token(user)
        link = url_for("auth.reset", token=token, _external=True)
        if auth_mod.send_reset_link(user, link):
            flash(f"Password reset link sent to {user.email}.", "ok")
        else:
            auth_mod._invalidate_token(token)
            flash(f"Could not send the reset email to {user.email}. Check SMTP settings.", "err")
        return redirect(url_for("admin"))

    @app.route("/admin/users/<int:user_id>/disable", methods=["POST"])
    @admin_required
    def admin_user_disable(user_id):
        """Disable or re-enable a learner account (signs them out immediately)."""
        s = db_session()
        user = s.get(User, user_id)
        me = current_user()
        if not user:
            abort(404)
        if me and user.id == me.id:
            flash("You can't disable your own admin account.", "err")
            return redirect(url_for("admin"))
        user.disabled = 0 if user.disabled else 1
        if user.disabled:
            user.session_version = (user.session_version or 0) + 1
        s.commit()
        state = "disabled" if user.disabled else "re-enabled"
        flash(f"{user.email} is now {state}.", "ok")
        return redirect(url_for("admin"))

    # ------------------------------------------------------------------ SEO
    @app.route("/sitemap.xml")
    def sitemap():
        urls = [
            url_for("index"),
            url_for("catalogue"),
            url_for("about"),
            url_for("contact"),
            url_for("privacy"),
            url_for("terms"),
        ]
        urls += [
            url_for("course_detail", slug=s)
            for s in courses_mod.all_courses()
        ]
        base = app.config["SITE_URL"] or request.url_root.rstrip("/")
        body = "".join(
            f"<url><loc>{html.escape(base + u)}</loc></url>" for u in urls
        )
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               + body + "</urlset>")
        return Response(xml, mimetype="application/xml")

    @app.route("/robots.txt")
    def robots():
        base = app.config["SITE_URL"] or request.url_root.rstrip("/")
        return Response(
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /dashboard\nDisallow: /my-courses\nDisallow: /progress\n"
            "Disallow: /profile\nDisallow: /settings\nDisallow: /admin\n"
            "Disallow: /api/\nDisallow: /learn/\n"
            f"Sitemap: {base}/sitemap.xml\n",
            mimetype="text/plain")

    @app.route("/healthz")
    def healthz():
        db_session().execute(select(1))
        return "ok"

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("message.html", title="Page not found",
                               text="That page does not exist or may have moved."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("message.html", title="Something went wrong",
                               text=getattr(e, "description", "Bad request.")), 400

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("message.html", title="Something went wrong",
                               text="An unexpected error occurred. Please try again."), 500

    return app


def safe_json(obj):
    """JSON that is safe to place inside a <script> tag."""
    s = json.dumps(obj)
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1",
            port=int(os.environ.get("PORT", 5000)))
