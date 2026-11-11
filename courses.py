"""Course catalogue: loading courses.json and counting saved progress."""
import json
import re
from pathlib import Path

from flask import current_app

BASE = Path(__file__).resolve().parent
COURSES_DIR = BASE / "courses"

_page_cache = {}  # slug -> (mtime, html)


def all_courses():
    """slug -> course dict. Re-reads courses.json so edits need no restart."""
    try:
        data = json.loads((BASE / "courses.json").read_text(encoding="utf8"))
        courses = {c["slug"]: c for c in data}
    except (OSError, ValueError, KeyError):
        return {}
    for slug, c in courses.items():
        c["lessons_list"] = lesson_titles(c)
        c.setdefault("units", c["lessons"])
    return courses


def lesson_titles(course):
    """Ordered lesson titles from courses/<slug>.lessons.json, or []."""
    path = COURSES_DIR / f"{course['slug']}.lessons.json"
    try:
        data = json.loads(path.read_text(encoding="utf8"))
        titles = data.get("lessons")
        return titles if isinstance(titles, list) else []
    except (OSError, ValueError):
        return []


def course_html(course):
    path = COURSES_DIR / course["file"]
    mtime = path.stat().st_mtime
    hit = _page_cache.get(course["slug"])
    if hit and hit[0] == mtime:
        return hit[1]
    html = path.read_text(encoding="utf8")
    _page_cache[course["slug"]] = (mtime, html)
    return html


def progress_info(course, state_text, done_override=None):
    """Return (done_count, xp) from a course's saved state string."""
    try:
        s = json.loads(state_text or "{}")
        if not isinstance(s, dict):
            return 0, 0
    except ValueError:
        return 0, 0
    xp_raw = str(s.get("xp") or "0")
    xp = int(xp_raw) if xp_raw.lstrip("-").isdigit() else 0
    if done_override is not None:
        done = done_override
    else:
        kind = course.get("progress")
        n = 0
        if kind == "done_map":
            done_map = s.get("done")
            if isinstance(done_map, dict):
                n = sum(1 for v in done_map.values() if v)
        elif kind == "python":
            seen = set()
            caps = set()
            for k, v in s.items():
                if not v:
                    continue
                m = re.match(r"^(?:t|chp|fbp|pr|qz)(\d+)(?:_\d+)?$", k)
                if m:
                    seen.add(int(m.group(1)))
                    continue
                m = re.match(r"^cp(\d+)_\d+$", k)
                if m:
                    caps.add(int(m.group(1)))
            n = len(seen) + len(caps)
        done = n
    return min(done, course.get("units") or course["lessons"]), xp


def lesson_flags(course, state_text):
    """Per-lesson done flags ([0/1]) reported by the course sync, or None."""
    try:
        s = json.loads(state_text or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(s, dict):
        return None
    flags = s.get("_rl_lessons")
    if isinstance(flags, list) and flags \
            and all(isinstance(x, (bool, int)) for x in flags):
        return [1 if x else 0 for x in flags]
    if course.get("progress") == "done_map":
        done_map = s.get("done")
        if isinstance(done_map, dict):
            n = course["lessons"]
            return [1 if done_map.get(str(i)) or done_map.get(i) else 0
                    for i in range(n)]
    return None
