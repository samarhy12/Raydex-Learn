"""Authenticated student area: dashboard, my courses, progress, profile, settings."""
import datetime
import hmac
import json

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, session, url_for)
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from auth import (cert_signature, cert_verify_url, check_csrf, current_user,
                  login_required, send_password_changed_email)
from courses import all_courses, progress_info
from db import get_session as db_session
from models import Progress, User

bp = Blueprint("student", __name__)


def user_progress(uid):
    """slug -> dict(done, xp, updated) for this user, skipping empty rows."""
    out = {}
    s = db_session()
    rows = s.query(Progress).filter_by(user_id=uid).all()
    courses = all_courses()
    for r in rows:
        course = courses.get(r.slug)
        if not course:
            continue
        done, xp = progress_info(course, r.state)
        if r.done_count:
            done = min(r.done_count, course["units"])
        xp = xp or r.xp
        if not done and not xp:
            continue
        out[r.slug] = {"done": done, "xp": xp, "updated": r.updated_at,
                       "reset_epoch": r.reset_epoch}
    return out


def _learning_streak(uid):
    """Consecutive-day study streak across all of a user's course states.

    Courses record study days as YYYY-MM-DD strings (UTC) under "days".
    """
    days = set()
    s = db_session()
    for r in s.query(Progress).filter_by(user_id=uid).all():
        try:
            state = json.loads(r.state or "{}")
        except (TypeError, ValueError):
            continue
        for d in state.get("days") or []:
            if isinstance(d, str):
                days.add(d)
    if not days:
        return 0
    cursor = datetime.datetime.now(datetime.timezone.utc).date()
    key = cursor.isoformat()
    if key not in days:
        cursor -= datetime.timedelta(days=1)
        key = cursor.isoformat()
        if key not in days:
            return 0
    streak = 0
    while key in days:
        streak += 1
        cursor -= datetime.timedelta(days=1)
        key = cursor.isoformat()
    return streak


def _prog_view(prog, courses):
    """Attach course info + percent to each prog entry."""
    out = {}
    for slug, p in prog.items():
        c = courses.get(slug)
        if not c:
            continue
        out[slug] = dict(p, course=c,
                        pct=round(100 * p["done"] / c["units"]))
    return out


@bp.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    courses = all_courses()
    prog = _prog_view(user_progress(u.id), courses)
    latest_slug = max(prog, key=lambda s: prog[s]["updated"]) if prog else None
    latest = prog.get(latest_slug) if latest_slug else None
    return render_template(
        "dashboard.html",
        prog=prog,
        latest=latest,
        total_lessons=sum(c["lessons"] for c in courses.values()),
        total_done=sum(p["done"] for p in prog.values()),
        total_xp=sum(p["xp"] for p in prog.values()),
        started=len(prog),
        streak=_learning_streak(u.id),
    )


@bp.route("/my-courses")
@login_required
def my_courses():
    u = current_user()
    courses = all_courses()
    prog = _prog_view(user_progress(u.id), courses)
    groups = {"in_progress": [], "completed": [], "not_started": []}
    for slug, c in courses.items():
        p = prog.get(slug)
        item = {"course": c, "prog": p}
        if not p:
            groups["not_started"].append(item)
        elif p["done"] >= c["units"]:
            groups["completed"].append(item)
        else:
            groups["in_progress"].append(item)
    return render_template("my_courses.html", groups=groups)


@bp.route("/progress")
@login_required
def progress_page():
    u = current_user()
    courses = all_courses()
    prog = _prog_view(user_progress(u.id), courses)
    return render_template(
        "progress.html",
        prog=prog,
        total_lessons=sum(c["lessons"] for c in courses.values()),
        total_done=sum(p["done"] for p in prog.values()),
        total_xp=sum(p["xp"] for p in prog.values()),
    )


def _course_progress(uid, slug, c):
    """(done, xp) for a user+course, honouring stored done_count/xp."""
    s = db_session()
    row = s.get(Progress, (uid, slug))
    done = xp = 0
    if row:
        done, xp = progress_info(c, row.state)
        if row.done_count:
            done = min(row.done_count, c["units"])
        xp = xp or row.xp
    return done, xp


@bp.route("/certificate/<slug>")
@login_required
def certificate(slug):
    """Printable certificate — only when the course is actually complete."""
    u = current_user()
    c = all_courses().get(slug)
    if not c:
        abort(404)
    done, xp = _course_progress(u.id, slug, c)
    if done < c["units"]:
        flash("Finish every lesson to earn this certificate.")
        return redirect(url_for("student.my_courses"))
    return render_template(
        "certificate.html", c=c, done=done, xp=xp,
        cert_name=u.name, verify_url=cert_verify_url(u, slug),
    )


@bp.route("/certificate/<slug>/verify/<int:uid>/<sig>")
def certificate_verify(slug, uid, sig):
    """Public, unguessable link that proves a certificate is genuine."""
    c = all_courses().get(slug)
    if not c:
        abort(404)
    if not hmac.compare_digest(sig, cert_signature(uid, slug)):
        abort(404)
    s = db_session()
    user = s.get(User, uid)
    if not user:
        abort(404)
    done, xp = _course_progress(uid, slug, c)
    if done < c["units"]:
        abort(404)
    return render_template(
        "certificate.html", c=c, done=done, xp=xp,
        cert_name=user.name, verify_public=True,
    )


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    u = current_user()
    if request.method == "POST":
        if not check_csrf(request.form.get("csrf")):
            abort(400, "Your session expired. Go back, refresh the page and try again.")
        name = request.form.get("name", "").strip()[:60]
        if not name:
            flash("Enter your name.")
        else:
            s = db_session()
            row = s.get(User, u.id)
            row.name = name
            s.commit()
            flash("Profile updated.")
            return redirect(url_for("student.profile"))
    import time as _t
    member_since = _t.strftime("%d %b %Y", _t.gmtime(u.created_at))
    return render_template("profile.html", member_since=member_since)


@bp.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    """Permanently delete the account and all its progress."""
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    u = current_user()
    s = db_session()
    row = s.get(User, u.id)
    if not row.password_set:
        flash("Set a password in Settings before deleting your account.")
        return redirect(url_for("student.settings"))
    if not check_password_hash(row.pw_hash, request.form.get("password", "")):
        flash("Password is wrong — account not deleted.")
        return redirect(url_for("student.settings"))
    s.query(Progress).filter_by(user_id=u.id).delete()
    s.delete(row)
    s.commit()
    from flask import session
    session.clear()
    flash("Your account and all saved progress have been deleted.")
    return redirect(url_for("index"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    u = current_user()
    if request.method == "POST":
        if not check_csrf(request.form.get("csrf")):
            abort(400, "Your session expired. Go back, refresh the page and try again.")
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        new2 = request.form.get("new_password2", "")
        s = db_session()
        row = s.get(User, u.id)
        if row.password_set and not check_password_hash(row.pw_hash, current):
            flash("Your current password is wrong.")
        elif len(new) < 8:
            flash("Use a password with at least 8 characters.")
        elif new != new2:
            flash("The two new passwords do not match.")
        else:
            row.pw_hash = generate_password_hash(new)
            row.password_set = 1
            row.session_version = (row.session_version or 0) + 1
            s.commit()
            # Keep this session; every other signed-in session is dropped.
            session["sver"] = row.session_version
            send_password_changed_email(row)
            flash("Password updated.")
            return redirect(url_for("student.settings"))
    return render_template("settings.html", password_set=current_user().password_set)
