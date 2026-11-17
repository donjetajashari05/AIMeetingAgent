"""
Outgoing email through any SMTP service (STARTTLS on port 587): Gmail, Brevo, ...

.env (Gmail):
    SMTP_HOST=smtp.gmail.com
    SMTP_USER=name@gmail.com
    SMTP_PASSWORD=<Google "App password", 16 characters>
.env (Brevo):
    SMTP_HOST=smtp-relay.brevo.com
    SMTP_USER=<the "Login" from Brevo > SMTP & API, …@smtp-brevo.com>
    SMTP_PASSWORD=<a Brevo SMTP key>
    MAIL_FROM=SyncUp <a sender verified in Brevo>

Without SMTP_USER/SMTP_PASSWORD nothing leaves this computer: every email is
kept in the outbox (page "Emails sent") with status "dry-run".
"""
import html
import os
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    kind TEXT,              -- login | meeting | reminder
    status TEXT NOT NULL,   -- sent | failed | dry-run
    error TEXT,
    created_at TEXT NOT NULL,
    owner_id INTEGER        -- the PM whose workspace sent it (each PM sees only their own emails)
);
"""


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def configured():
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def app_url():
    return (os.environ.get("APP_URL") or "http://127.0.0.1:8000").rstrip("/")


def _html(body_text, link=None, link_text=None):
    """A simple branded HTML version of a plain-text email."""
    paragraphs = "".join(
        f'<p style="margin:0 0 14px;line-height:1.55">{html.escape(p).replace(chr(10), "<br>")}</p>'
        for p in body_text.strip().split("\n\n"))
    button = (f'<p style="margin:22px 0 0"><a href="{html.escape(link)}" style="background:#7646D6;color:#fff;'
              f'padding:12px 22px;border-radius:999px;text-decoration:none;font-weight:700;display:inline-block">'
              f'{html.escape(link_text or "Open")}</a></p>') if link else ""
    return f"""<!doctype html><html><body style="margin:0;background:#F7F5FC;font-family:Arial,sans-serif;color:#2B2633">
<div style="max-width:560px;margin:0 auto;padding:28px 16px">
  <div style="background:#7445D4;background:linear-gradient(135deg,#8A5BE0,#6A3CCB);color:#fff;border-radius:18px 18px 0 0;padding:18px 24px;font-weight:700;letter-spacing:.04em">
    SyncUp</div>
  <div style="background:#fff;border:1px solid #ECE4F6;border-top:0;border-radius:0 0 18px 18px;padding:24px">
    {paragraphs}{button}
  </div>
  <p style="color:#736C7C;font-size:12px;margin:14px 4px">This email was sent automatically by SyncUp.</p>
</div></body></html>"""


def send(to_email, subject, body_text, kind=None, link=None, link_text=None, owner_id=None):
    """Send one email (or keep it as dry-run). Returns the outbox status.
    owner_id: the PM it is sent for; it shows only in that PM's "Emails" page."""
    text = f"{body_text}\n\n{link_text or 'Open'}: {link}" if link else body_text
    status, error = "dry-run", None
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    if configured() and not os.environ.get("MAIL_FROM") and "brevo" in host:
        # Brevo's SMTP login (…@smtp-brevo.com) is not a mailbox: the sender must be set
        status, error = "failed", "Set MAIL_FROM in .env to a sender verified in Brevo."
    elif configured():
        msg = EmailMessage()
        sender = os.environ.get("MAIL_FROM") or formataddr(("SyncUp", os.environ["SMTP_USER"]))
        msg["From"] = sender
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid()
        msg.set_content(text)
        msg.add_alternative(_html(body_text, link, link_text), subtype="html")
        try:
            with _SMTP4(host, int(os.environ.get("SMTP_PORT") or "587"), timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                _login(s, os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                s.send_message(msg)
            status = "sent"
        except (smtplib.SMTPException, OSError) as e:
            status, error = "failed", str(e)[:500]
    with db.connect() as c:
        c.execute("INSERT INTO outbox (to_email, subject, body, kind, status, error, created_at, owner_id) "
                  "VALUES (?,?,?,?,?,?,?,?)",
                  (to_email, subject if kind != "login" else _mask_code(subject),
                   text if kind != "login" else _mask_code(text), kind, status, error, db.now(), owner_id))
    return status


class _SMTP4(smtplib.SMTP):
    """Try IPv4 first: on networks where IPv6 is broken, the default order waits for the
    full timeout on the IPv6 address before falling back, so sign-in codes arrive very late."""

    def _get_socket(self, host, port, timeout):
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            infos = []
        for *_, addr in infos:
            try:
                return socket.create_connection(addr, timeout, self.source_address)
            except OSError:
                continue
        return super()._get_socket(host, port, timeout)


def _login(s, user, password):
    """AUTH LOGIN when the server offers it: with AUTH PLAIN Gmail sometimes just drops the
    connection, which hides the real reason (e.g. "535 Username and Password not accepted")."""
    s.ehlo()
    password = password.replace(" ", "")  # Google shows App passwords in groups of 4
    if "LOGIN" in s.esmtp_features.get("auth", "").upper().split():
        s.user, s.password = user, password
        s.auth("LOGIN", s.auth_login, initial_response_ok=False)
    else:
        s.login(user, password)


def _mask_code(text):
    """Login codes are never stored in the outbox."""
    import re
    return re.sub(r"\b\d{6}\b", "••••••", text)


def outbox(owner_id=None, limit=200):
    """owner_id: only the emails sent for this PM (None = all, local single-user mode)."""
    with db.connect() as c:
        if owner_id is None:
            return db.rows(c.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,)))
        return db.rows(c.execute("SELECT * FROM outbox WHERE owner_id = ? ORDER BY id DESC LIMIT ?", (owner_id, limit)))
