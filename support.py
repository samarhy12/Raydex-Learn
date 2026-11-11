"""Learner support tickets and admin replies."""
import time

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from sqlalchemy import select

from auth import (_deliver, _email_template, admin_required, check_csrf,
                  current_user, login_required, record_attempt,
                  too_many_attempts)
from courses import all_courses
from db import get_session
from models import SupportMessage, SupportTicket, User, now_ms

bp = Blueprint("support", __name__)

STATUSES = {
    "open": "Open",
    "waiting_for_learner": "Waiting for learner",
    "closed": "Closed",
}
MAX_SUBJECT_LENGTH = 160
MAX_LESSON_LENGTH = 160
MAX_MESSAGE_LENGTH = 5000


@bp.after_request
def prevent_ticket_caching(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _ticket_url(ticket, endpoint):
    return url_for(endpoint, ticket_id=ticket.id, _external=True)


def _time_label(timestamp):
    return time.strftime("%d %b %Y, %H:%M UTC", time.gmtime(timestamp / 1000))


def _send_admin_notification(ticket, body, subject_prefix):
    recipient = current_app.config.get("SUPPORT_EMAIL", "").strip()
    if not recipient:
        current_app.logger.warning("SUPPORT_EMAIL is not configured; ticket %s was not emailed", ticket.id)
        return False

    s = get_session()
    user = s.get(User, ticket.user_id)
    if not user:
        current_app.logger.error("Support ticket %s has no learner account", ticket.id)
        return False
    link = _ticket_url(ticket, "support.admin_ticket")
    lesson_line = f"\nLesson or topic: {ticket.lesson}" if ticket.lesson else ""
    details = (
        f"From: {user.name} <{user.email}>\n"
        f"Course: {ticket.course_title}{lesson_line}\n"
        f"Subject: {ticket.subject}"
    )
    plain = (
        f"Support activity on ticket #{ticket.id}\n\n"
        f"{details}\n\n"
        f"{body}\n\n"
        f"Open the ticket: {link}\n"
    )
    return _deliver(
        user, f"[Raydex Learn #{ticket.id}] {subject_prefix}: {ticket.subject}",
        plain,
        _email_template(
            "Raydex Learn team",
            f"Ticket #{ticket.id}: {subject_prefix}",
            f"{user.name} sent a message on a support ticket.\n\n{details}",
            "Open ticket",
            link,
            body,
        ),
        recipient_email=recipient,
    )


def _send_learner_notification(ticket, message):
    s = get_session()
    user = s.get(User, ticket.user_id)
    if not user:
        current_app.logger.error("Support ticket %s has no learner account", ticket.id)
        return False
    link = _ticket_url(ticket, "support.ticket")
    plain = (
        f"Hi {user.name},\n\n"
        "There's a new reply on your Raydex Learn support ticket "
        f"#{ticket.id} ({ticket.subject}).\n\n"
        f"{message.body}\n\n"
        f"View the conversation: {link}\n\n"
        "— The Raydex Learn team\n"
    )
    return _deliver(
        user, f"Reply to your Raydex Learn ticket #{ticket.id}",
        plain,
        _email_template(
            user.name,
            f"New reply on ticket #{ticket.id}",
            f"The Raydex Learn team replied to your support ticket:\n\n"
            f"{ticket.subject}",
            "View the conversation",
            link,
            message.body,
        ),
    )


def _send_ticket_closed_notification(ticket):
    s = get_session()
    user = s.get(User, ticket.user_id)
    if not user:
        current_app.logger.error("Support ticket %s has no learner account", ticket.id)
        return False
    link = _ticket_url(ticket, "support.ticket")
    plain = (
        f"Hi {user.name},\n\n"
        "Your Raydex Learn support ticket has been closed.\n\n"
        f"Ticket #{ticket.id}: {ticket.subject}\n\n"
        "If you still need help, you can reply to the ticket or open a new one "
        "from the support page.\n\n"
        f"View the conversation: {link}\n\n"
        "— The Raydex Learn team\n"
    )
    return _deliver(
        user, f"Your Raydex Learn ticket #{ticket.id} is closed",
        plain,
        _email_template(
            user.name,
            f"Ticket #{ticket.id} closed",
            "Your support ticket has been marked as closed by the Raydex Learn team.\n\n"
            f"{ticket.subject}",
            "View the conversation",
            link,
            "If you still need help, reply to this ticket or open a new one from "
            "the support page — just sign in and visit Support.",
        ),
    )


def _format_thread(ticket):
    s = get_session()
    messages = s.execute(
        select(SupportMessage)
        .where(SupportMessage.ticket_id == ticket.id)
        .order_by(SupportMessage.created_at, SupportMessage.id)
    ).scalars().all()
    first_message_id = messages[0].id if messages else None
    for message in messages:
        message.created_label = _time_label(message.created_at)
        message.notification_pending = (
            not message.notification_sent
            and (message.sender_type == "admin" or message.id != first_message_id)
        )
    ticket.created_label = _time_label(ticket.created_at)
    return messages


def _return_to_course(slug):
    if slug:
        return redirect(url_for("course_detail", slug=slug) + "#support-launcher")
    return redirect(url_for("index") + "#support-launcher")


@bp.route("/support")
@login_required
def tickets():
    u = current_user()
    rows = get_session().execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == u.id)
        .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
    ).scalars().all()
    for ticket_row in rows:
        ticket_row.updated_label = _time_label(ticket_row.updated_at)
    return render_template("support/tickets.html", tickets=rows, statuses=STATUSES)


