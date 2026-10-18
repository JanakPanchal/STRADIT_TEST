"""Outgoing email. Backends: console (dev: prints to the server log), smtp (production), memory (tests)."""
import html as _html
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("app.mail")


@dataclass
class Message:
    to: str
    subject: str
    text: str
    html: str | None = None


OUTBOX: list[Message] = []   # the memory backend appends here


def send(msg: Message) -> None:
    """Never raises into the caller's request: a mail failure is logged, not shown to the (anonymous) user."""
    s = get_settings()
    try:
        if s.mail_backend == "memory":
            OUTBOX.append(msg)
        elif s.mail_backend == "smtp":
            _smtp(msg)
        else:
            log.warning("EMAIL (console backend; nothing was sent)\n  To: %s\n  Subject: %s\n\n%s", msg.to, msg.subject, msg.text)
    except Exception:
        log.exception("could not send email to %s", msg.to)


def _smtp(msg: Message) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise RuntimeError("MAIL_BACKEND=smtp but SMTP_HOST is not set")
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = s.mail_from, msg.to, msg.subject
    m.set_content(msg.text)
    if msg.html:
        m.add_alternative(msg.html, subtype="html")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as c:
        c.ehlo()
        if s.smtp_starttls:
            c.starttls()
            c.ehlo()
        if s.smtp_user:
            c.login(s.smtp_user, s.smtp_password)
        c.send_message(m)


# ---------------------------------------------------------------- templates (plain, no tracking, no images)
def _wrap(title: str, body_html: str) -> str:
    return ('<div style="font-family:Helvetica,Arial,sans-serif;max-width:520px;margin:0 auto;color:#111">'
            '<div style="border-top:4px solid #111;padding:18px 0 6px;font-weight:800;letter-spacing:.2em;font-size:12px">'
            '<span style="display:inline-block;width:10px;height:10px;background:#e30613;margin-right:8px"></span>STRADIT</div>'
            f'<h2 style="margin:8px 0 16px">{title}</h2>{body_html}'
            '<p style="color:#777;font-size:12px;margin-top:28px;border-top:1px solid #ddd;padding-top:10px">'
            'This message was sent by the campus assessment platform. Do not forward it: the link is personal.</p></div>')


def invite_email(to: str, name: str, link: str, days: int, login_url: str, guide_url: str) -> Message:
    raw_name = name
    name, link, login_url, guide_url = (_html.escape(x, quote=True) for x in (name, link, login_url, guide_url))  # names are user-supplied
    text = (f"Hello {raw_name},\n\nYou have been invited to take the campus recruitment assessment.\n\n"
            f"1. Set your password (link valid for {days} days):\n   {_html.unescape(link)}\n"
            f"2. Then sign in at {_html.unescape(login_url)}\n\n"
            f"Before the test: use a laptop or desktop with Chrome, Edge or Firefox and a stable connection. Read the guide: {_html.unescape(guide_url)}\n\n"
            "If you were not expecting this message, you can ignore it.\n")
    html = _wrap("You are invited", f'<p>Hello {name},</p><p>You have been invited to take the campus recruitment assessment.</p>'
                 f'<p><a href="{link}" style="display:inline-block;background:#111;color:#fff;padding:12px 20px;text-decoration:none;font-weight:700">Set your password</a></p>'
                 f'<p style="font-size:13px;color:#555">The link is valid for {days} days. Then sign in at <a href="{login_url}">{login_url}</a>.</p>'
                 f'<p style="font-size:13px">Use a laptop or desktop with Chrome, Edge or Firefox. <a href="{guide_url}">Read the candidate guide</a> before your test.</p>')
    return Message(to, "You are invited to the campus assessment", text, html)


def reset_email(to: str, name: str, link: str, minutes: int) -> Message:
    raw_name, raw_link = name, link
    name, link = _html.escape(name, quote=True), _html.escape(link, quote=True)
    text = (f"Hello {raw_name},\n\nSomeone asked to reset the password for this account. If it was you, open this link "
            f"(valid for {minutes} minutes, works once):\n\n   {raw_link}\n\nIf it was not you, ignore this email; your password has not changed.\n")
    html = _wrap("Reset your password", f'<p>Hello {name},</p><p>Someone asked to reset the password for this account. If it was you, use the button below.</p>'
                 f'<p><a href="{link}" style="display:inline-block;background:#111;color:#fff;padding:12px 20px;text-decoration:none;font-weight:700">Choose a new password</a></p>'
                 f'<p style="font-size:13px;color:#555">Valid for {minutes} minutes and works once. If it was not you, ignore this email; your password has not changed.</p>')
    return Message(to, "Reset your password", text, html)


def staff_invite_email(to: str, name: str, role: str, link: str, days: int, login_url: str, inviter: str) -> Message:
    raw_name, raw_link, raw_login = name, link, login_url
    name, link, login_url, inviter_h = (_html.escape(x, quote=True) for x in (name, link, login_url, inviter))
    what = "full administrator access" if role == "admin" else "view-only access (you can monitor candidates and results but not change anything)"
    text = (f"Hello {raw_name},\n\n{inviter} added you to the campus assessment platform with {what}.\n\n"
            f"1. Set your password (link valid for {days} days):\n   {raw_link}\n2. Then sign in at {raw_login}\n\n"
            "If you were not expecting this, ignore this email.\n")
    html = _wrap("You have been added to the team", f'<p>Hello {name},</p><p>{inviter_h} added you with <b>{_html.escape(what)}</b>.</p>'
                 f'<p><a href="{link}" style="display:inline-block;background:#111;color:#fff;padding:12px 20px;text-decoration:none;font-weight:700">Set your password</a></p>'
                 f'<p style="font-size:13px;color:#555">Valid for {days} days. Then sign in at <a href="{login_url}">{login_url}</a>.</p>')
    return Message(to, "You have been added to the campus assessment team", text, html)
