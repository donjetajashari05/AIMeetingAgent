"""
Login with a one-time code sent to the work email, sessions, and who may see what.

Single-user system: only the owner (ADMIN_EMAILS in .env, or an employee marked admin) can log in.
The owner is the PM and can do everything. Other employees are only meeting participants.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from . import db

SESSION_COOKIE = "ma_session"
SESSION_DAYS = 365  # used only when LOGIN_EACH_VISIT=0 (log in once)
SESSION_HOURS = 12  # default: a sign-in lasts at most a working day


def login_each_visit():
    """Default on: every time the link is opened (new tab or window) the sign-in form is shown;
    a refresh of an open tab keeps you signed in. LOGIN_EACH_VISIT=0 = stay signed in for a year."""
    return os.environ.get("LOGIN_EACH_VISIT", "1") != "0"


def session_length():
    return timedelta(hours=SESSION_HOURS) if login_each_visit() else timedelta(days=SESSION_DAYS)
CODE_MINUTES = 10
CODE_MAX_ATTEMPTS = 5
CODES_PER_HOUR = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS login_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
        c.execute("DELETE FROM login_codes WHERE expires_at < ?", (db.now(),))


def _secret():
    # Codes and tokens are stored only as keyed hashes
    return (os.environ.get("APP_SECRET") or "change-me-in-env").encode()


def _hash(value):
    return hmac.new(_secret(), value.encode(), hashlib.sha256).hexdigest()


def admin_emails():
    return {e.strip().lower() for e in (os.environ.get("ADMIN_EMAILS") or "").split(",") if e.strip()}


def allowed_domain(email):
    """If ALLOWED_EMAIL_DOMAINS is set (e.g. anchorzup.com), only those domains may log in."""
    domains = {d.strip().lower().lstrip("@") for d in (os.environ.get("ALLOWED_EMAIL_DOMAINS") or "").split(",") if d.strip()}
    return not domains or email.rsplit("@", 1)[-1] in domains


def is_owner(emp):
    return bool(emp["is_admin"]) or (emp["email"] or "").lower() in admin_emails()


def _employee_by_email(c, email):
    r = c.execute("SELECT * FROM employees WHERE email = ? COLLATE NOCASE AND active = 1", (email,)).fetchone()
    return dict(r) if r else None


# ---------- Codes ----------

def request_code(email):
    """Return (employee, code) when a code should be sent, else (None, None).
    The caller always answers the same way, so nobody can probe which emails exist."""
    email = (email or "").strip().lower()
    if "@" not in email or not allowed_domain(email):
        return None, None
    with db.connect() as c:
        emp = _employee_by_email(c, email)
        if not emp and email in admin_emails():
            # First login of an admin listed in .env: create the record
            emp_id = c.execute("INSERT INTO employees (full_name, email, active, is_admin, created_at) VALUES (?,?,1,1,?)",
                               (email.split("@")[0].replace(".", " ").title(), email, db.now())).lastrowid
            emp = dict(c.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone())
        if not emp or not is_owner(emp):
            return None, None  # participants cannot log in
        hour_ago = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        recent = c.execute("SELECT COUNT(*) FROM login_codes WHERE email = ? AND created_at > ?", (email, hour_ago)).fetchone()[0]
        if recent >= CODES_PER_HOUR:
            raise HTTPException(429, "Too many code requests. Try again in a little while.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        c.execute("UPDATE login_codes SET used = 1 WHERE email = ?", (email,))  # only the newest code works
        c.execute("INSERT INTO login_codes (email, code_hash, expires_at, created_at) VALUES (?,?,?,?)",
                  (email, _hash(f"{email}:{code}"),
                   (datetime.now() + timedelta(minutes=CODE_MINUTES)).isoformat(timespec="seconds"), db.now()))
    return emp, code


def verify_code(email, code):
    """Return a new session token, or raise 400."""
    email = (email or "").strip().lower()
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    with db.connect() as c:
        row = c.execute("SELECT * FROM login_codes WHERE email = ? AND used = 0 AND expires_at > ? "
                        "ORDER BY id DESC LIMIT 1", (email, db.now())).fetchone()
        if not row:
            raise HTTPException(400, "The code has expired or doesn’t exist. Request a new one.")
        if row["attempts"] >= CODE_MAX_ATTEMPTS:
            raise HTTPException(400, "Too many wrong attempts. Request a new code.")
        if not hmac.compare_digest(row["code_hash"], _hash(f"{email}:{code}")):
            c.execute("UPDATE login_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            c.commit()  # keep the failed attempt even though we raise (limits guessing)
            raise HTTPException(400, "The code is not correct.")
        c.execute("UPDATE login_codes SET used = 1 WHERE id = ?", (row["id"],))
        emp = _employee_by_email(c, email)
        if not emp or not is_owner(emp):
            raise HTTPException(400, "This account has no access to the system.")
        if email in admin_emails() and not emp["is_admin"]:
            c.execute("UPDATE employees SET is_admin = 1 WHERE id = ?", (emp["id"],))
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions (token_hash, employee_id, created_at, expires_at) VALUES (?,?,?,?)",
                  (_hash(token), emp["id"], db.now(),
                   (datetime.now() + session_length()).isoformat(timespec="seconds")))
    return token


def logout(token):
    if token:
        with db.connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


# ---------- Current user & scope ----------

def user_from_token(token):
    if not token:
        return None
    with db.connect() as c:
        r = c.execute("""
            SELECT e.* FROM sessions s JOIN employees e ON e.id = s.employee_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND e.active = 1
        """, (_hash(token), db.now())).fetchone()
        if not r or not is_owner(r):
            return None
        user = _build_user(c, dict(r))
    user["role"] = "pm"
    return user


def _build_user(c, user):
    uid = user["id"]
    user["is_admin"] = bool(user["is_admin"]) or (user["email"] or "").lower() in admin_emails()
    user["pm_project_ids"] = [x[0] for x in c.execute("SELECT id FROM projects WHERE pm_id = ?", (uid,))]
    user["project_ids"] = [x[0] for x in c.execute("SELECT project_id FROM project_members WHERE employee_id = ?", (uid,))]
    user["team_ids"] = [x[0] for x in c.execute("SELECT team_id FROM team_members WHERE employee_id = ?", (uid,))]
    user["is_pm"] = bool(user["pm_project_ids"])
    user["role"] = "admin" if user["is_admin"] else "pm" if user["is_pm"] else "employee"
    return user


def login_disabled():
    """LOGIN_DISABLED=1 skips the login entirely (local testing only)."""
    return (os.environ.get("LOGIN_DISABLED") or "0").strip().lower() in ("1", "true", "yes")


def _local_admin():
    with db.connect() as c:
        r = c.execute("SELECT id FROM employees WHERE is_admin = 1 AND active = 1 ORDER BY id LIMIT 1").fetchone()
        if r:
            emp_id = r[0]
        else:
            email = next(iter(sorted(admin_emails())), "admin@local")
            emp = _employee_by_email(c, email)
            if emp:
                emp_id = emp["id"]
                c.execute("UPDATE employees SET is_admin = 1 WHERE id = ?", (emp_id,))
            else:
                emp_id = c.execute("INSERT INTO employees (full_name, email, active, is_admin, created_at) VALUES (?,?,1,1,?)",
                                   ("Admin", email, db.now())).lastrowid
        user = _build_user(c, dict(c.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()))
    # Single-user system: the owner is the PM and can do everything; everyone else is only a meeting participant
    user["is_admin"] = True
    user["role"] = "pm"
    return user


def current_user(request: Request):
    """FastAPI dependency: the logged-in user, or 401."""
    if login_disabled():
        return _local_admin()
    user = user_from_token(request.cookies.get(SESSION_COOKIE))
    if not user:
        raise HTTPException(401, "Sign in with your work email.")
    return user


def require_admin(user):
    if not user["is_admin"]:
        raise HTTPException(403, "Only an administrator can do this.")


def visible_meeting_ids(user):
    """None = all meetings (admin). Otherwise the ids this user may open."""
    if user["is_admin"]:
        return None
    projects = set(user["project_ids"]) | set(user["pm_project_ids"])
    with db.connect() as c:
        rows = c.execute(f"""
            SELECT id FROM meetings
            WHERE created_by = ?
               OR project_id IN ({','.join('?' * len(projects)) or 'NULL'})
               OR (project_id IS NULL AND team_id IN ({','.join('?' * len(user['team_ids'])) or 'NULL'}))
        """, (user["id"], *projects, *user["team_ids"])).fetchall()
    return {r[0] for r in rows}


def can_manage_meeting(user, meeting):
    """Admin, the PM of the meeting's project, or whoever added it."""
    return (user["is_admin"] or meeting.get("created_by") == user["id"]
            or (meeting.get("project_id") in user["pm_project_ids"]))