@bp.route("/support/<int:ticket_id>")
@login_required
def ticket(ticket_id):
    u = current_user()
    row = get_session().get(SupportTicket, ticket_id)
    if row is None or row.user_id != u.id:
        abort(404)
    return render_template(
        "support/ticket.html", ticket=row, messages=_format_thread(row),
        statuses=STATUSES, is_admin=False,
    )


@bp.route("/support/new", defaults={"slug": None}, methods=["POST"])
@bp.route("/support/new/<slug>", methods=["POST"])
def create_ticket(slug=None):
    u = current_user()
    if not u:
        next_url = (
            url_for("course_detail", slug=slug) + "#support-launcher"
            if slug else url_for("index") + "#support-launcher"
        )
        return redirect(url_for("auth.login", next=next_url))
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")

    return_slug = slug
    courses = all_courses()
    if slug:
        course = courses.get(slug)
        if not course:
            abort(404)
    else:
        selected_slug = request.form.get("course", "").strip()
        if selected_slug and selected_slug not in courses:
            abort(400, "Choose a course from the list.")
        slug = selected_slug or "general"
        course = courses.get(slug, {"title": "General question"})

    key = f"ticket-new|{u.id}"
    if too_many_attempts(key, 5, 3600):
        flash("Too many support requests. Please try again later.", "err")
        return _return_to_course(return_slug)

    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    lesson = request.form.get("lesson", "").strip()[:MAX_LESSON_LENGTH]
    has_control_chars = any(ord(char) < 32 or ord(char) == 127 for char in subject)
    if not subject or len(subject) > MAX_SUBJECT_LENGTH or has_control_chars:
        flash(f"Enter a subject of 1 to {MAX_SUBJECT_LENGTH} characters.", "err")
        return _return_to_course(return_slug)
    if not body or len(body) > MAX_MESSAGE_LENGTH:
        flash(f"Enter a question of 1 to {MAX_MESSAGE_LENGTH} characters.", "err")
        return _return_to_course(return_slug)

    record_attempt(key)
    s = get_session()
    ticket_row = SupportTicket(
        user_id=u.id,
        course_slug=slug,
        course_title=course["title"],
        lesson=lesson,
        subject=subject,
        status="open",
        created_at=now_ms(),
        updated_at=now_ms(),
    )
    s.add(ticket_row)
    s.flush()
    message = SupportMessage(
        ticket_id=ticket_row.id, sender_type="learner",
        sender_name=u.name, body=body, created_at=now_ms(),
    )
    s.add(message)
    s.commit()
    ticket_row.admin_notified = int(_send_admin_notification(ticket_row, body, "New question"))
    s.commit()
    if ticket_row.admin_notified:
        flash("Your question was sent. You can follow the conversation here.", "ok")
    else:
        flash("Your question was saved, but the support email could not be sent yet. The admin can retry it.", "err")
    return redirect(url_for("support.ticket", ticket_id=ticket_row.id))


