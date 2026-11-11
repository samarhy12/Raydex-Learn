# Raydex Learn

One Flask app that holds all the Raydex courses — the education product in the Raydex Hub ecosystem.

**Public site:** marketing homepage, course catalogue (`/courses`), course detail pages, about, contact, privacy and terms.
**Student area** (login required): dashboard, my courses, progress, profile and settings — all behind a sidebar shell.
**Courses** are self-contained HTML files in `courses/` with progress synced to the account.

## Run it

```
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

## Database

SQLAlchemy drives all database access. The backend is chosen by `DATABASE_URL`:

```
DATABASE_URL=sqlite:///instance/raydex.db     # default, no setup needed
DATABASE_URL=mysql+pymysql://user:pass@host/db
```

For MySQL, install the driver (`pip install pymysql`) and create the database first — tables are created automatically on startup. Existing SQLite data does not migrate itself; export progress from the UI or copy the tables over.

## Database migrations (Alembic)

Schema lives in `models.py`; Alembic tracks changes in `alembic/versions/`.

```
alembic upgrade head     # apply pending migrations (fresh deploys)
alembic revision --autogenerate -m "describe change"   # after editing models.py
alembic current / history / downgrade -1
```

`DATABASE_URL` (or `.env`) picks the target, same as the app. If you are upgrading a database that already has the tables (created before Alembic), run `alembic stamp head` once instead of `upgrade`.

## Environment variables

Copy `.env.example` to `.env` and fill it in (`.env` is gitignored). Real environment variables always win over `.env`. On hosts like Render/Railway, set the same names in the dashboard instead.

- `SECRET_KEY`: a long random string. Make one with `python -c "import secrets; print(secrets.token_hex(32))"`. A dev one is generated and kept in `instance/` if unset.
- `DATABASE_URL`: see above.
- `COOKIE_SECURE=1`: once the site runs on https.
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`: optional Google sign-in credentials. In Google Cloud Console, configure an OAuth client of type **Web application**, add your deployed callback URL (`https://your-domain/login/google/callback`) as an authorized redirect URI, and add `http://127.0.0.1:5000/login/google/callback` for local testing. Set both values in `.env` or your hosting provider's secret settings. The login and registration pages show Google sign-in when both are configured. Google identities are matched by Google's stable account ID; a verified Google email may be linked to an existing account with the same email.
- `ADMIN_EMAILS`: comma-separated emails allowed to open `/admin` (user and per-course stats) and manage support tickets.
- `SUPPORT_EMAIL`: inbox for new course questions and learner replies. Ticket conversations are stored in the app; email messages are notifications linking back to the thread.
- `SITE_URL`: canonical site URL used in page metadata, `sitemap.xml` and `robots.txt`. Set this to `https://learn.raydexhub.com` in production.
- `FLASK_DEBUG=1`: local debugging only, never in production.
- `SENTRY_DSN`: optional. When set (and `sentry-sdk` is installed), unhandled errors are reported to Sentry.
- `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`: used to send email-verification and password-reset links over SMTP. Ports 587 (STARTTLS) and 465 (implicit TLS) are supported. Set `SMTP_USER` and `SMTP_PASS` together when the provider requires authentication. Without `SMTP_HOST`, email delivery is disabled and the site tells the learner to try again; link tokens are never written to logs.

### Gmail / Google Workspace mail setup

The `.env.example` includes the non-secret Gmail SMTP host and port. Copy it to `.env`, then:

1. Use the Gmail address that owns the app password for both `SMTP_USER` and `MAIL_FROM`. Emails display **Raydex Learn** as the sender name. If you want a different sender address, first add and verify it as an authorized Gmail "Send mail as" alias.
2. Enable 2-Step Verification for the authenticating Google account, then create an **App password** in that account's Google Account security settings. Workspace administrators may need to allow app passwords. Do not use the account's normal Google password.
3. Put the generated app password in `SMTP_PASS` in your local `.env` (remove spaces if Google displays them). Never commit `.env` or paste the app password into chat.
4. Restart the app. Register a test account with an email address you control, then open the emailed verification link.
5. For a production custom domain, configure SPF and DKIM for Google Workspace, and publish a DMARC policy, to improve delivery and reduce spoofing.

If Google Workspace does not permit app passwords, use its SMTP relay or another transactional-email provider; set that provider's SMTP host, port, username, and password in the same variables.

### Course support tickets

Set `SUPPORT_EMAIL` to the inbox that should receive course questions and configure SMTP for notifications. Signed-in learners can open a ticket from a course detail page or lesson player, then continue the conversation from **Support** in their account. Admins in `ADMIN_EMAILS` can review, reply, change status, close/reopen, and retry failed notification emails at `/admin/tickets`. Email is only a notification; the ticket conversation remains in the app.

## Add a course

1. Put the HTML file in `courses/`.
2. Add an entry to `courses.json`:

```
{
  "slug": "sql",
  "title": "SQL for Beginners",
  "file": "sql.html",
  "storage_key": "the-localStorage-key-the-course-uses",
  "progress": "done_map",
  "progress_label": "lessons done",
  "lessons": 30,
  "level": "Complete beginner",
  "accent": "green",
  "summary": "One or two plain sentences.",
  "topics": ["SQL", "Joins"],
  "thumb": "img/sql.svg",
  "objectives": ["Write SELECT queries", "Join tables"],
  "skills": ["SQL", "Joins"],
  "prerequisites": "None.",
  "parts": [
    {"name": "Basics", "lessons": 10},
    {"name": "Joins", "lessons": 20}
  ]
}
```

