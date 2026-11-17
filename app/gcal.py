"""
Google Calendar: each PM connects their own calendar.

- Read: today's meetings on Home, and a new meeting can be filled in from a calendar event
  (title, date, who was invited, the project).
- Create: the "New online meeting" assistant (side panel) creates a meeting with a Google Meet
  link; Google emails the invitation to everyone invited and puts it in their calendars.

Permissions asked from Google (as few as possible):
- `calendar.events.readonly`: read events. It can not change or delete anything.
- `calendar.app.created`: the app may create its OWN calendar ("SyncUp meetings") and manage
  only the events in it. Everything else in the PM's calendars stays untouchable.
- `openid email`: the account's email address, to show which calendar is connected.
The app never sees the PM's emails.

- Sign-in with Google uses OAuth with PKCE and a one-time `state` bound to the PM who started it.
- The refresh token is stored encrypted (key derived from APP_SECRET); access tokens stay in
  memory only. "Disconnect" revokes the permission at Google and deletes the token.
- Sent to Google: the sign-in, the "events of this day" request, and for a new meeting its
  title, time, description and the invited email addresses.

Setup (.env): GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET from a Google Cloud OAuth client
("Web application") whose redirect URI is APP_URL + /api/calendar/callback.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from datetime import time as dtime

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_accounts (
    employee_id INTEGER PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,
    google_email TEXT,
    refresh_token TEXT NOT NULL,   -- encrypted (see _seal)
    connected_at TEXT NOT NULL
);

-- Online meetings created from the app (Google Meet): kept here too, shown on the Meetings page
CREATE TABLE IF NOT EXISTS online_meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_by INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    google_event_id TEXT NOT NULL,
    google_calendar_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    start_at TEXT NOT NULL,          -- ISO with time zone
    end_at TEXT NOT NULL,
    meet_link TEXT,
    calendar_link TEXT,
    guests TEXT NOT NULL,            -- JSON list of email addresses
    status TEXT NOT NULL DEFAULT 'scheduled',   -- scheduled | cancelled
    created_at TEXT NOT NULL,
    cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS online_meetings_start ON online_meetings(start_at);
"""
MIGRATIONS = {
    "scopes": "TEXT",            # permissions Google granted (space separated)
    "app_calendar_id": "TEXT",   # the app's own calendar "SyncUp meetings" (created on first use)
}

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"
EVENTS_URL = API + "/calendars/primary/events"
READ_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
CREATE_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
SCOPES = f"openid email {READ_SCOPE} {CREATE_SCOPE}"
APP_CALENDAR = "SyncUp meetings"
STATE_TTL = 600  # seconds to finish the Google sign-in
EMAIL_RE = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")
MAX_GUESTS = 100

_pending = {}   # state -> (employee_id, code_verifier, expires)
_access = {}    # employee_id -> (access_token, expires)
_lock = threading.Lock()


class CalendarError(RuntimeError):
    pass


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(calendar_accounts)")}
        for col, decl in MIGRATIONS.items():
            if col not in have:
                c.execute(f"ALTER TABLE calendar_accounts ADD COLUMN {col} {decl}")


def configured():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def redirect_uri():
    return (os.environ.get("APP_URL") or "http://127.0.0.1:8000").rstrip("/") + "/api/calendar/callback"


# ---------- Encryption of the stored refresh token (stdlib only) ----------
# HMAC-SHA256 as a keystream (counter mode) + HMAC-SHA256 tag over nonce and ciphertext.

def _keys():
    secret = (os.environ.get("APP_SECRET") or "").encode()
    return (hmac.new(secret, b"syncup-calendar-enc", hashlib.sha256).digest(),
            hmac.new(secret, b"syncup-calendar-mac", hashlib.sha256).digest())


def _stream(key, nonce, n):
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def _seal(text):
    enc, mac = _keys()
    nonce = secrets.token_bytes(16)
    data = text.encode()
    ct = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data))))
    tag = hmac.new(mac, nonce + ct, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + tag + ct).decode()