@bp.route("/support/<int:ticket_id>/reply", methods=["POST"])
@login_required
def learner_reply(ticket_id):
    u = current_user()
    s = get_session()
    ticket_row = s.get(SupportTicket, ticket_id)
    if ticket_row is None or ticket_row.user_id != u.id:
        abort(404)
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    if ticket_row.status == "closed":
        flash("Reopen this ticket before replying.", "err")
        return redirect(url_for("support.ticket", ticket_id=ticket_id))
    key = f"ticket-reply|{u.id}"
    if too_many_attempts(key, 20, 3600):
        flash("Too many replies. Please try again later.", "err")
        return redirect(url_for("support.ticket", ticket_id=ticket_id))
    body = request.form.get("body", "").strip()
    if not body or len(body) > MAX_MESSAGE_LENGTH:
        flash(f"Enter a reply of 1 to {MAX_MESSAGE_LENGTH} characters.", "err")
        return redirect(url_for("support.ticket", ticket_id=ticket_id))

    record_attempt(key)
    message = SupportMessage(
        ticket_id=ticket_id, sender_type="learner",
        sender_name=u.name, body=body, created_at=now_ms(),
    )
    s.add(message)
    ticket_row.status = "open"
    ticket_row.closed_at = None
    ticket_row.updated_at = now_ms()
    s.flush()
    message.notification_sent = int(_send_admin_notification(ticket_row, body, "Learner replied"))
    s.commit()
    if not message.notification_sent:
        flash("Your reply was saved, but the support notification could not be sent yet.", "err")
    else:
        flash("Your reply was sent.", "ok")
    return redirect(url_for("support.ticket", ticket_id=ticket_id))


@bp.route("/support/<int:ticket_id>/reopen", methods=["POST"])
@login_required
def reopen_ticket(ticket_id):
    u = current_user()
    ticket_row = get_session().get(SupportTicket, ticket_id)
    if ticket_row is None or ticket_row.user_id != u.id:
        abort(404)
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    key = f"ticket-reopen|{u.id}"
    if too_many_attempts(key, 30, 3600):
        flash("Too many requests. Please try again later.", "err")
        return redirect(url_for("support.ticket", ticket_id=ticket_id))
    record_attempt(key)
    if ticket_row.status == "closed":
        ticket_row.status = "open"
        ticket_row.closed_at = None
        ticket_row.updated_at = now_ms()
        get_session().commit()
        flash("Ticket reopened.", "ok")
    return redirect(url_for("support.ticket", ticket_id=ticket_id))


@bp.route("/admin/tickets")
@admin_required
def admin_tickets():
    s = get_session()
    rows = s.execute(
        select(SupportTicket, User)
        .join(User, SupportTicket.user_id == User.id)
        .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
    ).all()
    for ticket_row, _learner in rows:
        ticket_row.updated_label = _time_label(ticket_row.updated_at)
    return render_template(
        "support/admin_tickets.html", rows=rows, statuses=STATUSES,
        support_email=current_app.config.get("SUPPORT_EMAIL", "").strip(),
    )


@bp.route("/admin/tickets/<int:ticket_id>")
@admin_required
def admin_ticket(ticket_id):
    s = get_session()
    ticket_row = s.get(SupportTicket, ticket_id)
    if ticket_row is None:
        abort(404)
    user = s.get(User, ticket_row.user_id)
    return render_template(
        "support/ticket.html", ticket=ticket_row, messages=_format_thread(ticket_row),
        user=user, statuses=STATUSES, is_admin=True,
    )


