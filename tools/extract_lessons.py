"""Extract lesson titles from course HTML files into courses/<slug>.lessons.json.

Run after editing a course's lesson list:
    python tools/extract_lessons.py
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# (slug, html file, how titles appear)
# python:  LS[N]={t:"Title"  (indexed assignments)
# webdev:  LS=[{t:"Title", ...}, ...]  (array literal)
SOURCES = {
    "python": ("python.html", "indexed"),
    "webdev": ("webdev.html", "array"),
}

T = r'\{t:"((?:[^"\\]|\\.)*)"'


def unescape(s):
    return s.replace('\\"', '"').replace("\\n", " ").replace("\\'", "'")


def extract(slug, fname, mode):
    text = (BASE / "courses" / fname).read_text(encoding="utf8")
    titles = []
    if mode == "indexed":
        # LS[0]={t:"..." ... LS[41]={t:"..."
        for m in re.finditer(r"LS\[(\d+)\]=" + T, text):
            titles.append((int(m.group(1)), unescape(m.group(2))))
        titles.sort()
        out = [t for _, t in titles]
    else:
        # first LS=[ ... ]; objects start with {t:"...
        start = text.index("const LS=[")
        out = []
        # titles appear in order inside the array
        for m in re.finditer(T, text[start:]):
            out.append(unescape(m.group(1)))
    return out


def main():
    for slug, (fname, mode) in SOURCES.items():
        titles = extract(slug, fname, mode)
        # sanity: should be non-trivial and unique-ish
        print(f"{slug}: {len(titles)} lessons")
        path = BASE / "courses" / f"{slug}.lessons.json"
        path.write_text(json.dumps({"lessons": titles}, indent=1), encoding="utf8")
        print(f"  wrote {path.name}")


if __name__ == "__main__":
    main()