def _open(blob):
    enc, mac = _keys()
    raw = base64.urlsafe_b64decode(blob.encode())
    nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + ct, hashlib.sha256).digest()):
        raise CalendarError("The saved Google connection can't be read (APP_SECRET changed?). Connect the calendar again.")
    return bytes(a ^ b for a, b in zip(ct, _stream(enc, nonce, len(ct)))).decode()


# ---------- HTTP ----------

class GoogleHTTPError(CalendarError):
    def __init__(self, status, message):
        super().__init__(f"Google answered {status}: {message}")
        self.status = status


def _request(url, data=None, token=None, json_body=None, method=None):
    """data = form fields (OAuth endpoints); json_body = JSON (Calendar API)."""
    headers = {}
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body is not None else "GET"), headers=headers)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}")
        except ValueError:
            detail = {}
        err = detail.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        raise GoogleHTTPError(e.code, err or e.reason) from None
    except urllib.error.URLError as e:
        raise CalendarError(f"Google can't be reached: {e.reason}") from None


# ---------- Connect / disconnect ----------

def start(employee_id):
    """The Google sign-in address for this PM (PKCE + one-time state)."""
    if not configured():
        raise CalendarError("Google Calendar is not set up: add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if v[2] < now]:
            del _pending[k]
        _pending[state] = (employee_id, verifier, now + STATE_TTL)
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "false",
    })


def _email_from_id_token(id_token):
    # Received straight from Google's token endpoint over HTTPS: only read, used for display
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email")
    except (IndexError, ValueError, AttributeError):
        return None


def finish(state, code, employee_id):
    """Google sent the PM back: exchange the code, store the token. Returns the Google email.
    The sign-in must have been started by the same signed-in employee."""
    with _lock:
        entry = _pending.pop(state or "", None)
    if not entry or entry[2] < time.time():
        raise CalendarError("This Google sign-in has expired or was already used. Try again.")
    if entry[0] != employee_id:
        raise CalendarError("This Google sign-in was started by another user.")
    verifier = entry[1]
    tok = _request(TOKEN_URL, {
        "code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code", "code_verifier": verifier,
    })
    scopes = tok.get("scope") or ""
    if READ_SCOPE not in scopes:
        raise CalendarError("Calendar access was not allowed. Try again and tick the calendar permission.")
    if not tok.get("refresh_token"):
        raise CalendarError("Google did not give a lasting permission. Disconnect the app in your Google account and try again.")
    email = _email_from_id_token(tok.get("id_token"))
    with db.connect() as c:
        old = c.execute("SELECT app_calendar_id FROM calendar_accounts WHERE employee_id = ?", (employee_id,)).fetchone()
        c.execute("INSERT OR REPLACE INTO calendar_accounts (employee_id, google_email, refresh_token, connected_at, "
                  "scopes, app_calendar_id) VALUES (?,?,?,?,?,?)",
                  (employee_id, email, _seal(tok["refresh_token"]), db.now(), scopes, old["app_calendar_id"] if old else None))
    with _lock:
        _access[employee_id] = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)
    return email


def account(employee_id):
    with db.connect() as c:
        r = c.execute("SELECT google_email, connected_at, scopes FROM calendar_accounts WHERE employee_id = ?",
                      (employee_id,)).fetchone()
    if not r:
        return None
    return {"email": r["google_email"], "connected_at": r["connected_at"],
            "can_create": CREATE_SCOPE in (r["scopes"] or "")}


def disconnect(employee_id):
    """Revoke the permission at Google (best effort) and delete the stored token."""
    with db.connect() as c:
        r = c.execute("SELECT refresh_token FROM calendar_accounts WHERE employee_id = ?", (employee_id,)).fetchone()
        c.execute("DELETE FROM calendar_accounts WHERE employee_id = ?", (employee_id,))
    with _lock:
        _access.pop(employee_id, None)
    _forget_events(employee_id)  # a disconnected calendar must not keep showing events
    if r:
        try:
            _request(REVOKE_URL, {"token": _open(r["refresh_token"])})
        except CalendarError:
            pass  # already revoked or Google unreachable: the token is deleted here anyway
    return bool(r)


