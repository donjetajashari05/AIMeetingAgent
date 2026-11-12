"""
Security: startup checks, browser protections and the audit log.

- The server refuses to start with a missing or weak APP_SECRET (codes and sessions are
  keyed with it).
- Every response carries headers that stop the page being framed, sniffed or leaking the
  address, and a Content-Security-Policy that only allows the app's own files.
- Requests that change something must come from the app's own page (Origin check), so
  another website or local app can't act with the PM's session.
- LOGIN_DISABLED only works for requests from this computer.
- `audit_log` keeps who did what: sign-ins (also failed ones), sending to Jira, deleting,
  and changes to people and permissions. Admins see it on Settings → Security log.
"""
import ipaddress
import os
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor_id INTEGER,
    actor_email TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log(at);
"""

WEAK_SECRETS = {"", "change-me", "change-me-in-env", "secret", "changeme"}
MIN_SECRET = 32


class StartupError(RuntimeError):
    pass


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def check_startup():
    """Refuse to run with settings that would make the sign-in unsafe."""
    secret = os.environ.get("APP_SECRET") or ""
    if secret.strip().lower() in WEAK_SECRETS or len(secret) < MIN_SECRET:
        raise StartupError("APP_SECRET in .env is missing or too short. Set it to a long random string, e.g. the "
                           "output of: python -c \"import secrets; print(secrets.token_hex(32))\"")
    if dev_codes() and not _loopback_url(os.environ.get("APP_URL")):
        raise StartupError("DEV_LOGIN_CODES=1 shows sign-in codes on the page: it is only allowed when APP_URL "
                           "is this computer (127.0.0.1 / localhost).")


def dev_codes():
    """DEV_LOGIN_CODES=1: without SMTP, the sign-in code is filled in on the page (testing on
    this computer only). Off by default: then a missing SMTP means nobody can sign in."""
    return (os.environ.get("DEV_LOGIN_CODES") or "0").strip().lower() in ("1", "true", "yes")


def _loopback_url(url):
    host = urlsplit(url or "http://127.0.0.1").hostname or ""
    return host == "localhost" or _is_loopback(host)


def _is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def client_ip(request: Request):
    return request.client.host if request.client else None


def is_local(request: Request):
    return _is_loopback(client_ip(request) or "")


# ---------- Audit log ----------

def log(action, request: Request | None = None, user=None, target=None, detail=None, email=None):
    """Write one line to the audit log. Never raises: logging must not break the action."""
    try:
        with db.connect() as c:
            c.execute("INSERT INTO audit_log (at, actor_id, actor_email, action, target, detail, ip) VALUES (?,?,?,?,?,?,?)",
                      (db.now(), (user or {}).get("id"), (user or {}).get("email") or email, action,
                       None if target is None else str(target)[:200], None if detail is None else str(detail)[:500],
                       client_ip(request) if request else None))
    except Exception as e:  # noqa: BLE001
        print(f"audit log failed: {e}")


def entries(limit=300, action=None):
    with db.connect() as c:
        if action:
            return db.rows(c.execute("SELECT * FROM audit_log WHERE action LIKE ? ORDER BY id DESC LIMIT ?",
                                     (f"{action}%", limit)))
        return db.rows(c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)))


# ---------- HTTP protections ----------

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; media-src 'self' blob:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _same_origin(request: Request):
    """The request comes from a page of this app (or from no browser page at all)."""
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        if not referer:
            return True  # not sent by a web page (e.g. curl on this computer)
        origin = referer
    o = urlsplit(origin)
    host = request.headers.get("host") or ""
    return o.netloc == host


async def middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        if request.method in UNSAFE and not _same_origin(request):
            log("blocked.cross_origin", request, detail=f"{request.method} {path} from {request.headers.get('origin')}")
            return JSONResponse({"detail": "Request blocked: it did not come from this app."}, status_code=403)
        if (os.environ.get("LOGIN_DISABLED") or "0").strip().lower() in ("1", "true", "yes") and not is_local(request):
            return JSONResponse({"detail": "Sign-in is disabled, so the app only answers this computer."}, status_code=403)
    response = await call_next(request)
    for k, v in HEADERS.items():
        response.headers.setdefault(k, v)
    if not path.startswith("/api/"):
        response.headers.setdefault("Content-Security-Policy", CSP)
    else:
        response.headers.setdefault("Cache-Control", "no-store")  # API data is never kept in the browser cache
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response
