import json
import re

import pytest

from app import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASS", "")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    db = tmp_path / "test.db"
    app = create_app({
        "DATABASE_URL": "sqlite:///" + str(db).replace("\\", "/"),
        "SECRET_KEY": "test-secret",
        "TESTING": True,
    })
    with app.test_client() as c:
        yield c


def csrf(client, value="test-csrf"):
    with client.session_transaction() as s:
        s["csrf"] = value
    return value


def register(client, email="a@b.com", pw="password123", name="Ann"):
    token = csrf(client)
    return client.post("/register", data={
        "csrf": token, "name": name, "email": email, "password": pw,
    })


def login(client, email="a@b.com", pw="password123"):
    token = csrf(client)
    return client.post("/login", data={
        "csrf": token, "email": email, "password": pw,
    })


def test_register_login_and_progress_roundtrip(client):
    r = register(client)
    assert r.status_code == 302

    page = client.get("/learn/webdev/")
    assert page.status_code == 200
    assert "window.__RL" in page.get_data(as_text=True)

    token = csrf(client)
    state = json.dumps({"xp": 120, "done": {"0": True, "4": True}})
    r = client.post("/api/progress/webdev",
                    json={"state": state, "done": 2},
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r = client.get("/api/progress/webdev")
    j = r.get_json()
    assert j["state"] == state
    assert j["reset_epoch"] == 0

    dash = client.get("/dashboard").get_data(as_text=True)
    assert "2 of 70" in dash  # done_count override was stored


def test_public_seo_metadata_and_course_structured_data(client):
    client.application.config["SITE_URL"] = "https://learn.raydexhub.com"

    home = client.get("/").get_data(as_text=True)
    assert '<link rel="canonical" href="https://learn.raydexhub.com/">' in home
    assert "Free Coding Courses: Python &amp; Web Development" in home
    assert "Agyarey Raphael Yeboah Wadie" in home
    assert "/static/img/ralph.jpg" in home

    about = client.get("/about").get_data(as_text=True)
    assert "Meet the creator" in about
    assert "Accra, Ghana" in about
    assert "Agyarey Raphael Yeboah Wadie" in about

    detail = client.get("/courses/python").get_data(as_text=True)
    assert "Python Programming for Beginners to Machine Learning" in detail
    assert 'application/ld+json' in detail
    assert '"@type": "Course"' in detail
    assert '"parentOrganization"' in detail
    assert 'https://learn.raydexhub.com/courses/python' in detail

    player = client.get("/learn/python/").get_data(as_text=True)
    assert '<meta name="robots" content="noindex,follow">' in player
    assert '<link rel="canonical" href="https://learn.raydexhub.com/courses/python">' in player

    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "https://learn.raydexhub.com/courses/python" in sitemap
    assert "https://learn.raydexhub.com/about" in sitemap


def test_course_pages_include_question_form(client):
    detail = client.get("/courses/python").get_data(as_text=True)
    assert 'id="support-launcher"' in detail
    assert "Open support messages" in detail
    assert "Question about Python to Machine Learning" in detail

    player = client.get("/learn/python/").get_data(as_text=True)
    assert 'id="support-launcher"' in player
    assert "Open support messages" in player

    home = client.get("/").get_data(as_text=True)
    assert 'id="support-launcher"' in home
    register(client)
    home = client.get("/").get_data(as_text=True)
    assert "General question" in home
    assert 'id="support-course"' in home


def test_homepage_support_form_creates_general_ticket(client, monkeypatch):
    import support as support_mod
    from sqlalchemy import select
    from models import SupportTicket, User

    client.application.config["SUPPORT_EMAIL"] = "support@example.test"
    monkeypatch.setattr(support_mod, "_deliver", lambda *args, **kwargs: True)
    register(client)
    response = client.post("/support/new", data={
        "csrf": csrf(client),
        "course": "",
        "subject": "General question",
        "body": "I have a question about my account.",
    })
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/support/")
    with client.application.app_context():
        s = client.application.extensions["session_factory"]()
        user = s.execute(select(User).where(User.email == "a@b.com")).scalar_one()
        ticket = s.execute(select(SupportTicket).where(
            SupportTicket.user_id == user.id
        )).scalar_one()
        assert ticket.course_slug == "general"
        assert ticket.course_title == "General question"


def test_course_widget_injection_uses_last_body_tag(client, monkeypatch):
    """Courses embed example HTML inside <script> strings; a literal </body>
    mid-document must not receive the support widget (it would break the
    script block, as happened on the webdev course)."""
    import courses as courses_mod

    fake = {"slug": "fake", "title": "Fake Course", "file": "fake.html",
            "thumb": "img/webdev.svg", "storage_key": "fakekey",
            "progress": "done_map", "lessons": 1}
    monkeypatch.setattr(courses_mod, "all_courses", lambda: {"fake": fake})
    page = (
        "<!doctype html><html><head><title>t</title></head><body>"
        "<script>const example = `</body>`;</script>"
        "<p>real content</p></body></html>"
    )
    monkeypatch.setattr(courses_mod, "course_html", lambda c: page)

    html_text = client.get("/learn/fake/").get_data(as_text=True)
    widget_at = html_text.index("support-widget.js")
    # Widget must land after the script block, before the real </body></html>.
    assert widget_at > html_text.index("</script>")
    assert html_text.index("real content") < widget_at
    assert html_text.rstrip().endswith("</body></html>")


def test_api_requires_login(client):
    assert client.get("/api/progress/webdev").status_code == 401


def test_progress_reset_bumps_epoch(client):
    register(client)
    token = csrf(client)
    client.post("/api/progress/webdev",
                json={"state": json.dumps({"xp": 10})},
                headers={"X-CSRF-Token": token})

    r = client.post("/progress/webdev/reset", data={"csrf": token})
    assert r.status_code == 302

    j = client.get("/api/progress/webdev").get_json()
    assert j["state"] == "{}"
    assert j["reset_epoch"] == 1

    page = client.get("/my-courses").get_data(as_text=True)
    assert "Reset" not in page  # empty progress rows are hidden


def test_export_progress(client):
    register(client)
    token = csrf(client)
    client.post("/api/progress/webdev",
                json={"state": json.dumps({"xp": 33})},
                headers={"X-CSRF-Token": token})
    r = client.get("/api/progress/export")
    assert r.status_code == 200
    data = json.loads(r.get_data(as_text=True))
    assert data["courses"]["webdev"]["xp"] == 33


def test_export_progress_ignores_malformed_state(client):
    register(client)
    token = csrf(client)
    client.post("/api/progress/webdev",
                json={"state": json.dumps({"xp": 33})},
                headers={"X-CSRF-Token": token})
    with client.application.app_context():
        from sqlalchemy import select
        from models import Progress, User
        s = client.application.extensions["session_factory"]()
        user = s.execute(select(User).where(User.email == "a@b.com")).scalar_one()
        row = s.get(Progress, (user.id, "webdev"))
        row.state = '{not-json'
        s.commit()

    r = client.get("/api/progress/export")
    assert r.status_code == 200
    data = json.loads(r.get_data(as_text=True))
    assert data["courses"]["webdev"]["state"] == {}


def logout(client):
    token = csrf(client)
    return client.post("/logout", data={"csrf": token})


def test_password_reset_flow(client, monkeypatch):
    register(client)
    captured = {}
    import auth as auth_mod
    monkeypatch.setattr(auth_mod, "send_reset_link",
                        lambda user, link: captured.setdefault("link", link))

    token = csrf(client)
    client.post("/forgot", data={"csrf": token, "email": "a@b.com"})
    assert "link" in captured
    m = re.search(r"/reset/([^/?]+)", captured["link"])
    assert m

    token = csrf(client)
    r = client.post(f"/reset/{m.group(1)}", data={
        "csrf": token, "password": "newpassword9", "password2": "newpassword9",
    })
    assert r.status_code == 302

    logout(client)
    # old password no longer works (failed login re-renders the page: 200)
    assert login(client, pw="password123").status_code == 200
    # new password works (redirect: 302)
    assert login(client, pw="newpassword9").status_code == 302


def test_forgot_does_not_leave_valid_reset_token_when_delivery_fails(client, monkeypatch):
    import hashlib
    from sqlalchemy import select
    from models import PasswordReset, User

    register(client)
    monkeypatch.setattr("auth.send_reset_link", lambda user, link: False)
    token = csrf(client)
    client.post("/forgot", data={"csrf": token, "email": "a@b.com"})

    with client.application.app_context():
        s = client.application.extensions["session_factory"]()
        user = s.execute(select(User).where(User.email == "a@b.com")).scalar_one()
        rows = s.execute(select(PasswordReset).where(PasswordReset.user_id == user.id)).scalars().all()
        assert rows and all(r.used == 1 for r in rows)


def test_api_write_rate_limit(client):
    register(client)
    token = csrf(client)
    # Fill the hourly write budget directly, then the next POST is refused.
    with client.application.app_context():
        from sqlalchemy import select
        from models import LoginAttempt, User, now_ms
        s = client.application.extensions["session_factory"]()
        uid = s.execute(select(User.id)).scalar_one()
        for _ in range(600):
            s.add(LoginAttempt(key=f"api|{uid}", attempted_at=now_ms()))
        s.commit()
        s.close()
    r = client.post("/api/progress/webdev",
                    json={"state": "{}"},
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 429


def test_admin_requires_allowlist(client, monkeypatch):
    register(client)
    assert client.get("/admin").status_code == 404
    monkeypatch.setenv("ADMIN_EMAILS", "a@b.com")
    assert client.get("/admin").status_code == 200


def test_support_ticket_conversation_and_email_notifications(client, monkeypatch):
    import support as support_mod

    client.application.config["SUPPORT_EMAIL"] = "support@example.test"
    monkeypatch.setenv("ADMIN_EMAILS", "a@b.com")
    sent = []

    def deliver(user, subject, body, html_body=None, recipient_email=None):
        sent.append((user.email, subject, body, recipient_email))
        return True

    monkeypatch.setattr(support_mod, "_deliver", deliver)
    register(client)
    token = csrf(client)
    response = client.post("/support/new/python", data={
        "csrf": token,
        "subject": "Question about loops",
        "lesson": "For loops",
        "body": "Why does range stop before the end?",
    })
    assert response.status_code == 302
    ticket_url = response.headers["Location"]
    assert ticket_url.startswith("/support/")
    ticket_id = int(ticket_url.rsplit("/", 1)[1])
    assert sent[0][0] == "a@b.com"
    assert sent[0][3] == "support@example.test"
    assert "Why does range stop before the end?" in sent[0][2]

    thread = client.get(ticket_url).get_data(as_text=True)
    assert "Question about loops" in thread
    assert "Why does range stop before the end?" in thread
    ticket_list = client.get("/support").get_data(as_text=True)
    assert "Updated " in ticket_list

    response = client.get("/admin/tickets")
    assert response.status_code == 200
    assert f"#{ticket_id}" in response.get_data(as_text=True)

    response = client.post(f"/admin/tickets/{ticket_id}/reply", data={
        "csrf": csrf(client), "body": "The end value is exclusive.",
    })
    assert response.status_code == 302
    assert sent[-1][0] == "a@b.com"
    assert "Reply to your Raydex Learn ticket" in sent[-1][1]

    response = client.post(f"/admin/tickets/{ticket_id}/status", data={
        "csrf": csrf(client), "status": "closed",
    })
    assert response.status_code == 302
    closed_page = client.get(ticket_url).get_data(as_text=True)
    assert "Reopen ticket" in closed_page
    assert "The end value is exclusive." in closed_page

    response = client.post(f"/support/{ticket_id}/reopen", data={"csrf": csrf(client)})
    assert response.status_code == 302
    response = client.post(f"/support/{ticket_id}/reply", data={
        "csrf": csrf(client), "body": "Thanks, that helps.",
    })
    assert response.status_code == 302
    assert sent[-1][3] == "support@example.test"
    admin_thread = client.get(f"/admin/tickets/{ticket_id}").get_data(as_text=True)
    assert "Thanks, that helps." in admin_thread


def test_support_ticket_notification_failure_can_be_retried(client, monkeypatch):
    import support as support_mod

    client.application.config["SUPPORT_EMAIL"] = "support@example.test"
    monkeypatch.setenv("ADMIN_EMAILS", "a@b.com")
    delivered = {"ok": False}
    monkeypatch.setattr(support_mod, "_deliver",
                        lambda *args, **kwargs: delivered["ok"])
    register(client)

    response = client.post("/support/new/webdev", data={
        "csrf": csrf(client),
        "subject": "Question",
        "body": "I need some help.",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "support email could not be sent yet" in response.get_data(as_text=True)
    ticket_id = int(response.request.path.rsplit("/", 1)[1])

    delivered["ok"] = True
    response = client.post(f"/admin/tickets/{ticket_id}/retry-notification", data={
        "csrf": csrf(client),
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "Support notification sent." in response.get_data(as_text=True)


def test_support_ticket_is_private_to_its_learner(client, monkeypatch):
    import support as support_mod

    client.application.config["SUPPORT_EMAIL"] = "support@example.test"
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.test")
    monkeypatch.setattr(support_mod, "_deliver", lambda *args, **kwargs: True)
    register(client, email="first@example.test")
    response = client.post("/support/new/python", data={
        "csrf": csrf(client), "subject": "Private question", "body": "My question.",
    })
    ticket_id = int(response.headers["Location"].rsplit("/", 1)[1])

    logout(client)
    register(client, email="second@example.test")
    assert client.get(f"/support/{ticket_id}").status_code == 404
    assert client.get(f"/admin/tickets/{ticket_id}").status_code == 404


def test_login_throttle(client):
    register(client)
    logout(client)
    for _ in range(8):
        login(client, pw="wrong-password")
    r = login(client, pw="password123")
    # throttled: stays on login page instead of redirecting
    assert r.status_code == 200


def test_delete_account(client):
    register(client)
    # wrong password must not delete
    r = client.post("/account/delete", data={
        "csrf": csrf(client), "password": "wrongpass1"},
        follow_redirects=False)
    assert r.status_code == 302
    assert client.get("/dashboard").status_code == 200
    # right password deletes and logs out
    r = client.post("/account/delete", data={
        "csrf": csrf(client), "password": "password123"},
        follow_redirects=False)
    assert r.status_code == 302
    assert client.get("/dashboard").status_code == 302  # logged out


def test_email_verification(client):
    import hashlib
    from sqlalchemy import select
    from models import PasswordReset, User
    from db import get_session

    register(client)
    factory = client.application.extensions["session_factory"]
    with client.application.app_context():
        s = factory()
        u = s.execute(select(User)).scalar_one()
        assert u.verified == 0

        # craft a known token -> hash it into the table like _issue_token does
        token = "verify-test-token"
        th = hashlib.sha256(token.encode()).hexdigest()
        s.add(PasswordReset(user_id=u.id, token_hash=th,
                            purpose="verify",
                            expires_at=9999999999999))
        s.commit()

    r = client.get(f"/verify/{token}", follow_redirects=False)
    assert r.status_code == 302
    with client.application.app_context():
        s = factory()
        s.expire_all()
        assert s.get(User, u.id).verified == 1

    # token is single-use
    client.get(f"/verify/{token}", follow_redirects=False)


def test_registration_reports_unavailable_email_delivery(client):
    response = client.post("/register", data={
        "csrf": csrf(client),
        "name": "Delivery Test",
        "email": "delivery@example.com",
        "password": "password123",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "couldn&#39;t send the verification email" in response.get_data(as_text=True)
    dashboard = client.get("/dashboard").get_data(as_text=True)
    assert "you can request another" in dashboard


def test_password_reset_token_cannot_verify_email(client):
    import hashlib
    from sqlalchemy import select
    from models import PasswordReset, User

    register(client)
    factory = client.application.extensions["session_factory"]
    with client.application.app_context():
        s = factory()
        user = s.execute(select(User)).scalar_one()
        token = "reset-purpose-token"
        s.add(PasswordReset(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            purpose="reset",
            expires_at=9999999999999,
        ))
        s.commit()
        user_id = user.id

    client.get(f"/verify/{token}", follow_redirects=False)
    with client.application.app_context():
        s = factory()
        assert s.get(User, user_id).verified == 0


def test_smtp_delivery_uses_starttls_and_auth(client, monkeypatch):
    import auth as auth_mod
    from types import SimpleNamespace

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["connection"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context):
            sent["starttls"] = context

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "mailer")
    monkeypatch.setenv("SMTP_PASS", "local-test-secret")
    monkeypatch.setenv("MAIL_FROM", "learn@example.test")
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)

    assert auth_mod._deliver(
        SimpleNamespace(email="student@example.test"),
        "Test subject",
        "Test body",
        "<p>Test body</p>",
    )
    assert sent["connection"] == ("smtp.example.test", 587, 15)
    assert sent["login"] == ("mailer", "local-test-secret")
    assert sent["message"]["From"] == "Raydex Learn <learn@example.test>"
    assert sent["message"]["To"] == "student@example.test"
    assert sent["message"].is_multipart()
    assert sent["message"].get_body(preferencelist=("html",)).get_content_type() == "text/html"


def test_google_sign_in_creates_account_and_allows_password_setup(client):
    class FakeGoogleClient:
        def authorize_redirect(self, redirect_uri):
            from flask import redirect
            assert redirect_uri.endswith("/login/google/callback")
            return redirect("https://accounts.google.test/authorize")

        def authorize_access_token(self):
            return {"userinfo": {
                "sub": "google-user-123",
                "email": "google@example.com",
                "email_verified": True,
                "name": "Google User",
            }}

    class FakeOAuth:
        def create_client(self, name):
            assert name == "google"
            return FakeGoogleClient()

    client.application.extensions["google_oauth"] = FakeOAuth()

    response = client.get("/login/google?next=https://example.com")
    assert response.status_code == 302
    assert response.location == "https://accounts.google.test/authorize"
    response = client.get("/login/google/callback")
    assert response.status_code == 302
    assert response.location == "/"

    from sqlalchemy import select
    from models import OAuthIdentity, User

    factory = client.application.extensions["session_factory"]
    with client.application.app_context():
        s = factory()
        user = s.execute(select(User).where(User.email == "google@example.com")).scalar_one()
        assert user.verified == 1
        assert user.password_set == 0
        identity = s.execute(select(OAuthIdentity).where(
            OAuthIdentity.provider == "google",
            OAuthIdentity.subject == "google-user-123",
        )).scalar_one()
        assert identity.user_id == user.id

    settings = client.get("/settings").get_data(as_text=True)
    assert "Set a password here" in settings
    token = csrf(client)
    response = client.post("/settings", data={
        "csrf": token,
        "new_password": "google-password-123",
        "new_password2": "google-password-123",
    })
    assert response.status_code == 302

    logout(client)
    assert login(client, email="google@example.com", pw="google-password-123").status_code == 302


def test_google_sign_in_requires_configuration(client):
    response = client.get("/login/google", follow_redirects=True)
    assert response.status_code == 200
    assert "Google sign-in is not configured yet." in response.get_data(as_text=True)
    assert "Continue with Google" not in response.get_data(as_text=True)


def test_google_sign_in_links_existing_verified_email(client):
    register(client)

    class FakeGoogleClient:
        def authorize_access_token(self):
            return {"userinfo": {
                "sub": "google-user-existing",
                "email": "a@b.com",
                "email_verified": True,
                "name": "Ann",
            }}

    class FakeOAuth:
        def create_client(self, name):
            return FakeGoogleClient()

    client.application.extensions["google_oauth"] = FakeOAuth()
    response = client.get("/login/google/callback")
    assert response.status_code == 302
    assert response.location == "/"

    from sqlalchemy import select
    from models import OAuthIdentity, User

    factory = client.application.extensions["session_factory"]
    with client.application.app_context():
        s = factory()
        user = s.execute(select(User).where(User.email == "a@b.com")).scalar_one()
        assert user.password_set == 1
        assert user.verified == 1
        identity = s.execute(select(OAuthIdentity).where(
            OAuthIdentity.provider == "google",
            OAuthIdentity.subject == "google-user-existing",
        )).scalar_one()
        assert identity.user_id == user.id


def test_google_sign_in_rejects_unverified_email(client):
    class FakeGoogleClient:
        def authorize_access_token(self):
            return {"userinfo": {
                "sub": "google-user-unverified",
                "email": "unverified@example.com",
                "email_verified": False,
                "name": "Unverified",
            }}

    class FakeOAuth:
        def create_client(self, name):
            return FakeGoogleClient()

    client.application.extensions["google_oauth"] = FakeOAuth()
    response = client.get("/login/google/callback", follow_redirects=True)
    assert response.status_code == 200
    assert "Google did not provide a verified email address." in response.get_data(as_text=True)

    from sqlalchemy import select
    from models import User

    factory = client.application.extensions["session_factory"]
    with client.application.app_context():
        s = factory()
        assert s.execute(select(User).where(
            User.email == "unverified@example.com"
        )).scalar_one_or_none() is None


def test_existing_database_gets_password_set_default(tmp_path):
    import sqlite3
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255) NOT NULL, "
            "name VARCHAR(60) NOT NULL, pw_hash VARCHAR(255) NOT NULL, created_at INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO users (email, name, pw_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("legacy@example.com", "Legacy User", "existing-hash", 123),
        )

    app = create_app({
        "DATABASE_URL": "sqlite:///" + str(db).replace("\\", "/"),
        "SECRET_KEY": "test-secret",
        "TESTING": True,
    })
    from sqlalchemy import select
    from models import User

    with app.app_context():
        s = app.extensions["session_factory"]()
        user = s.execute(select(User).where(User.email == "legacy@example.com")).scalar_one()
        assert user.password_set == 1
        assert user.pw_hash == "existing-hash"


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_data(as_text=True) == "ok"


def test_gzip_large_html(client):
    r = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in r.headers.get("Vary", "")
    r2 = client.get("/")
    assert "Content-Encoding" not in r2.headers


def test_register_sends_welcome_email(client, monkeypatch):
    import auth as auth_mod
    sent = []
    monkeypatch.setattr(
        auth_mod, "_deliver",
        lambda user, subject, body, html_body=None, recipient_email=None:
            sent.append(subject) or True,
    )
    register(client)
    assert "Welcome to Raydex Learn" in sent


def test_password_change_invalidates_old_sessions(client):
    register(client)
    r = client.post("/settings", data={
        "csrf": csrf(client),
        "current_password": "password123",
        "new_password": "newpassword456",
        "new_password2": "newpassword456",
    })
    assert r.status_code == 302
    # The session that changed the password was bumped and stays valid.
    assert client.get("/dashboard").status_code == 200
    # A session issued before the change (older sver) is rejected.
    with client.session_transaction() as s:
        s["sver"] = 0
    assert client.get("/dashboard").status_code == 302


def test_progress_export_import_roundtrip(client):
    import io
    register(client)
    token = csrf(client)
    client.post("/api/progress/webdev",
                json={"state": json.dumps({"xp": 50, "done": {"0": True}}),
                      "done": 1},
                headers={"X-CSRF-Token": token})
    export = client.get("/api/progress/export").get_data()

    register(client, email="b@b.com", name="Bob")
    r = client.post("/progress/import", data={
        "csrf": csrf(client),
        "file": (io.BytesIO(export), "progress.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    j = client.get("/api/progress/webdev").get_json()
    assert json.loads(j["state"]) == {"xp": 50, "done": {"0": True}}


def test_course_completion_sends_email_once(client, monkeypatch):
    import progress as progress_mod
    sent = []
    monkeypatch.setattr(
        progress_mod, "send_course_completed_email",
        lambda *a, **k: sent.append(a) or True,
    )
    register(client)
    token = csrf(client)
    state = json.dumps({"xp": 999})
    for _ in range(2):
        r = client.post("/api/progress/webdev",
                        json={"state": state, "done": 70},
                        headers={"X-CSRF-Token": token})
        assert r.status_code == 200
    assert len(sent) == 1
    assert sent[0][1]["slug"] == "webdev"  # the course dict was passed


def test_certificate_public_verification(client):
    register(client)
    token = csrf(client)
    client.post("/api/progress/webdev",
                json={"state": json.dumps({"xp": 10}), "done": 70},
                headers={"X-CSRF-Token": token})
    page = client.get("/certificate/webdev").get_data(as_text=True)
    assert "Certificate of Completion" in page
    m = re.search(r'href="([^"]*certificate/webdev/verify/[^"]+)"', page)
    assert m, "certificate page should expose a public verification link"
    verify_url = m.group(1)

    # The link works without logging in.
    anon = client.application.test_client()
    r = anon.get(verify_url)
    assert r.status_code == 200
    assert "Verified authentic" in r.get_data(as_text=True)

    # A tampered signature is rejected.
    bad = verify_url[:-4] + ("0" if verify_url[-4] != "0" else "1") + verify_url[-3:]
    assert anon.get(bad).status_code == 404


def test_admin_user_actions(client, monkeypatch):
    import auth as auth_mod
    from sqlalchemy import select
    from models import User

    monkeypatch.setenv("ADMIN_EMAILS", "a@b.com")
    sent = []
    monkeypatch.setattr(
        auth_mod, "_deliver",
        lambda user, subject, body, html_body=None, recipient_email=None:
            sent.append(subject) or True,
    )
    register(client)
    with client.application.app_context():
        s = client.application.extensions["session_factory"]()
        uid = s.execute(select(User.id).where(User.email == "a@b.com")).scalar_one()

    # New accounts start unverified; admin can flip the flag.
    r = client.post(f"/admin/users/{uid}/verify", data={"csrf": csrf(client)})
    assert r.status_code == 302
    with client.application.app_context():
        s = client.application.extensions["session_factory"]()
        assert s.get(User, uid).verified == 1

    # Admin can email the user a password-reset link.
    r = client.post(f"/admin/users/{uid}/reset-link", data={"csrf": csrf(client)})
    assert r.status_code == 302
    assert any("Reset your Raydex Learn password" in s for s in sent)

    # Non-admins get a 404 on the same endpoints.
    logout(client)
    register(client, email="b@b.com", name="Bob")
    with client.application.app_context():
        s = client.application.extensions["session_factory"]()
        uid2 = s.execute(select(User.id).where(User.email == "b@b.com")).scalar_one()
    assert client.post(f"/admin/users/{uid2}/verify",
                       data={"csrf": csrf(client)}).status_code == 404


def test_closing_ticket_emails_learner(client, monkeypatch):
    import support as support_mod
    from sqlalchemy import select
    from models import SupportTicket, User

    monkeypatch.setenv("ADMIN_EMAILS", "a@b.com")
    client.application.config["SUPPORT_EMAIL"] = "support@example.test"
    sent = []
    monkeypatch.setattr(support_mod, "_deliver",
                        lambda *a, **k: sent.append(a) or True)
    register(client)
    client.post("/support/new", data={
        "csrf": csrf(client), "subject": "Question", "body": "Need help.",
    })
    with client.application.app_context():
        s = client.application.extensions["session_factory"]()
        tid = s.execute(select(SupportTicket.id)).scalar_one()

    r = client.post(f"/admin/tickets/{tid}/status",
                    data={"csrf": csrf(client), "status": "closed"})
    assert r.status_code == 302
    assert any("is closed" in a[1] for a in sent)


def test_admin_disable_account(client, monkeypatch):
    from sqlalchemy import select
    from models import User

    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    register(client, email="admin@example.com", name="Admin")
    logout(client)
    register(client, email="learner@example.com", name="Learner")

    with client.application.app_context():
        s = client.application.extensions["session_factory"]()
        aid = s.execute(select(User.id).where(User.email == "admin@example.com")).scalar_one()
        lid = s.execute(select(User.id).where(User.email == "learner@example.com")).scalar_one()

    # Learner has a live session.
    assert client.get("/progress").status_code == 200

    # Log in as admin and disable the learner.
    logout(client)
    login(client, email="admin@example.com")
    r = client.post(f"/admin/users/{lid}/disable", data={"csrf": csrf(client)})
    assert r.status_code == 302

    # The learner's old session is dead and they can't log back in.
    learner = client.application.test_client()
    r = login(learner, email="learner@example.com")
    assert b"disabled" in r.get_data().lower()
    assert learner.get("/progress").status_code == 302

    # Admin cannot disable their own account.
    r = client.post(f"/admin/users/{aid}/disable", data={"csrf": csrf(client)})
    assert r.status_code == 302
    with client.application.app_context():
        s = client.application.extensions["session_factory"]()
        assert s.get(User, aid).disabled == 0

    # Re-enable restores access.
    client.post(f"/admin/users/{lid}/disable", data={"csrf": csrf(client)})
    login(learner, email="learner@example.com")
    assert learner.get("/progress").status_code == 200
