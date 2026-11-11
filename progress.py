"""Progress sync API, export, and per-course reset."""
import json

from flask import (Blueprint, Response, flash, jsonify, redirect,
                   request, url_for)

from auth import (check_csrf, current_user, record_attempt,
                  send_course_completed_email, too_many_attempts)
from courses import all_courses, progress_info
from db import get_session as db_session
from models import Progress, now_ms

bp = Blueprint("progress", __name__)

MAX_STATE_BYTES = 1_000_000  # one course's saved progress
API_WRITE_LIMIT = 600        # sync pushes per user per hour


def _row(user_id, slug):
    return db_session().get(Progress, (user_id, slug))


@bp.route("/api/progress/<slug>", methods=["GET", "POST"])
def api(slug):
    c = all_courses().get(slug)
    if not c:
        return jsonify(error="No such course"), 404
    u = current_user()
    if not u:
        return jsonify(error="Log in to save progress"), 401

    if request.method == "GET":
        row = _row(u.id, slug)
        if not row:
            return jsonify(state=None, updated=0, reset_epoch=0)
        return jsonify(state=row.state, updated=row.updated_at,
                       reset_epoch=row.reset_epoch)

    # POST needs the CSRF header as well
    if not check_csrf(request.headers.get("X-CSRF-Token", "")):
        return jsonify(error="Bad token"), 400
    rl_key = f"api|{u.id}"
    if too_many_attempts(rl_key, API_WRITE_LIMIT, 3600):
        return jsonify(error="Too many saves. Take a break and try again later."), 429
    record_attempt(rl_key)
    body = request.get_json(silent=True) or {}
    state = body.get("state")
    if not isinstance(state, str) or len(state.encode("utf8")) > MAX_STATE_BYTES:
        return jsonify(error="State missing or too large"), 400
    try:
        parsed = json.loads(state)
    except ValueError:
        return jsonify(error="State is not valid JSON"), 400
    if not isinstance(parsed, dict):
        return jsonify(error="State must be an object"), 400

    lessons = body.get("lessons")
    if isinstance(lessons, list) and 0 < len(lessons) <= 500 \
            and all(isinstance(x, (bool, int)) and not isinstance(x, str)
                    for x in lessons):
        parsed["_rl_lessons"] = [1 if x else 0 for x in lessons]
        state = json.dumps(parsed)

    done_override = body.get("done")
    if not isinstance(done_override, int) or isinstance(done_override, bool) \
            or not (0 <= done_override <= 100000):
        done_override = None
    done, xp = progress_info(c, state, done_override)
    now = now_ms()

    row = _row(u.id, slug)
    prev_done = row.done_count if row else 0
    just_completed = done >= c["units"] and prev_done < c["units"]
    if row is None:
        row = Progress(user_id=u.id, slug=slug, state=state, xp=xp,
                       done_count=done, updated_at=now, reset_epoch=0)
        db_session().add(row)
    else:
        row.state = state
        row.xp = xp
        row.done_count = done
        row.updated_at = now
    db_session().commit()
    if just_completed:
        send_course_completed_email(u, c, done, xp)
    return jsonify(ok=True, updated=now, reset_epoch=row.reset_epoch)


@bp.route("/api/progress/export")
def export():
    """Download all of this user's saved course states as JSON."""
    u = current_user()
    if not u:
        return redirect(url_for("auth.login", next=request.path))
    s = db_session()
    rows = s.query(Progress).filter_by(user_id=u.id).all()
    courses = all_courses()
    out = {}
    for r in rows:
        c = courses.get(r.slug)
        done, xp = progress_info(c, r.state) if c else (r.done_count, r.xp)
        try:
            state = json.loads(r.state) if r.state else {}
        except (TypeError, ValueError):
            state = {}
        out[r.slug] = {
            "title": c["title"] if c else r.slug,
            "state": state,
            "done": done,
            "xp": xp,
            "updated": r.updated_at,
        }
    payload = json.dumps({"user": u.email, "exported_at": now_ms(), "courses": out},
                         indent=2)
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="raydex-progress.json"'},
    )


@bp.route("/progress/import", methods=["POST"])
def import_progress():
    """Restore progress from a file downloaded via /api/progress/export."""
    from flask import abort

    u = current_user()
    if not u:
        return redirect(url_for("auth.login", next=request.path))
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("Choose a progress JSON file to import.", "err")
        return redirect(url_for("student.progress_page"))
    try:
        data = json.loads(upload.read().decode("utf8"))
    except (ValueError, UnicodeDecodeError):
        flash("That file is not valid JSON.", "err")
        return redirect(url_for("student.progress_page"))
    courses_in = data.get("courses") if isinstance(data, dict) else None
    if not isinstance(courses_in, dict):
        flash("That file does not look like a Raydex Learn progress export.", "err")
        return redirect(url_for("student.progress_page"))

    courses = all_courses()
    s = db_session()
    now = now_ms()
    imported = 0
    for slug, entry in list(courses_in.items())[:100]:
        c = courses.get(slug)
        if not c or not isinstance(entry, dict):
            continue
        state = entry.get("state")
        if not isinstance(state, dict):
            continue
        state_text = json.dumps(state)
        if len(state_text.encode("utf8")) > MAX_STATE_BYTES:
            continue
        done_override = entry.get("done")
        if not isinstance(done_override, int) or isinstance(done_override, bool) \
                or not (0 <= done_override <= 100000):
            done_override = None
        done, xp = progress_info(c, state_text, done_override)
        row = _row(u.id, slug)
        if row is None:
            row = Progress(user_id=u.id, slug=slug, state=state_text, xp=xp,
                           done_count=done, updated_at=now, reset_epoch=0)
            s.add(row)
        else:
            row.state = state_text
            row.xp = xp
            row.done_count = done
            row.updated_at = now
        imported += 1
    s.commit()
    if imported:
        flash(f"Imported progress for {imported} course{'s' if imported != 1 else ''}.", "ok")
    else:
        flash("No usable course progress found in that file.", "err")
    return redirect(url_for("student.progress_page"))


@bp.route("/progress/<slug>/reset", methods=["POST"])
def reset(slug):
    """Wipe this course's progress on the server AND tell other devices to
    drop their local copy (via reset_epoch)."""
    u = current_user()
    if not u:
        return redirect(url_for("auth.login", next=request.path))
    if not check_csrf(request.form.get("csrf")):
        from flask import abort
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    s = db_session()
    row = _row(u.id, slug)
    if row is None:
        row = Progress(user_id=u.id, slug=slug, state="{}", xp=0,
                       done_count=0, updated_at=now_ms(), reset_epoch=1)
        s.add(row)
    else:
        row.state = "{}"
        row.xp = 0
        row.done_count = 0
        row.updated_at = now_ms()
        row.reset_epoch += 1
    s.commit()
    flash("Progress reset. It will clear on your other devices next time you open the course.")
    return redirect(url_for("index"))