def _token(employee_id):
    with _lock:
        hit = _access.get(employee_id)
    if hit and hit[1] > time.time():
        return hit[0]
    with db.connect() as c:
        r = c.execute("SELECT refresh_token FROM calendar_accounts WHERE employee_id = ?", (employee_id,)).fetchone()
    if not r:
        raise CalendarError("Google Calendar is not connected.")
    try:
        tok = _request(TOKEN_URL, {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""), "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "refresh_token": _open(r["refresh_token"]), "grant_type": "refresh_token",
        })
    except CalendarError as e:
        if "invalid_grant" in str(e):
            with db.connect() as c:  # permission removed at Google or expired: ask to connect again
                c.execute("DELETE FROM calendar_accounts WHERE employee_id = ?", (employee_id,))
            raise CalendarError("The Google permission has expired or was removed. Connect the calendar again.") from None
        raise
    with _lock:
        _access[employee_id] = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)
    return tok["access_token"]


# ---------- Read events ----------

FIELDS = ("items(id,summary,status,start,end,attendees(email,displayName,self,resource,responseStatus),"
          "organizer(email,self),hangoutLink,htmlLink,eventType)")


def _app_calendar(employee_id):
    with db.connect() as c:
        r = c.execute("SELECT app_calendar_id FROM calendar_accounts WHERE employee_id = ?", (employee_id,)).fetchone()
    return r["app_calendar_id"] if r else None


def _day_items(calendar_id, start, token):
    return _request(f"{API}/calendars/{urllib.parse.quote(calendar_id, safe='@.')}/events?" + urllib.parse.urlencode({
        "timeMin": start.isoformat(), "timeMax": (start + timedelta(days=1)).isoformat(),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": 50, "fields": FIELDS,
    }), token=token).get("items", [])


EVENTS_TTL = 60  # seconds: Home asks for today's events on every load, Google takes ~0.5 s each time
_events_cache = {}  # (employee_id, day) -> (expires, items); kept in memory only, never on disk


def _forget_events(employee_id):
    """A meeting was created or cancelled here: the next load asks Google again."""
    with _lock:
        for k in [k for k in _events_cache if k[0] == employee_id]:
            _events_cache.pop(k, None)


def _google_day(employee_id, day):
    with _lock:
        hit = _events_cache.get((employee_id, day))
    if hit and hit[0] > time.time():
        return hit[1]
    start = datetime.combine(day, dtime.min).astimezone()
    token = _token(employee_id)
    items = _day_items("primary", start, token)
    own = _app_calendar(employee_id)
    if own:
        try:
            seen = {ev.get("id") for ev in items}
            items += [ev for ev in _day_items(own, start, token) if ev.get("id") not in seen]
        except GoogleHTTPError as e:
            if e.status not in (403, 404):  # the PM deleted the calendar: nothing to add
                raise
    with _lock:
        _events_cache[(employee_id, day)] = (time.time() + EVENTS_TTL, items)
    return items