@bp.route("/admin/tickets/<int:ticket_id>/reply", methods=["POST"])
@admin_required
def admin_reply(ticket_id):
    s = get_session()
    ticket_row = s.get(SupportTicket, ticket_id)
    if ticket_row is None:
        abort(404)
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    if ticket_row.status == "closed":
        flash("Reopen the ticket before replying.", "err")
        return redirect(url_for("support.admin_ticket", ticket_id=ticket_id))
    body = request.form.get("body", "").strip()
    if not body or len(body) > MAX_MESSAGE_LENGTH:
        flash(f"Enter a reply of 1 to {MAX_MESSAGE_LENGTH} characters.", "err")
        return redirect(url_for("support.admin_ticket", ticket_id=ticket_id))

    u = current_user()
    message = SupportMessage(
        ticket_id=ticket_id, sender_type="admin",
        sender_name=u.name, body=body, created_at=now_ms(),
    )
    s.add(message)
    ticket_row.status = "waiting_for_learner"
    ticket_row.closed_at = None
    ticket_row.updated_at = now_ms()
    s.flush()
    message.notification_sent = int(_send_learner_notification(ticket_row, message))
    s.commit()
    if message.notification_sent:
        flash("Your reply was sent to the learner.", "ok")
    else:
        flash("Your reply was saved, but the learner notification could not be sent yet.", "err")
    return redirect(url_for("support.admin_ticket", ticket_id=ticket_id))


@bp.route("/admin/tickets/<int:ticket_id>/status", methods=["POST"])
@admin_required
def admin_status(ticket_id):
    s = get_session()
    ticket_row = s.get(SupportTicket, ticket_id)
    if ticket_row is None:
        abort(404)
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    status = request.form.get("status", "")
    if status not in STATUSES:
        abort(400, "Unknown ticket status.")
    was_closed = ticket_row.status == "closed"
    ticket_row.status = status
    ticket_row.closed_at = now_ms() if status == "closed" else None
    ticket_row.updated_at = now_ms()
    s.commit()
    flash(f"Ticket status set to {STATUSES[status].lower()}.", "ok")
    if status == "closed" and not was_closed:
        if _send_ticket_closed_notification(ticket_row):
            flash("The learner was emailed that the ticket is closed.", "ok")
        else:
            flash("The learner could not be emailed about the closure. Check SMTP settings.", "err")
    return redirect(url_for("support.admin_ticket", ticket_id=ticket_id))


@bp.route("/admin/tickets/<int:ticket_id>/retry-notification", methods=["POST"])
@admin_required
def retry_ticket_notification(ticket_id):
    s = get_session()
    ticket_row = s.get(SupportTicket, ticket_id)
    if ticket_row is None:
        abort(404)
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    if ticket_row.admin_notified:
        flash("The support notification was already sent.", "ok")
    else:
        first_message = s.execute(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket_id, SupportMessage.sender_type == "learner")
            .order_by(SupportMessage.created_at, SupportMessage.id)
        ).scalars().first()
        sent = bool(first_message and _send_admin_notification(
            ticket_row, first_message.body, "New question"
        ))
        ticket_row.admin_notified = int(sent)
        s.commit()
        flash("Support notification sent." if sent else "Support notification could not be sent. Check SMTP and SUPPORT_EMAIL.", "ok" if sent else "err")
    return redirect(url_for("support.admin_ticket", ticket_id=ticket_id))


@bp.route("/admin/tickets/<int:ticket_id>/messages/<int:message_id>/retry-notification", methods=["POST"])
@admin_required
def retry_message_notification(ticket_id, message_id):
    s = get_session()
    ticket_row = s.get(SupportTicket, ticket_id)
    message = s.get(SupportMessage, message_id)
    if ticket_row is None or message is None or message.ticket_id != ticket_id:
        abort(404)
    if not check_csrf(request.form.get("csrf")):
        abort(400, "Your session expired. Go back, refresh the page and try again.")
    if message.notification_sent:
        flash("That notification was already sent.", "ok")
    else:
        if message.sender_type == "admin":
            sent = _send_learner_notification(ticket_row, message)
        else:
            sent = _send_admin_notification(ticket_row, message.body, "Learner replied")
        message.notification_sent = int(sent)
        s.commit()
        flash("Notification sent." if sent else "Notification could not be sent. Check SMTP and SUPPORT_EMAIL.", "ok" if sent else "err")
    return redirect(url_for("support.admin_ticket", ticket_id=ticket_id))
