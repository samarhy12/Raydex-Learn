"""Sign up, log in, log out, password reset, and shared auth helpers."""
import hashlib
import hmac
import html
import os
import re
import secrets
from functools import wraps

from authlib.common.errors import AuthlibBaseError
from authlib.integrations.flask_client import OAuth
from email.headerregistry import Address
from email.utils import parseaddr
from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)
from requests import RequestException
from sqlalchemy import delete, func, select
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_session
from models import LoginAttempt, OAuthIdentity, PasswordReset, User, now_ms

bp = Blueprint("auth", __name__)
oauth = OAuth()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESET_TTL_S = 30 * 60


def init_oauth(app):
    client_id = app.config.get("GOOGLE_CLIENT_ID")
    client_secret = app.config.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return
    google_oauth = OAuth()
    google_oauth.init_app(app)
    google_oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    app.extensions["google_oauth"] = google_oauth


def admin_emails():
    return {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    if "user" not in g:
        s = get_session()
        g.user = s.get(User, uid)
        # Drop sessions for deleted/disabled accounts or stale password changes.
        if (g.user is None or g.user.disabled
                or (g.user.session_version or 0) != session.get("sver", 0)):
            session.clear()
            g.user = None
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            return redirect(url_for("auth.login", next=request.path))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        u = current_user()
        if not u or u.email.lower() not in admin_emails():
            abort(404)
        return view(*a, **kw)
    return wrapped


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def check_csrf(token):
    return bool(token) and secrets.compare_digest(token, session.get("csrf", ""))


def safe_next(target):
    """Only allow same-site relative redirects."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("index")


# ------------------------------------------------------------- throttling
def _prune(s, cutoff):
    s.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff))


def too_many_attempts(key, limit, window_s):
    """True if `key` already has `limit` attempts inside the window."""
    s = get_session()
    now = now_ms()
    cutoff = now - window_s * 1000
    _prune(s, cutoff)
    count = s.execute(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.key == key, LoginAttempt.attempted_at >= cutoff
        )
    ).scalar_one()
    s.commit()
    return count >= limit


def record_attempt(key):
    s = get_session()
    s.add(LoginAttempt(key=key, attempted_at=now_ms()))
    s.commit()


def clear_attempts(key):
    s = get_session()
    s.execute(delete(LoginAttempt).where(LoginAttempt.key == key))
    s.commit()


# ------------------------------------------------------------------ auth
def _start_session(user):
    session.clear()
    session.permanent = True
    session["uid"] = user.id
    session["sver"] = user.session_version or 0


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("index"))
    nxt = request.args.get("next") or request.form.get("next") or ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:60]
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        key = f"reg|{request.remote_addr}"
        error = None
        if too_many_attempts(key, 10, 3600):
            error = "Too many sign-ups from this network. Try again later."
        elif not name:
            error = "Enter your name."
        elif not EMAIL_RE.match(email):
            error = "Enter a valid email address."
        elif len(pw) < 8:
            error = "Use a password with at least 8 characters."
        elif get_session().execute(select(User).where(User.email == email)).scalar_one_or_none():
            error = "That email already has an account. Log in instead."
        if error:
            flash(error)
        else:
            record_attempt(key)
            user = User(email=email, name=name, pw_hash=generate_password_hash(pw),
                        password_set=1, verified=0)
            s = get_session()
            s.add(user)
            s.commit()
            _start_session(user)
            send_welcome_email(user)
            if send_verify_link(user):
                flash("Your account is ready. Check your inbox for the email verification link.")
            else:
                flash("Your account is ready, but we couldn't send the verification email. Please try again later or contact support.")
            return redirect(safe_next(nxt))
    return render_template("register.html", next=nxt)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))
    nxt = request.args.get("next") or request.form.get("next") or ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        key = f"login|{request.remote_addr}|{email}"
        if too_many_attempts(key, 8, 600):
            flash("Too many tries. Wait a few minutes and try again.")
        else:
            s = get_session()
            row = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if row and row.disabled:
                flash("This account has been disabled. Contact support if you think this is a mistake.")
            elif row and row.password_set and check_password_hash(row.pw_hash, pw):
                clear_attempts(key)
                _start_session(row)
                return redirect(safe_next(nxt))
            else:
                record_attempt(key)
                flash("Email or password is wrong.")
    return render_template("login.html", next=nxt)


@bp.route("/login/google")
def google_login():
    google = current_app.extensions.get("google_oauth")
    if not google:
        flash("Google sign-in is not configured yet.")
        return redirect(url_for("auth.login"))
    session["google_oauth_next"] = safe_next(request.args.get("next", ""))
    redirect_uri = url_for("auth.google_callback", _external=True)
    try:
        return google.create_client("google").authorize_redirect(redirect_uri)
    except AuthlibBaseError as exc:
        current_app.logger.warning("Could not start Google sign-in: %s", exc)
    except RequestException:
        current_app.logger.exception("Could not reach Google to start sign-in")
    session.pop("google_oauth_next", None)
    flash("Google sign-in is temporarily unavailable. Please try again.")
    return redirect(url_for("auth.login"))


@bp.route("/login/google/callback")
def google_callback():
    google = current_app.extensions.get("google_oauth")
    if not google:
        flash("Google sign-in is not configured yet.")
        return redirect(url_for("auth.login"))

    try:
        token = google.create_client("google").authorize_access_token()
    except AuthlibBaseError as exc:
        current_app.logger.warning("Google sign-in was not completed: %s", exc)
        session.pop("google_oauth_next", None)
        flash("Google sign-in was not completed. Please try again.")
        return redirect(url_for("auth.login"))
    except RequestException:
        current_app.logger.exception("Could not reach Google to complete sign-in")
        session.pop("google_oauth_next", None)
        flash("Google sign-in is temporarily unavailable. Please try again.")
        return redirect(url_for("auth.login"))

    userinfo = token.get("userinfo") or {}
    email = str(userinfo.get("email", "")).strip().lower()
    subject = str(userinfo.get("sub", "")).strip()
    name = str(userinfo.get("name", "")).strip()[:60]
    if (not subject or len(subject) > 255 or not EMAIL_RE.match(email)
            or userinfo.get("email_verified") is not True):
        current_app.logger.warning("Google sign-in returned incomplete or unverified account details")
        session.pop("google_oauth_next", None)
        flash("Google did not provide a verified email address. Please try another sign-in method.")
        return redirect(url_for("auth.login"))

    s = get_session()
    new_user = False
    identity = s.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == "google",
            OAuthIdentity.subject == subject,
        )
    ).scalar_one_or_none()
    if identity:
        user = s.get(User, identity.user_id)
        if not user:
            current_app.logger.error("Google identity %s has no linked user", subject)
            abort(500)
        if user.disabled:
            flash("This account has been disabled. Contact support if you think this is a mistake.")
            return redirect(url_for("auth.login"))
    else:
        user = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user and user.disabled:
            flash("This account has been disabled. Contact support if you think this is a mistake.")
            return redirect(url_for("auth.login"))
        new_user = user is None
        if not user:
            user = User(
                email=email,
                name=name or email.split("@", 1)[0][:60],
                pw_hash=generate_password_hash(secrets.token_urlsafe(48)),
                password_set=0,
                verified=1,
            )
            s.add(user)
            s.flush()
        else:
            user.verified = 1
        s.add(OAuthIdentity(provider="google", subject=subject, user_id=user.id))
        s.commit()

    nxt = session.pop("google_oauth_next", "")
    _start_session(user)
    if new_user:
        send_welcome_email(user)
    return redirect(safe_next(nxt))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


# ------------------------------------------------------- password reset
def _deliver(user, subject, body, html_body=None, recipient_email=None):
    """Send a branded plain-text and optional HTML email over configured SMTP."""
    import logging
    import smtplib
    import ssl
    from email.message import EmailMessage

    log = logging.getLogger("raydex.auth")
    host = os.environ.get("SMTP_HOST")
    recipient = recipient_email or user.email
    if not host:
        log.warning("SMTP is not configured; could not send %s to %s", subject, recipient)
        return False
    msg = EmailMessage()
    _, sender_email = parseaddr(os.environ.get("MAIL_FROM", "no-reply@raydexlearn.com"))
    if not sender_email:
        log.error("MAIL_FROM must contain a valid sender email address")
        return False
    msg["From"] = str(Address(display_name="Raydex Learn", addr_spec=sender_email))
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user, smtp_pass = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
        if bool(smtp_user) != bool(smtp_pass):
            log.error("SMTP_USER and SMTP_PASS must either both be set or both be empty")
            return False
        if port == 465:
            smtp_connection = smtplib.SMTP_SSL(
                host, port, timeout=15, context=ssl.create_default_context()
            )
        else:
            smtp_connection = smtplib.SMTP(host, port, timeout=15)
        with smtp_connection as smtp:
            if port != 465:
                smtp.starttls(context=ssl.create_default_context())
            if smtp_user:
                smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException, ValueError):
        log.exception("Failed to send email to %s", recipient)
        return False


def _email_template(name, title, intro, action_label, link, note):
    """Render a branded HTML email. All caller-supplied text is escaped."""
    greeting = html.escape((name or "").strip() or "there")
    escaped_title = html.escape(title)
    escaped_intro = html.escape(intro).replace("\n", "<br>")
    escaped_action = html.escape(action_label)
    escaped_link = html.escape(link, quote=True)
    escaped_note = html.escape(note).replace("\n", "<br>")
    preheader = html.escape(intro).replace("\n", " ")[:300]
    return f"""\
<!doctype html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>{escaped_title}</title>
</head>
<body style="margin:0;padding:0;background-color:#edf0ea">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all">{preheader}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#edf0ea;padding:40px 16px">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background-color:#ffffff;border:1px solid #e2e7df;border-radius:16px;overflow:hidden">
        <tr><td style="background-color:#1d2b25;padding:24px 32px 20px">
          <div style="font-size:20px;line-height:26px;font-weight:700;color:#ffffff">Raydex <span style="color:#ffc870">Learn</span></div>
          <div style="font-size:12px;line-height:18px;color:#b7c4ba;margin-top:4px">Practical lessons. Progress that stays with you.</div>
        </td></tr>
        <tr><td style="height:4px;line-height:4px;font-size:4px;background-color:#ffc870">&nbsp;</td></tr>
        <tr><td style="padding:34px 32px 30px">
          <h1 style="font-size:22px;line-height:30px;color:#1d2b25;margin:0 0 14px">{escaped_title}</h1>
          <p style="font-size:16px;line-height:26px;color:#202721;margin:0 0 12px">Hi {greeting},</p>
          <p style="font-size:15px;line-height:24px;color:#55605a;margin:0 0 28px">{escaped_intro}</p>
          <table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 0 26px">
            <tr><td bgcolor="#315c50" style="border-radius:10px">
              <a href="{escaped_link}" style="display:inline-block;padding:14px 26px;color:#ffffff;text-decoration:none;font-size:15px;line-height:20px;font-weight:700">{escaped_action}</a>
            </td></tr>
          </table>
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 24px">
            <tr><td style="background-color:#f5f7f2;border-left:3px solid #315c50;border-radius:0 8px 8px 0;padding:14px 18px">
              <p style="font-size:13px;line-height:21px;color:#4b5750;margin:0">{escaped_note}</p>
            </td></tr>
          </table>
          <p style="font-size:12px;line-height:20px;color:#7a847d;margin:0">If the button doesn’t work, paste this link into your browser:<br>
            <a href="{escaped_link}" style="color:#315c50;word-break:break-all">{escaped_link}</a>
          </p>
        </td></tr>
        <tr><td style="border-top:1px solid #e8ece7;padding:18px 32px">
          <p style="font-size:12px;line-height:19px;color:#88918a;margin:0">Raydex Learn · Practical lessons. Progress that stays with you.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _issue_token(user, purpose="reset"):
    """Create a single-use token (stored hashed) and return the raw token."""
    token = secrets.token_urlsafe(32)
    s = get_session()
    s.add(PasswordReset(
        user_id=user.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        purpose=purpose,
        expires_at=now_ms() + RESET_TTL_S * 1000,
    ))
    s.commit()
    return token


def send_reset_link(user, link):
    return _deliver(
        user,
        "Reset your Raydex Learn password",
        f"Hi {user.name},\n\n"
        "We received a request to reset the password for your Raydex Learn account.\n\n"
        f"Reset your password:\n{link}\n\n"
        "This link expires in 30 minutes and can only be used once.\n\n"
        "If you didn't request a password reset, you can safely ignore this email — "
        "your password won't change.\n\n"
        "— The Raydex Learn team\n",
        _email_template(
            user.name,
            "Reset your password",
            "We received a request to reset the password for your Raydex Learn account.",
            "Choose a new password",
            link,
            "This link expires in 30 minutes and can only be used once.\n"
            "If you didn’t request a password reset, you can safely ignore this email — "
            "your password won’t change.",
        ),
    )


def send_welcome_email(user):
    link = url_for("student.dashboard", _external=True)
    return _deliver(
        user,
        "Welcome to Raydex Learn",
        f"Hi {user.name},\n\n"
        "Your Raydex Learn account is ready. Pick a course, learn at your own "
        "pace, and your progress syncs across every device you sign in on.\n\n"
        f"Open your dashboard: {link}\n\n"
        "If you didn't create this account, you can ignore this email.\n\n"
        "— The Raydex Learn team\n",
        _email_template(
            user.name,
            "Welcome to Raydex Learn",
            "Your account is ready. Pick a course, learn at your own pace, and "
            "your progress syncs across every device you sign in on.",
            "Open your dashboard",
            link,
            "If you didn’t create this account, you can safely ignore this email.",
        ),
    )


def send_password_changed_email(user):
    link = url_for("student.settings", _external=True)
    return _deliver(
        user,
        "Your Raydex Learn password was changed",
        f"Hi {user.name},\n\n"
        "The password for your Raydex Learn account was just changed.\n\n"
        f"If this was you, no action is needed. If it wasn't, reset your "
        f"password right away: {link}\n\n"
        "— The Raydex Learn team\n",
        _email_template(
            user.name,
            "Password changed",
            "The password for your Raydex Learn account was just changed.",
            "Review account security",
            link,
            "If this was you, no action is needed. If it wasn’t, reset your "
            "password right away — other signed-in sessions were signed out.",
        ),
    )


def cert_signature(user_id, slug):
    """Unforgeable signature for a public certificate-verification link."""
    return hmac.new(
        current_app.secret_key.encode(),
        f"cert|{user_id}|{slug}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def cert_verify_url(user, slug):
    return url_for(
        "student.certificate_verify", slug=slug, uid=user.id,
        sig=cert_signature(user.id, slug), _external=True,
    )


def send_course_completed_email(user, course, done, xp):
    link = cert_verify_url(user, course["slug"])
    return _deliver(
        user,
        f"Course complete: {course['title']}",
        f"Hi {user.name},\n\n"
        f"Congratulations — you finished every lesson in {course['title']}!\n\n"
        f"Your certificate is ready: {link}\n\n"
        f"That's {done} lessons and {xp} XP earned. What's next?\n\n"
        "— The Raydex Learn team\n",
        _email_template(
            user.name,
            f"Course complete: {course['title']}",
            "Congratulations — you finished every lesson. Your certificate is "
            "ready to view and print.",
            "View your certificate",
            link,
            f"{done} lessons completed · {xp} XP earned",
        ),
    )


def send_verify_link(user):
    token = _issue_token(user, purpose="verify")
    link = url_for("auth.verify", token=token, _external=True)
    delivered = _deliver(
        user,
        "Verify your Raydex Learn email",
        f"Hi {user.name},\n\n"
        "Thanks for joining Raydex Learn! Please confirm this email address to finish "
        "setting up your account.\n\n"
        f"Verify your email:\n{link}\n\n"
        "This one-time link expires in 30 minutes.\n\n"
        "If you didn’t create a Raydex Learn account, you can safely ignore this email.\n\n"
        "— The Raydex Learn team\n",
        _email_template(
            user.name,
            "Confirm your email",
            "Thanks for joining Raydex Learn. Confirm your email address to finish setting up your account.",
            "Verify email address",
            link,
            "This one-time link expires in 30 minutes.\n"
            "If you didn’t create a Raydex Learn account, you can safely ignore this email.",
        ),
    )
    if not delivered:
        _invalidate_token(token)
    return delivered


def _invalidate_token(token):
    s = get_session()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = s.execute(
        select(PasswordReset).where(PasswordReset.token_hash == token_hash)
    ).scalar_one_or_none()
    if row:
        row.used = 1
        s.commit()


# ------------------------------------------------------ email verification
@bp.route("/verify/<token>")
def verify(token):
    row = _valid_reset(token, purpose="verify")
    if not row:
        flash("That verification link is invalid or has expired. Request a new one.")
        return redirect(url_for("student.dashboard") if current_user() else url_for("index"))
    s = get_session()
    user = s.get(User, row.user_id)
    if user:
        user.verified = 1
    row.used = 1
    s.commit()
    flash("Email verified. Thanks for confirming your address.")
    return redirect(url_for("student.dashboard") if current_user() else url_for("index"))


@bp.route("/verify/resend", methods=["POST"])
def verify_resend():
    u = current_user()
    if not u:
        return redirect(url_for("auth.login"))
    if u.verified:
        flash("Your email is already verified.")
        return redirect(url_for("student.dashboard"))
    key = f"verify|{u.id}"
    if too_many_attempts(key, 3, 3600):
        flash("Too many verification emails. Try again later.")
    else:
        record_attempt(key)
        if send_verify_link(u):
            flash("Verification email sent — check your inbox.")
        else:
            flash("We couldn't send the verification email. Please try again later or contact support.")
    return redirect(url_for("student.dashboard"))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        key = f"forgot|{request.remote_addr}"
        if not too_many_attempts(key, 5, 3600):
            record_attempt(key)
            s = get_session()
            user = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if user:
                token = _issue_token(user)
                link = url_for("auth.reset", token=token, _external=True)
                if not send_reset_link(user, link):
                    _invalidate_token(token)
        # Same message either way so the endpoint does not reveal accounts.
        flash("If that email has an account, a reset link is on its way.")
        return redirect(url_for("auth.login"))
    return render_template("forgot.html")


def _valid_reset(token, purpose="reset"):
    if not token or len(token) > 200:
        return None
    s = get_session()
    th = hashlib.sha256(token.encode()).hexdigest()
    row = s.execute(
        select(PasswordReset).where(
            PasswordReset.token_hash == th,
            PasswordReset.purpose == purpose,
            PasswordReset.used == 0,
            PasswordReset.expires_at > now_ms(),
        )
    ).scalar_one_or_none()
    return row


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    row = _valid_reset(token)
    if not row:
        flash("That reset link is invalid or has expired. Request a new one.")
        return redirect(url_for("auth.forgot"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if len(pw) < 8:
            flash("Use a password with at least 8 characters.")
        elif pw != pw2:
            flash("The two passwords do not match.")
        else:
            s = get_session()
            user = s.get(User, row.user_id)
            user.pw_hash = generate_password_hash(pw)
            user.password_set = 1
            user.session_version = (user.session_version or 0) + 1
            row.used = 1
            s.commit()
            send_password_changed_email(user)
            flash("Password updated. Log in with your new password.")
            return redirect(url_for("auth.login"))
    return render_template("reset.html", token=token)