def events(employee_id, day: date, project_ids=None):
    """Timed events of one day (main calendar + the app's own calendar), with who was invited
    (matched to employees), a speaker estimate and the most likely project (among `project_ids`;
    None = all)."""
    items = _google_day(employee_id, day)

    with db.connect() as c:
        people = {r["email"].lower(): dict(r) for r in c.execute(
            "SELECT id, full_name, email FROM employees WHERE email IS NOT NULL AND active = 1")}
        members = db.rows(c.execute("SELECT pm.project_id, pm.employee_id, p.name FROM project_members pm "
                                    "JOIN projects p ON p.id = pm.project_id"))

    out = []
    for ev in items:
        st, en = ev.get("start", {}), ev.get("end", {})
        if ev.get("status") == "cancelled" or "dateTime" not in st or ev.get("eventType") in ("outOfOffice", "focusTime", "workingLocation"):
            continue  # all-day events, absences and focus time are not meetings
        guests = [a for a in ev.get("attendees", []) if not a.get("resource")]
        if any(a.get("self") and a.get("responseStatus") == "declined" for a in guests):
            continue
        going = [a for a in guests if a.get("responseStatus") != "declined"]
        matched = []
        for a in going:
            e = people.get((a.get("email") or "").lower())
            matched.append({"name": e["full_name"] if e else (a.get("displayName") or a.get("email")),
                            "employee_id": e["id"] if e else None})
        ids = {m["employee_id"] for m in matched if m["employee_id"]}
        score = {}
        for m in members:
            if m["employee_id"] in ids and (project_ids is None or m["project_id"] in project_ids):
                score[m["project_id"]] = score.get(m["project_id"], 0) + 1
        best = max(score, key=score.get) if score else None
        out.append({
            "id": ev.get("id"),
            "title": (ev.get("summary") or "Meeting")[:200],
            "start": st["dateTime"], "end": en.get("dateTime"),
            "online": bool(ev.get("hangoutLink")),
            "meet_link": ev.get("hangoutLink"),
            "people": matched,
            "speakers": len(going) or None,
            "project_id": best,
        })
    out.sort(key=lambda e: datetime.fromisoformat(e["start"]))
    return out


# ---------- Create a meeting with Google Meet ----------

def _ensure_app_calendar(employee_id, token, fresh=False):
    """The app's own calendar ("SyncUp meetings"), created the first time. The app may only
    manage events in this calendar, never in the PM's other calendars."""
    cal_id = None if fresh else _app_calendar(employee_id)
    if cal_id:
        return cal_id
    tz = None
    try:  # same time zone as the PM's main calendar (readable with the read permission)
        tz = _request(f"{API}/calendars/primary/events?maxResults=1&fields=timeZone", token=token).get("timeZone")
    except CalendarError:
        pass
    body = {"summary": APP_CALENDAR, "description": "Online meetings created from SyncUp."}
    if tz:
        body["timeZone"] = tz
    cal_id = _request(f"{API}/calendars", token=token, json_body=body)["id"]
    with db.connect() as c:
        c.execute("UPDATE calendar_accounts SET app_calendar_id = ? WHERE employee_id = ?", (cal_id, employee_id))
    return cal_id


def clean_guests(emails):
    out, bad = [], []
    for e in emails or []:
        e = (e or "").strip().lower()
        if not e:
            continue
        (out if EMAIL_RE.match(e) and len(e) <= 254 else bad).append(e)
    if bad:
        raise CalendarError(f"Not a valid email: {', '.join(bad[:3])}")
    out = list(dict.fromkeys(out))
    if not out:
        raise CalendarError("Invite at least one person.")
    if len(out) > MAX_GUESTS:
        raise CalendarError(f"At most {MAX_GUESTS} people per meeting.")
    return out