3. Done — `courses.json` is re-read on every request, no restart needed. The course appears on the home page at `/learn/sql/`.

`accent` is `green` or `amber`. `thumb` is a path under `static/` shown on course cards (SVG works well).
`objectives`, `skills` and `prerequisites` fill the course detail page — keep them truthful, no invented claims.
`parts` draws the course map bar on the home page and the curriculum modules on the detail page. The part lessons should add up to `lessons`.

`storage_key` must match the key the course saves its progress under.
Current keys: Python course `ztml-v1`, Web Dev course `raydexjs4`.

`progress` tells the home page how to count lessons:
- `done_map`: the saved state has a `done` object like `{"0": true, "3": true}`.
- `python`: the course reports its exact finished-items count via `window.RAYDEX_PROGRESS()`; the storage-key fallback counts lessons and capstones/projects that have any saved activity.
- Anything else: the bar shows 0 and only XP is stored.

`units` (optional, defaults to `lessons`) is the progress denominator when it differs from the lesson count — the Python course counts its 42 lessons plus 57 capstones and real-world projects (99 units). Progress bars, completion checks and certificates use `units`; `lessons` still drives the curriculum display and parts.

A course can also expose `window.RAYDEX_PROGRESS()` returning a number. `sync.js` sends it as the exact "items done" count on every save, which beats guessing from storage keys. Exposing `window.RAYDEX_LESSONS()` returning an array of 0/1 per lesson additionally powers the per-module "x/y done" counts on the course detail page; it is stored in the synced state as `_rl_lessons`.

## How progress sync works

A small script (`static/sync.js`) is injected at the top of each course page.
Before the course starts, it restores the saved progress from the account.
After that, it watches the course's storage key — in this tab, other tabs, and on save/close/online — and pushes changes to the server about 2 seconds later.

If the account and the device disagree, the copy with more XP wins.
Two accounts on one device never mix. The second account starts clean.
"Reset progress" bumps a per-course epoch on the server; every device drops its local copy the next time it opens the course.

## Password reset

New email/password accounts start unverified and receive a single-use verification link (30 minute expiry, stored hashed). The dashboard offers a rate-limited resend action. Google sign-ins use Google's verified email and do not need a separate verification email. `/forgot` sends a separate-purpose, single-use password-reset token. Configure the `SMTP_*` variables above to enable both messages. Login attempts, sign-ups, verification sends and progress-sync writes are rate-limited in the `login_attempts` table (survives restarts, shared across workers).

## Tests

```
pytest
```

Covers register/login, progress sync round-trip, reset, export, admin gate, login throttle and the API write limit.

## Deploy

Set the environment variables above and start with:

```
gunicorn -w 2 -b 0.0.0.0:8000 app:app
```

Easy hosts: PythonAnywhere, Render, Railway, or any small VPS behind Nginx.
SQLite is fine for thousands of learners. Point `DATABASE_URL` at Postgres or MySQL when you outgrow it.

### Production domain and search indexing

For the Raydex Learn production site, set `SITE_URL=https://learn.raydexhub.com`, point the `learn` subdomain at your hosting provider, and enable HTTPS. The public home, course catalogue, course detail pages, and About page emit canonical URLs; course detail pages include Course structured data. The sitemap is available at `https://learn.raydexhub.com/sitemap.xml`. Add the verified domain property to Google Search Console and submit that sitemap after DNS and HTTPS are live. Authenticated pages and interactive course-player URLs are not intended as search landing pages.

## Emails

All mail is branded and sent as **Raydex Learn** through `_deliver` in `auth.py` (SMTP via `SMTP_*`/`MAIL_FROM`). Learners receive: a welcome email on signup (password and Google), an email-verification link, a password-changed notice (also sent after a reset-link change), a course-completed note with the certificate link, and support-ticket replies/closures. Admins receive ticket notifications at `SUPPORT_EMAIL`.

## Operations

- `GET /healthz` returns `ok` for uptime monitors (it also exercises the database).
- A Content-Security-Policy header is sent on every response: `default-src 'self'`, scripts allow inline + eval (course pages and Skulpt need them), styles allow inline + Google Fonts, `object-src 'none'`, `frame-ancestors 'none'`, `form-action 'self'`. Tighten with nonces if the inline surface ever shrinks.
- HTML/CSS/JS/JSON/SVG responses over 1 KB are gzip-compressed when the client accepts it — including the large course vendor bundles in `static/course-assets/`.
- Password changes and resets bump `users.session_version`, signing out every other session; the session that made the change stays signed in.
- Progress can be exported and re-imported from **Progress** (`/progress`), so accounts are portable.
- Certificates have public verification links (`/certificate/<slug>/verify/<uid>/<sig>`, HMAC-signed) — the completion email links to them, so anyone can confirm a certificate is genuine without an account.
- `/admin` lets admins verify/unverify emails, send password-reset links, and disable/enable learner accounts.

## Known gaps (good next jobs)

- The status pill sits bottom-left over the course. Move it if it covers a course control.
- Course pages were slimmed down by moving the CodeMirror/Prettier vendor bundles into cacheable static files (`static/course-assets/`); the remaining inline script is the course app itself. The Python course's Skulpt engine is bundled the same way (`skulpt.min.js`, `skulpt-stdlib.js`), so it runs without a CDN.