def check_meeting(user, meeting_id, manage=False):
    m = db.get_meeting(meeting_id)
    visible = visible_meeting_ids(user)
    if not m or (visible is not None and meeting_id not in visible):
        raise HTTPException(404, "Meeting not found.")
    if manage and not can_manage_meeting(user, m):
        raise HTTPException(403, "Only the project’s PM or an administrator can do this.")
    return m


def sees_tasks(user):
    """Tasks and tickets are only for PMs and administrators: team leads and the others work on
    them in Jira directly, so the app doesn't show them."""
    return bool(user["is_admin"] or user["is_pm"])


def task_scope(user):
    """(meeting_ids, assignee_id) filter for task lists. Admin: all.
    PM: every task of their projects' meetings + own. Everyone else: none (they use Jira)."""
    if user["is_admin"]:
        return None, None
    if not sees_tasks(user):
        return set(), 0  # matches nothing (ids start at 1)
    if user["is_pm"]:
        with db.connect() as c:
            ids = {r[0] for r in c.execute(
                f"SELECT id FROM meetings WHERE project_id IN ({','.join('?' * len(user['pm_project_ids']))})",
                user["pm_project_ids"])}
        return ids, user["id"]  # tasks in those meetings OR assigned to me


def can_edit_task(user, task, reassign=False):
    m = db.get_meeting(task["meeting_id"])
    if user["is_admin"] or (m and can_manage_meeting(user, m)):
        return True
    return sees_tasks(user) and not reassign and task.get("assignee_id") == user["id"]
