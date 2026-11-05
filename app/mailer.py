"""
Outgoing email through SMTP (Google Workspace / Gmail by default).

.env:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=emri@kompania.com
    SMTP_PASSWORD=<Google "App password", 16 characters>
    MAIL_FROM=AnchorzUp Meeting Agent <emri@kompania.com>

Without SMTP_USER/SMTP_PASSWORD nothing leaves this computer: every email is
kept in the outbox (page "Njoftimet") with status "dry-run".
"""
import html
import os
import smtplib
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
    created_at TEXT NOT NULL
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
    button = (f'<p style="margin:22px 0 0"><a href="{html.escape(link)}" style="background:#683790;color:#fff;'
              f'padding:12px 22px;border-radius:999px;text-decoration:none;font-weight:700;display:inline-block">'
              f'{html.escape(link_text or "Hap")}</a></p>') if link else ""
    return f"""<!doctype html><html><body style="margin:0;background:#FAF7FD;font-family:Arial,sans-serif;color:#2B2633">
<div style="max-width:560px;margin:0 auto;padding:28px 16px">
  <div style="background:#3D1466;color:#fff;border-radius:18px 18px 0 0;padding:18px 24px;font-weight:700;letter-spacing:.04em">
    AnchorzUp <span style="color:#F3CB50">Meeting Agent</span></div>
  <div style="background:#fff;border:1px solid #ECE4F6;border-top:0;border-radius:0 0 18px 18px;padding:24px">
    {paragraphs}{button}
  </div>
  <p style="color:#736C7C;font-size:12px;margin:14px 4px">Ky email u dërgua automatikisht nga AnchorzUp Meeting Agent.</p>
</div></body></html>"""


def send(to_email, subject, body_text, kind=None, link=None, link_text=None):
    """Send one email (or keep it as dry-run). Returns the outbox status."""
    text = f"{body_text}\n\n{link_text or 'Hap'}: {link}" if link else body_text
    status, error = "dry-run", None
    if configured():
        msg = EmailMessage()
        sender = os.environ.get("MAIL_FROM") or formataddr(("AnchorzUp Meeting Agent", os.environ["SMTP_USER"]))
        msg["From"] = sender
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid()
        msg.set_content(text)
        msg.add_alternative(_html(body_text, link, link_text), subtype="html")
        try:
            with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                              int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                s.send_message(msg)
            status = "sent"
        except (smtplib.SMTPException, OSError) as e:
            status, error = "failed", str(e)[:500]
    with db.connect() as c:
        c.execute("INSERT INTO outbox (to_email, subject, body, kind, status, error, created_at) VALUES (?,?,?,?,?,?,?)",
                  (to_email, subject, text if kind != "login" else _mask_code(text), kind, status, error, db.now()))
    return status


def _mask_code(text):
    """Login codes are never stored in the outbox."""
    import re
    return re.sub(r"\b\d{6}\b", "••••••", text)


def outbox(limit=200):
    with db.connect() as c:
        return db.rows(c.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,)))