def create_meeting(employee_id, title, start: datetime, minutes, guests, description=None):
    """Create an event with a Google Meet link in the app's calendar. Google emails the invitation
    to every guest (sendUpdates=all) and adds it to their calendars."""
    acc = account(employee_id)
    if not acc:
        raise CalendarError("Google Calendar is not connected.")
    if not acc["can_create"]:
        raise CalendarError("Connect Google Calendar again and allow SyncUp to create meetings.")
    token = _token(employee_id)
    body = {
        "summary": title,
        "description": description or None,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat()},
        # The PM is invited too, so the meeting is also in their main Gmail calendar
        "attendees": [{"email": g} for g in guests]
                     + ([{"email": acc["email"], "responseStatus": "accepted"}] if acc.get("email") and acc["email"].lower() not in guests else []),
        "conferenceData": {"createRequest": {"requestId": uuid.uuid4().hex,
                                             "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
        "guestsCanSeeOtherGuests": True,
        "reminders": {"useDefault": True},
    }
    body = {k: v for k, v in body.items() if v is not None}
    query = "?" + urllib.parse.urlencode({"conferenceDataVersion": 1, "sendUpdates": "all"})
    for attempt in (0, 1):
        cal_id = _ensure_app_calendar(employee_id, token, fresh=attempt == 1)
        try:
            ev = _request(f"{API}/calendars/{urllib.parse.quote(cal_id, safe='@.')}/events{query}", token=token, json_body=body)
            break
        except GoogleHTTPError as e:
            if attempt == 0 and e.status in (403, 404):
                continue  # the PM deleted the "SyncUp meetings" calendar: make a new one
            raise
    _forget_events(employee_id)
    link = ev.get("hangoutLink") or next(
        (p.get("uri") for p in (ev.get("conferenceData") or {}).get("entryPoints", []) if p.get("entryPointType") == "video"), None)
    return {"id": ev.get("id"), "calendar_id": cal_id, "title": ev.get("summary"), "start": ev["start"].get("dateTime"),
            "end": ev["end"].get("dateTime"), "meet_link": link, "calendar_link": ev.get("htmlLink"),
            "guests": len(guests)}


def save_online(employee_id, project_id, created, guests, description=None):
    with db.connect() as c:
        return c.execute("""INSERT INTO online_meetings (created_by, project_id, google_event_id, google_calendar_id, title,
                            description, start_at, end_at, meet_link, calendar_link, guests, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (employee_id, project_id, created["id"], created["calendar_id"], created["title"], description,
                          created["start"], created["end"], created["meet_link"], created["calendar_link"],
                          json.dumps(guests), db.now())).lastrowid


def online_meetings(user, days_back=7):
    """Scheduled online meetings: upcoming and of the last week. Admins see all; a PM sees the
    ones they created and the ones of the projects they manage."""
    since = (datetime.now().astimezone() - timedelta(days=days_back)).isoformat()
    with db.connect() as c:
        people = {r["email"].lower(): r["full_name"] for r in c.execute(
            "SELECT email, full_name FROM employees WHERE email IS NOT NULL")}
        rows = db.rows(c.execute("""
            SELECT o.*, p.name AS project_name, e.full_name AS created_by_name
            FROM online_meetings o LEFT JOIN projects p ON p.id = o.project_id LEFT JOIN employees e ON e.id = o.created_by
            WHERE o.end_at >= ? ORDER BY o.start_at""", (since,)))
    out = []
    for r in rows:
        if not (user["is_admin"] or r["created_by"] == user["id"] or r["project_id"] in user["pm_project_ids"]):
            continue
        guests = json.loads(r["guests"] or "[]")
        r["guests"] = guests
        r["people"] = [{"name": people.get(g, g), "email": g} for g in guests]
        out.append(r)
    return out


def get_online(online_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM online_meetings WHERE id = ?", (online_id,)).fetchone()
    return dict(r) if r else None


def cancel_online(employee_id, online_id):
    """Cancel at Google (everyone invited gets the cancellation by email) and mark it here."""
    m = get_online(online_id)
    if not m or m["status"] != "scheduled":
        raise CalendarError("This meeting is not scheduled.")
    token = _token(employee_id)
    url = (f"{API}/calendars/{urllib.parse.quote(m['google_calendar_id'], safe='@.')}/events/"
           f"{urllib.parse.quote(m['google_event_id'], safe='')}?sendUpdates=all")
    try:
        _request(url, token=token, method="DELETE")
    except GoogleHTTPError as e:
        if e.status not in (404, 410):  # already deleted in Google Calendar: just mark it here
            raise
    _forget_events(employee_id)
    with db.connect() as c:
        c.execute("UPDATE online_meetings SET status = 'cancelled', cancelled_at = ? WHERE id = ?", (db.now(), online_id))
    return m
