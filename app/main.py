"""
AnchorzUp Meeting Agent — web app.

Run (from the project root, with the venv active):
    python -m app
Then open http://127.0.0.1:8000
"""
import json
import mimetypes
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date
from starlette.concurrency import run_in_threadpool
from pathlib import Path

import ctranslate2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import assistant, auth, db, deadlines, dedupe, jira, learning, mailer, memory, notify, org, owners, pipeline, security, standup, voices, tickets as ticketing, weekly  # noqa: E402

UPLOAD_DIR = db.ROOT / "data" / "uploads"
STATIC_DIR = Path(__file__).resolve().parent / "static"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".mp4", ".flac", ".aac"}
MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB
MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
BUSY = ("queued", "transcribing", "diarizing", "extracting")


@asynccontextmanager
async def lifespan(_app):
    security.check_startup()  # refuses to run with a weak APP_SECRET
    db.init()
    security.init()
    org.init()
    auth.init()
    mailer.init()
    notify.init()
    learning.init()
    memory.init()
    owners.init()
    standup.init()
    voices.init()
    weekly.init()
    deadlines.init()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    pipeline.start_worker()
    notify.start_scheduler()
    weekly.start_scheduler()
    jira.start_sync()
    if not auth.admin_emails():
        print("WARNING: set ADMIN_EMAILS in .env so you can sign in as administrator.")
    yield


app = FastAPI(title="AnchorzUp Meeting Agent", lifespan=lifespan)
User = Depends(auth.current_user)
app.middleware("http")(security.middleware)  # security headers, same-origin check, audit of blocked requests


@app.middleware("http")
async def no_stale_pages(request: Request, call_next):
    """The browser must check for a new index.html / app.js / app.css on every load, so a
    normal refresh shows the latest version after the server is restarted (no Ctrl+F5)."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------- Login ----------

class EmailIn(BaseModel):
    email: str


class CodeIn(BaseModel):
    email: str
    code: str


@app.post("/api/auth/request")
def auth_request(body: EmailIn, request: Request):
    if auth.login_disabled():
        raise HTTPException(404, "Sign-in is disabled: the system has a single user.")
    emp, code = auth.request_code(body.email)
    security.log("login.code_requested", request, email=(body.email or "").strip().lower()[:200],
                 detail="known account" if emp else "unknown or not allowed")
    if emp:
        if not mailer.configured() and not (security.dev_codes() and security.is_local(request)):
            # Never show a sign-in code on the page in a real setup: it would let anyone in
            raise HTTPException(503, "Email (SMTP) is not configured, so sign-in codes can't be sent. "
                                     "Ask the administrator to set SMTP in .env.")
        status = mailer.send(emp["email"], f"Your sign-in code: {code}",
                             f"Hi {emp['full_name'].split()[0]},\n\nYour code for AnchorzUp Meeting Agent is: {code}\n\n"
                             f"The code is valid for {auth.CODE_MINUTES} minutes. If you didn’t request it, ignore this email.",
                             kind="login")
        if status == "dry-run":
            # Only with DEV_LOGIN_CODES=1, from this computer (checked above): testing without email
            return {"ok": True, "message": "Test mode (DEV_LOGIN_CODES=1): the code was filled in automatically.", "dev_code": code}
        elif status == "failed":
            raise HTTPException(502, "The email with the code could not be sent. Check the SMTP settings in .env.")
    # Same answer whether the email exists or not
    return {"ok": True, "message": "If the email is registered, the code was sent."}


@app.post("/api/auth/verify")
def auth_verify(body: CodeIn, response: Response, request: Request):
    if auth.login_disabled():
        raise HTTPException(404, "Sign-in is disabled: the system has a single user.")
    email = (body.email or "").strip().lower()[:200]
    try:
        token = auth.verify_code(body.email, body.code)
    except HTTPException as e:
        security.log("login.failed", request, email=email, detail=e.detail)
        raise
    security.log("login.ok", request, email=email)
    # Each-visit sign-in: a browser-session cookie (gone when the browser closes)
    max_age = None if auth.login_each_visit() else auth.SESSION_DAYS * 86400
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=max_age, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")
    return {"ok": True}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(auth.SESSION_COOKIE)
    who = auth.user_from_token(token) if token else None
    auth.logout(token)
    if who:
        security.log("logout", request, user=who)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=User):
    return {**{k: user[k] for k in ("id", "full_name", "email", "role", "is_admin", "is_pm",
                                    "pm_project_ids", "project_ids", "team_ids")},
            "login_disabled": auth.login_disabled(), "login_each_visit": auth.login_each_visit()}


# ---------- Status ----------

@app.get("/api/status")
def status(user=User):
    gpu = ctranslate2.get_cuda_device_count() > 0
    return {
        "claude": pipeline.has_claude_key(),   # any language model (Claude or Groq)
        "ai_label": assistant.llm.label(),
        "speakers": pipeline.has_hf_token(),
        "email": mailer.configured(),
        "jira": jira.configured(),
        "jira_mock": jira.mock(),
        "jira_default": jira._default_key() or None,  # used when a ticket/project has no Jira key
        "jira_url": os.environ.get("JIRA_BASE_URL", "").rstrip("/") if jira.configured() and not jira.mock() else None,
        "gpu": gpu,
        "models": MODELS,
        "default_model": os.environ.get("WHISPER_MODEL") or "large-v3",  # most accurate for Albanian
        "today": date.today().isoformat(),
    }


# ---------- Meetings ----------

@app.get("/api/meetings")
def meetings(user=User):
    return db.list_meetings(auth.visible_meeting_ids(user))


@app.post("/api/meetings")
async def create_meeting(
    file: UploadFile = File(...),
    title: str = Form(""),
    meeting_date: str = Form(""),
    language: str = Form(""),
    model: str = Form("large-v3"),
    source: str = Form("audio"),
    team_id: int | None = Form(None),
    project_id: int | None = Form(None),
    kind: str = Form("meeting"),        # meeting | standup (daily standup: a report per person, no tasks)
    speakers: int | None = Form(None),  # how many people spoke, if known
    user=User,
):
    # Admins add any meeting; a PM only for the projects they manage
    if not user["is_admin"]:
        if not user["is_pm"]:
            raise HTTPException(403, "Only PMs and administrators can add meetings.")
        if project_id not in user["pm_project_ids"]:
            raise HTTPException(403, "Choose one of the projects you manage.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT | {".json"}:
        raise HTTPException(400, "Unknown format. Use mp3, wav, m4a, webm, ogg, mp4, flac or json.")
    if model not in MODELS:
        raise HTTPException(400, "Model i panjohur.")
    try:
        mdate = date.fromisoformat(meeting_date) if meeting_date else date.today()
    except ValueError:
        raise HTTPException(400, "The date must be in YYYY-MM-DD format.")
    if project_id:  # the project decides the team
        with db.connect() as c:
            row = c.execute("SELECT team_id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(400, "Project not found.")
        team_id = row["team_id"]

    # Random file name: never trust the uploaded one for paths
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "The file is larger than 1 GB.")
            out.write(chunk)

    standup_kind = "standup" if kind == "standup" else "meeting"
    kind = "json" if ext == ".json" else ("recording" if source == "recording" else "audio")
    name = (title.strip() or (f"Daily standup {mdate.isoformat()}" if standup_kind == "standup" else "")
            or Path(file.filename or "Meeting").stem or "Meeting")
    meeting_id = db.create_meeting(name[:200], mdate.isoformat(), kind, str(dest), model,
                                   language if language in ("sq", "en") else None,
                                   team_id or None, project_id or None, created_by=user["id"], kind=standup_kind,
                                   speakers_hint=speakers if speakers and 1 <= speakers <= 30 else None)
    pipeline.enqueue(meeting_id)
    return {"id": meeting_id}


@app.get("/api/meetings/{meeting_id}")
def meeting(meeting_id: int, user=User):
    auth.check_meeting(user, meeting_id)
    m = db.meeting_detail(meeting_id)
    m["can_manage"] = auth.can_manage_meeting(user, m)
    m["earlier_work"] = memory.for_meeting(meeting_id)  # what this meeting said about earlier tasks
    m["unowned"] = owners.for_meeting(meeting_id)  # discussed, but nobody was given it
    m["speakers"] = voices.speakers(meeting_id) if m["can_manage"] else []  # who each voice is (no fingerprints)
    if not auth.sees_tasks(user):  # tasks and tickets are for PMs; the others use Jira
        m["tasks"], m["earlier_work"], m["unowned"] = [], [], []
    return m


@app.get("/api/meetings/{meeting_id}/audio")
def meeting_audio(meeting_id: int, user=User):
    m = auth.check_meeting(user, meeting_id)
    if m["source"] == "json" or not m["file_path"] or not Path(m["file_path"]).is_file():
        raise HTTPException(404, "There is no audio for this meeting.")
    path = Path(m["file_path"])
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@app.post("/api/meetings/{meeting_id}/retry")
def retry(meeting_id: int, user=User):
    m = auth.check_meeting(user, meeting_id, manage=True)
    if m["status"] in BUSY:
        raise HTTPException(409, "The meeting is being processed.")
    with db.connect() as c:
        sent = c.execute("SELECT COUNT(*) FROM tasks WHERE meeting_id = ? AND jira_key IS NOT NULL", (meeting_id,)).fetchone()[0]
    if sent:
        raise HTTPException(409, f"{sent} task(s) were already sent to Jira; a new analysis would replace them. Edit the tasks by hand.")
    # With a transcript we only redo the extraction; otherwise start from scratch
    job = "extract" if m["transcript"] else "process"
    db.update_meeting(meeting_id, status="queued", error=None, note=None)
    pipeline.enqueue(meeting_id, job)
    return {"ok": True}


# ---------- Transcript fixes (the system learns from them) ----------

class LineIn(BaseModel):
    text: str


def _lines(data):
    return data.get("utterances") or data.get("segments") or []


@app.patch("/api/meetings/{meeting_id}/lines/{index}")
def edit_line(meeting_id: int, index: int, body: LineIn, user=User):
    """The PM fixes one transcript line. The changed words are learned for future meetings."""
    m = auth.check_meeting(user, meeting_id, manage=True)
    if m["status"] in BUSY:
        raise HTTPException(409, "The meeting is being processed. Try again when it finishes.")
    if not m["transcript"]:
        raise HTTPException(404, "This meeting has no transcript.")
    text = " ".join((body.text or "").split())[:5000]
    if not text:
        raise HTTPException(400, "The line can’t be empty.")
    data = json.loads(m["transcript"])
    lines = _lines(data)
    if not 0 <= index < len(lines):
        raise HTTPException(404, "Line not found.")
    line = lines[index]
    old = line["text"]
    learned = learning.learn(old, text) if text != old else []
    if text != old:
        line.setdefault("raw", old)
        line["text"] = text
        line["edited"] = True
        db.save_transcript(meeting_id, data)
    others = [l for i, l in enumerate(lines) if i != index]
    return {"line": line, "learned": learned, "others": learning.count_matches(others, learned) if learned else 0}


@app.patch("/api/tasks/{task_id}/quote")
def edit_quote(task_id: int, body: LineIn, user=User):
    """The PM fixes the Albanian quote on a task/ticket. The changed words are learned, and the
    same sentence is fixed in the meeting's transcript, decisions and other tasks."""
    t = db.get_task(task_id)
    if not t:
        raise HTTPException(404, "Ticket not found.")
    m = auth.check_meeting(user, t["meeting_id"], manage=True)
    text = " ".join((body.text or "").split())[:2000]
    old = t["quote"] or ""
    if not text:
        raise HTTPException(400, "The quote can’t be empty.")
    if text == old:
        return {"quote": old, "learned": [], "lines": 0}
    learned = learning.learn(old, text)
    with db.connect() as c:
        c.execute("UPDATE tasks SET quote = ? WHERE id = ? OR (meeting_id = ? AND quote = ?)",
                  (text, task_id, t["meeting_id"], old))
        c.execute("UPDATE decisions SET quote = ? WHERE meeting_id = ? AND quote = ?", (text, t["meeting_id"], old))
    fixed = 0
    if m["transcript"] and old and m["status"] not in BUSY:
        data = json.loads(m["transcript"])
        for line in _lines(data):
            if old in line["text"]:
                line.setdefault("raw", line["text"])
                line["text"] = line["text"].replace(old, text)
                line["edited"] = True
                fixed += 1
        if fixed:
            db.save_transcript(t["meeting_id"], data)
    return {"quote": text, "learned": learned, "lines": fixed}


@app.post("/api/meetings/{meeting_id}/apply-learned")
def apply_learned(meeting_id: int, user=User):
    """Apply every learned correction to this meeting's transcript."""
    m = auth.check_meeting(user, meeting_id, manage=True)
    if m["status"] in BUSY:
        raise HTTPException(409, "The meeting is being processed. Try again when it finishes.")
    if not m["transcript"]:
        raise HTTPException(404, "This meeting has no transcript.")
    data = json.loads(m["transcript"])
    changed = learning.apply_to_data(data)
    if changed:
        db.save_transcript(meeting_id, data)
    return {"changed": changed}


@app.get("/api/vocabulary")
def vocabulary(user=User):
    auth.require_admin(user)
    return {"learned": learning.all_corrections(), "glossary": pipeline.vocab.load_glossary()}


class CorrectionIn(BaseModel):
    active: bool


@app.patch("/api/vocabulary/{correction_id}")
def toggle_correction(correction_id: int, body: CorrectionIn, user=User):
    auth.require_admin(user)
    learning.set_active(correction_id, body.active)
    return {"ok": True}


@app.delete("/api/vocabulary/{correction_id}")
def delete_correction(correction_id: int, user=User):
    auth.require_admin(user)
    learning.delete(correction_id)
    return {"ok": True}


class JiraIn(BaseModel):
    task_ids: list[int] | None = None  # the confirmed tasks the PM chose; None = all confirmed ones


@app.post("/api/meetings/{meeting_id}/jira")
def send_to_jira(meeting_id: int, body: JiraIn | None = None, user=User):
    auth.check_meeting(user, meeting_id, manage=True)
    try:
        return jira.push_meeting(meeting_id, body.task_ids if body else None)
    except jira.JiraError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/meetings/{meeting_id}")
def delete(meeting_id: int, request: Request, user=User):
    m = auth.check_meeting(user, meeting_id, manage=True)
    if m["status"] in BUSY[1:]:
        raise HTTPException(409, "Wait until processing finishes.")
    db.delete_meeting(meeting_id)
    security.log("meeting.delete", request, user, target=meeting_id, detail=f"{m['title']} ({m['meeting_date']})")
    if m["file_path"]:
        Path(m["file_path"]).unlink(missing_ok=True)
    return {"ok": True}


# ---------- Tasks & report ----------

@app.get("/api/tasks")
def tasks(status: str | None = None, assignee_id: int | None = None, user=User):
    jira.sync_status()  # tickets moved to Done in Jira become done here (at most once a minute)
    return db.list_tasks(status if status in ("open", "done") else None, assignee_id, auth.task_scope(user))


class TaskUpdate(BaseModel):
    status: str | None = None
    assignee_id: int | None = None
    set_assignee: bool = False  # true = change assignee_id (null removes the link)
    description: str | None = None
    due_date: str | None = None
    set_due_date: bool = False  # true = change due_date (null removes it)
    approved: bool | None = None  # PM confirms (true) or un-confirms (false) the task
    # Ticket review
    assignee_email: str | None = None
    set_email: bool = False       # true = change the recipient email (null/empty = use the employee's)
    details: str | None = None
    jira_project: str | None = None
    priority: str | None = None       # high | medium | low ; "" removes it
    estimate_hours: float | None = None
    set_estimate: bool = False        # true = change estimate_hours (null removes it)


class TaskIn(BaseModel):
    description: str
    assignee_id: int | None = None
    due_date: str | None = None


def _check_due(value):
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(400, "The due date must be in YYYY-MM-DD format.")


def _check_desc(value):
    value = (value or "").strip()
    if not value:
        raise HTTPException(400, "Write the task description.")
    return value[:1000]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, user=User):
    """One task or ticket (the edit dialog on the Tasks page)."""
    t = db.get_task(task_id)
    if not t or not auth.sees_tasks(user):
        raise HTTPException(404, "Task not found.")
    auth.check_meeting(user, t["meeting_id"])
    t["history"] = memory.for_task(task_id)  # what later meetings said about it
    return t


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate, user=User):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found.")
    ticket_edit = (body.set_email or body.details is not None or body.jira_project is not None
                   or body.priority is not None or body.set_estimate)
    pm_edit = (body.set_assignee or body.description is not None or body.set_due_date
               or body.approved is not None or ticket_edit)
    if not auth.can_edit_task(user, task, reassign=pm_edit):
        raise HTTPException(403, "You are not allowed to change this task.")
    if task["jira_key"] and (body.description is not None or body.set_due_date or body.set_assignee
                             or body.approved is False or ticket_edit):
        raise HTTPException(409, f"This task was sent to Jira ({task['jira_key']}). Change it there.")
    fields = {}
    if body.priority is not None:
        value = db.check_priority(body.priority)
        if body.priority.strip() and not value:
            raise HTTPException(400, "The priority must be high, medium or low.")
        if value != task.get("priority"):
            fields["priority"], fields["priority_reason"] = value, None  # the PM's choice, not the AI's reason
    if body.set_estimate:
        fields["estimate_hours"] = db.check_estimate(body.estimate_hours)
    if body.details is not None:
        fields["details"] = body.details.strip()[:4000] or None
    if body.jira_project is not None:
        key = body.jira_project.strip().upper() or None
        if key and not org.JIRA_KEY_RE.match(key):
            raise HTTPException(400, "The Jira project must look like PAY or HR2 (capital letters).")
        fields["jira_project"] = key
    if body.set_email:
        email = (body.assignee_email or "").strip().lower()
        if email:
            if not org.EMAIL_RE.match(email):
                raise HTTPException(400, "The email is not valid.")
            if not auth.allowed_domain(email):
                raise HTTPException(400, "Only the company’s work emails are allowed.")
            emp = org.employee_by_email(email)
            if emp:  # a known employee: link the task to them (their email is used)
                org.set_task_assignee(task_id, emp["id"])
            else:    # someone not in the list: keep the email itself
                fields["assignee_email"] = email
                with db.connect() as c:
                    c.execute("UPDATE tasks SET assignee_id = NULL, assignee = ? WHERE id = ?", (email, task_id))
        else:
            fields["assignee_email"] = None
    if body.description is not None:
        fields["description"] = _check_desc(body.description)
    if body.set_due_date:
        fields["due_date"] = _check_due(body.due_date)
    if body.approved is not None:
        fields["approved"] = body.approved
    elif pm_edit and not task["approved_at"]:
        fields["approved"] = True  # the PM touched it: "Analyze again" keeps it
    if fields:
        db.edit_task(task_id, **fields)
    if body.status is not None:
        if body.status not in ("open", "done"):
            raise HTTPException(400, "The status must be 'open' or 'done'.")
        if body.status == "open" and task["status"] == "done" and task["jira_key"]:
            memory.reopen(task_id, "Reopened from AnchorzUp Meeting Agent.")  # moved out of Done in Jira too
        else:
            db.set_task_status(task_id, body.status)
    if body.set_assignee:
        try:
            org.set_task_assignee(task_id, body.assignee_id)
        except org.OrgError as e:
            raise HTTPException(404, str(e))
    return db.get_task(task_id)


@app.post("/api/meetings/{meeting_id}/tasks")
def add_task(meeting_id: int, body: TaskIn, user=User):
    m = auth.check_meeting(user, meeting_id, manage=True)
    if m.get("kind") == "standup":
        raise HTTPException(400, "A daily standup is a report: it has no tasks or tickets.")
    task_id = db.add_task(meeting_id, _check_desc(body.description), _check_due(body.due_date))
    if body.assignee_id:
        try:
            org.set_task_assignee(task_id, body.assignee_id)
        except org.OrgError as e:
            db.delete_task(task_id)
            raise HTTPException(404, str(e))
    return db.get_task(task_id)


@app.delete("/api/tasks/{task_id}")
def remove_task(task_id: int, request: Request, user=User):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found.")
    auth.check_meeting(user, task["meeting_id"], manage=True)
    if task["jira_key"]:
        raise HTTPException(409, f"This task was sent to Jira ({task['jira_key']}). Delete it there.")
    db.delete_task(task_id)
    security.log("task.delete", request, user, target=task_id, detail=task["description"])
    return {"ok": True}


def _manager_meeting_ids(user):
    """Meetings whose tickets this user reviews: all for admins, their projects' for PMs."""
    if user["is_admin"]:
        return None
    if not user["is_pm"]:
        raise HTTPException(403, "Tickets are reviewed by PMs and administrators.")
    return auth.task_scope(user)[0]


# ---------- Daily standups ----------

def _standup_scope(user):
    """Standup reports are for PMs and administrators (like tasks)."""
    if not auth.sees_tasks(user):
        raise HTTPException(403, "Standup reports are for PMs and administrators.")
    return auth.visible_meeting_ids(user)


@app.get("/api/standups")
def standups_list(user=User):
    return standup.list_standups(_standup_scope(user))


@app.get("/api/standups/people")
def standups_people(days: int = 14, project_id: int | None = None, user=User):
    """How work goes for each person over the last days of standups."""
    return standup.by_person(_standup_scope(user), max(1, min(days, 90)), project_id)


@app.get("/api/standups/{meeting_id}")
def standup_detail(meeting_id: int, user=User):
    scope = _standup_scope(user)
    m = db.get_meeting(meeting_id)
    if not m or m.get("kind") != "standup" or (scope is not None and meeting_id not in scope):
        raise HTTPException(404, "Standup not found.")
    m.pop("transcript", None)
    m.pop("file_path", None)
    m["can_manage"] = auth.can_manage_meeting(user, m)
    m["updates"] = standup.updates(meeting_id)
    return m


# ---------- Needs an owner ----------

class OwnerAssign(BaseModel):
    employee_id: int
    due_date: str | None = None


class OwnerDismiss(BaseModel):
    note: str | None = None


def _owner_issue(issue_id, user):
    """The issue, if this user manages its meeting."""
    u = owners.get(issue_id)
    allowed = _manager_meeting_ids(user)
    if not u or (allowed is not None and u["meeting_id"] not in allowed):
        raise HTTPException(404, "Item not found.")
    return u


@app.get("/api/owners")
def owners_list(user=User):
    """Work discussed in meetings that nobody was given: AI-found issues + tasks without an assignee."""
    return owners.open_items(_manager_meeting_ids(user))


@app.post("/api/owners/{issue_id}/assign")
def owners_assign(issue_id: int, body: OwnerAssign, user=User):
    _owner_issue(issue_id, user)
    try:
        task_id = owners.assign(issue_id, body.employee_id, _check_due(body.due_date or None), user["id"])
    except owners.OwnersError as e:
        raise HTTPException(409, str(e))
    return db.get_task(task_id)


@app.post("/api/owners/{issue_id}/dismiss")
def owners_dismiss(issue_id: int, body: OwnerDismiss, user=User):
    _owner_issue(issue_id, user)
    try:
        owners.dismiss(issue_id, user["id"], body.note)
    except owners.OwnersError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/owners/{issue_id}/restore")
def owners_restore(issue_id: int, user=User):
    _owner_issue(issue_id, user)
    owners.restore(issue_id)
    return {"ok": True}


@app.get("/api/tickets")
def tickets(user=User):
    jira.sync_status()  # statuses changed in Jira (at most once a minute)
    return db.list_tickets(_manager_meeting_ids(user))


class FromTasks(BaseModel):
    task_ids: list[int]
    ai: bool = True  # let the AI write the title and description


@app.post("/api/tickets/from-tasks")
def ticket_from_tasks(body: FromTasks, user=User):
    """One ticket from one or more tasks. It waits in "To send" on the Tickets page."""
    allowed = _manager_meeting_ids(user)
    ids = list(dict.fromkeys(body.task_ids))[:30]
    if not ids:
        raise HTTPException(400, "Select at least one task.")
    for tid in ids:
        t = db.get_task(tid)
        if not t or (allowed is not None and t["meeting_id"] not in allowed):
            raise HTTPException(404, "Task not found.")
        if t["merged_from"]:
            raise HTTPException(400, "That is already a ticket.")
        if t["merged_into"] or t["is_ticket"]:
            raise HTTPException(409, f"“{t['description'][:80]}” is already part of a ticket.")
    ticket_id, ai_used = ticketing.make_ticket(ids, body.ai)
    return {"ticket": db.get_task(ticket_id), "ai": ai_used}


class ManualTicket(BaseModel):
    title: str
    details: str | None = None
    assignee_id: int | None = None      # an employee from Settings → Team
    assignee_email: str | None = None   # or any work email
    due_date: str | None = None
    project_id: int | None = None
    jira_project: str | None = None
    force: bool = False                 # create it even if similar open work exists
    priority: str | None = None         # high | medium | low ; empty = the AI suggests one
    estimate_hours: float | None = None  # empty = the AI suggests one


@app.post("/api/tickets/manual")
def ticket_manual(body: ManualTicket, user=User):
    """A ticket the PM writes by hand, without a meeting. It waits in "To send" on the Tickets page."""
    _manager_meeting_ids(user)  # PMs and admins only
    if not user["is_admin"] and body.project_id not in user["pm_project_ids"]:
        raise HTTPException(403, "Choose one of your projects.")
    title = " ".join(body.title.split())[:250]
    if not title:
        raise HTTPException(400, "Write a title.")
    if body.project_id is not None:
        with db.connect() as c:
            if not c.execute("SELECT 1 FROM projects WHERE id = ?", (body.project_id,)).fetchone():
                raise HTTPException(404, "Project not found.")
    key = (body.jira_project or "").strip().upper() or None
    if key and not org.JIRA_KEY_RE.match(key):
        raise HTTPException(400, "The Jira project must look like PAY or HR2 (capital letters).")
    assignee_id, assignee, email = None, None, None
    if body.assignee_id:
        emp = next((e for e in org.overview()["employees"] if e["id"] == body.assignee_id), None)
        if not emp:
            raise HTTPException(404, "Employee not found.")
        assignee_id, assignee = emp["id"], emp["full_name"]
    elif (body.assignee_email or "").strip():
        email = body.assignee_email.strip().lower()
        if not org.EMAIL_RE.match(email):
            raise HTTPException(400, "The email is not valid.")
        if not auth.allowed_domain(email):
            raise HTTPException(400, "Only the company’s work emails are allowed.")
        emp = org.employee_by_email(email)
        if emp:
            assignee_id, assignee, email = emp["id"], emp["full_name"], None
        else:
            assignee = email
    if not body.force:
        # Same work already open (a ticket or a task from a meeting)? Ask before making a copy.
        with db.connect() as c:
            open_work = db.rows(c.execute(
                "SELECT t.id, t.description, t.is_ticket, t.jira_key, COALESCE(e.full_name, t.assignee) AS who "
                "FROM tasks t JOIN meetings m ON m.id = t.meeting_id LEFT JOIN employees e ON e.id = t.assignee_id "
                "WHERE t.status = 'open' AND t.merged_into IS NULL AND (? IS NULL OR m.project_id = ?) "
                "ORDER BY t.is_ticket DESC, t.id DESC LIMIT 500", (body.project_id, body.project_id)))
        match = dedupe.best_match(title, open_work, assignee)
        if match:
            return {"duplicate": match}
        # Finished before as a ticket? Offer to reopen it instead of making a second one.
        done = dedupe.best_match(title, memory.closed_tickets(project_id=body.project_id), assignee)
        if done:
            return {"reopen": done}
    details = (body.details or "").strip()[:4000] or None
    priority = db.check_priority(body.priority)
    if (body.priority or "").strip() and not priority:
        raise HTTPException(400, "The priority must be high, medium or low.")
    est = {"priority": priority, "priority_reason": None, "estimate_hours": db.check_estimate(body.estimate_hours)}
    if not priority or est["estimate_hours"] is None:
        guess = ticketing.suggest(title, details) or {}  # the AI fills what the PM left empty
        if not priority and guess.get("priority"):
            est["priority"], est["priority_reason"] = guess["priority"], guess.get("priority_reason")
        if est["estimate_hours"] is None:
            est["estimate_hours"] = db.check_estimate(guess.get("estimate_hours"))
    holder = db.manual_holder(body.project_id, user["id"])
    ticket_id = db.create_manual_ticket(holder, title, details, assignee_id,
                                        assignee, _check_due(body.due_date or None), key, email, **est)
    return {"ticket": db.get_task(ticket_id)}


@app.post("/api/tickets/{ticket_id}/reopen")
def reopen_ticket(ticket_id: int, request: Request, user=User):
    """The same work again: open the finished ticket again (in Jira too) instead of a new ticket."""
    allowed = _manager_meeting_ids(user)
    t = db.get_task(ticket_id)
    if not t or (allowed is not None and t["meeting_id"] not in allowed):
        raise HTTPException(404, "Ticket not found.")
    error = memory.reopen(ticket_id, "Reopened by AnchorzUp Meeting Agent: the same work is needed again.")
    security.log("jira.reopen", request, user, target=t.get("jira_key") or ticket_id, detail=t["description"])
    return {"ticket": db.get_task(ticket_id), "jira_error": error}


@app.delete("/api/tickets/{ticket_id}")
def delete_ticket(ticket_id: int, request: Request, user=User):
    """Remove a ticket that wasn't sent; its tasks stay on the Tasks page."""
    allowed = _manager_meeting_ids(user)
    t = db.get_task(ticket_id)
    if not t or not t["is_ticket"] or (allowed is not None and t["meeting_id"] not in allowed):
        raise HTTPException(404, "Ticket not found.")
    if t["jira_key"]:
        raise HTTPException(409, f"This ticket was sent to Jira ({t['jira_key']}). Delete it there.")
    db.remove_ticket(ticket_id)
    security.log("ticket.delete", request, user, target=ticket_id, detail=t["description"])
    return {"ok": True}


class TicketsSend(BaseModel):
    task_ids: list[int]


@app.post("/api/tickets/send")
def send_tickets(body: TicketsSend, request: Request, user=User):
    allowed = _manager_meeting_ids(user)
    ids = list(dict.fromkeys(body.task_ids))[:200]
    if not ids:
        raise HTTPException(400, "Select at least one ticket.")
    for tid in ids:
        t = db.get_task(tid)
        if not t or (allowed is not None and t["meeting_id"] not in allowed):
            raise HTTPException(404, "Ticket not found.")
    try:
        r = jira.push_tasks(ids, user["id"])
    except jira.JiraError as e:
        security.log("jira.send_failed", request, user, target=",".join(map(str, ids)), detail=str(e))
        raise HTTPException(400, str(e))
    security.log("jira.send", request, user, target=",".join(map(str, ids)),
                 detail=f"created {', '.join(r['created']) or 'none'}; {len(r['errors'])} not sent")
    return r


@app.post("/api/meetings/{meeting_id}/approve")
def approve_tasks(meeting_id: int, user=User):
    auth.check_meeting(user, meeting_id, manage=True)
    return {"approved": db.approve_all(meeting_id)}


@app.get("/api/report")
def report(user=User):
    return db.report(date.today().isoformat(), auth.task_scope(user))


# ---------- Employees, teams, projects ----------

class EmployeeIn(BaseModel):
    full_name: str
    email: str | None = None
    role: str | None = None
    aliases: str | None = None
    active: bool = True
    is_admin: bool = False
    team_ids: list[int] = []
    project_ids: list[int] = []
    voice_consent: bool | None = None  # the person agreed to be recognized by voice (None = unchanged)


class GroupIn(BaseModel):
    name: str
    description: str | None = None
    team_id: int | None = None   # projects only
    pm_id: int | None = None     # projects only
    jira_key: str | None = None  # projects only
    member_ids: list[int] = []


class CsvIn(BaseModel):
    csv: str


def _org_call(fn, *args):
    try:
        return fn(*args)
    except org.OrgError as e:
        raise HTTPException(400, str(e))


@app.get("/api/org")
def org_overview(user=User):
    data = org.overview()
    if user["is_admin"]:
        voice = voices.summary()  # consent date and how many meetings the voice was learned from
        for e in data["employees"]:
            e.update(voice.get(e["id"], {"voice_consent_at": None, "voice_samples": 0}))
        return data
    # Others see only their own teams/projects and the people in them
    projects = [p for p in data["projects"] if p["id"] in user["project_ids"] or p["id"] in user["pm_project_ids"]]
    teams = [t for t in data["teams"] if t["id"] in user["team_ids"]]
    people = {user["id"]} | {m for p in projects for m in p["member_ids"]}
    return {"employees": [e for e in data["employees"] if e["id"] in people], "teams": teams, "projects": projects}


@app.post("/api/employees")
def add_employee(body: EmployeeIn, request: Request, user=User):
    auth.require_admin(user)
    data = body.model_dump()
    data.pop("is_admin")  # participants never get system access
    data.pop("voice_consent")
    new_id = _org_call(org.save_employee, data)
    security.log("employee.add", request, user, target=new_id, detail=f"{data['full_name']} <{data.get('email') or '-'}>")
    _voice_consent(new_id, body.voice_consent, request, user)
    return {"id": new_id}


@app.put("/api/employees/{employee_id}")
def edit_employee(employee_id: int, body: EmployeeIn, request: Request, user=User):
    auth.require_admin(user)
    data = body.model_dump()
    data.pop("is_admin")  # roles are fixed: only the owner uses the system
    data.pop("voice_consent")
    r = _org_call(org.save_employee, data, employee_id)
    security.log("employee.edit", request, user, target=employee_id,
                 detail=f"{data['full_name']} <{data.get('email') or '-'}>, active={data.get('active')}")
    _voice_consent(employee_id, body.voice_consent, request, user)
    return {"id": r}


def _voice_consent(employee_id, consent, request, user):
    if consent is None:
        return
    change = voices.set_consent(employee_id, consent, user["id"])
    if change:
        security.log(f"voice.consent_{change}", request, user, target=employee_id,
                     detail="voice profile deleted" if change == "withdrawn" else None)


@app.post("/api/employees/{employee_id}/voice")
async def enroll_voice(employee_id: int, request: Request, file: UploadFile = File(...), user=User):
    """A short sample of the person speaking alone: their name is then put on their voice from
    the first meeting. Only the fingerprint is kept; the recording is deleted right away."""
    auth.require_admin(user)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(400, "Unknown format. Use mp3, wav, m4a, webm, ogg, mp4, flac or aac.")
    with db.connect() as c:
        emp = c.execute("SELECT full_name, voice_consent_at FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if not emp:
        raise HTTPException(404, "Employee not found.")
    if not emp["voice_consent_at"]:
        raise HTTPException(400, "Tick “Agreed to be recognized by voice” and save first.")
    dest = UPLOAD_DIR / f"voice-{uuid.uuid4().hex}{ext}"
    try:
        size = 0
        with open(dest, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise HTTPException(413, "The sample is too large: 20-60 seconds is enough.")
                out.write(chunk)
        try:
            vec, seconds = await run_in_threadpool(voices.sample_fingerprint, dest)
            samples = voices.enroll(employee_id, vec)
        except voices.VoiceError as e:
            raise HTTPException(400, str(e))
    finally:
        dest.unlink(missing_ok=True)  # the recording is never kept
    security.log("voice.enrolled", request, user, target=employee_id, detail=f"{emp['full_name']}, {seconds:.0f}s sample")
    return {"ok": True, "seconds": round(seconds), "samples": samples}


@app.post("/api/voices/test")
async def test_voice(request: Request, file: UploadFile = File(...), user=User):
    """Record someone for a few seconds: who does the system think it is? Nothing is stored."""
    auth.require_admin(user)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(400, "Unknown format. Use mp3, wav, m4a, webm, ogg, mp4, flac or aac.")
    dest = UPLOAD_DIR / f"voicetest-{uuid.uuid4().hex}{ext}"
    try:
        size = 0
        with open(dest, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise HTTPException(413, "The recording is too large: 10-20 seconds is enough.")
                out.write(chunk)
        try:
            vec, seconds = await run_in_threadpool(voices.sample_fingerprint, dest, 4)
        except voices.VoiceError as e:
            raise HTTPException(400, str(e))
    finally:
        dest.unlink(missing_ok=True)  # the recording is never kept
    r = voices.test(vec)
    security.log("voice.test", request, user, detail=f"result: {r['name'] or 'not recognized'}")
    return {**r, "seconds": round(seconds)}


@app.delete("/api/employees/{employee_id}/voice")
def delete_voice(employee_id: int, request: Request, user=User):
    """Delete the person's voice profile (it is learned again only from new namings)."""
    auth.require_admin(user)
    if voices.delete_profile(employee_id):
        security.log("voice.profile_delete", request, user, target=employee_id)
    return {"ok": True}


class SpeakerIn(BaseModel):
    employee_id: int | None = None  # None = unknown again ("Folësi N")
    name: str | None = Field(None, max_length=100)  # someone not on the team: just a name, no voice learned


@app.put("/api/meetings/{meeting_id}/speakers/{label}")
def name_speaker(meeting_id: int, label: str, body: SpeakerIn, request: Request, user=User):
    """The PM says who a speaker is. If the person agreed, their voice is learned from this meeting."""
    m = auth.check_meeting(user, meeting_id, manage=True)
    if m["status"] in BUSY:
        raise HTTPException(409, "Wait until processing finishes.")
    try:
        r = voices.name_speaker(meeting_id, label, body.employee_id, name=body.name)
    except voices.VoiceError as e:
        raise HTTPException(400, str(e))
    security.log("voice.speaker_named", request, user, target=f"{meeting_id}:{label}",
                 detail=f"{r['name']}{' (voice learned)' if r['learned'] else ''}")
    return r


@app.delete("/api/employees/{employee_id}")
def remove_employee(employee_id: int, request: Request, user=User):
    auth.require_admin(user)
    if employee_id == user["id"]:
        raise HTTPException(400, "S’mund ta fshish veten.")
    org.delete_employee(employee_id)
    security.log("employee.delete", request, user, target=employee_id)
    return {"ok": True}


@app.post("/api/employees/import")
def import_employees(body: CsvIn, request: Request, user=User):
    auth.require_admin(user)
    if len(body.csv) > 500_000:
        raise HTTPException(413, "The list is too large.")
    r = org.import_csv(body.csv)
    security.log("employee.import", request, user, detail=str(r)[:300])
    return r


@app.post("/api/{kind}s")
def add_group(kind: str, body: GroupIn, request: Request, user=User):
    if kind not in ("team", "project"):
        raise HTTPException(404)
    auth.require_admin(user)
    new_id = _org_call(org.save_group, kind, body.model_dump())
    security.log(f"{kind}.add", request, user, target=new_id, detail=f"{body.name}, PM={body.pm_id}, Jira={body.jira_key}")
    return {"id": new_id}


@app.put("/api/{kind}s/{group_id}")
def edit_group(kind: str, group_id: int, body: GroupIn, request: Request, user=User):
    if kind not in ("team", "project"):
        raise HTTPException(404)
    auth.require_admin(user)
    r = _org_call(org.save_group, kind, body.model_dump(), group_id)
    security.log(f"{kind}.edit", request, user, target=group_id, detail=f"{body.name}, PM={body.pm_id}, Jira={body.jira_key}")
    return {"id": r}


@app.delete("/api/{kind}s/{group_id}")
def remove_group(kind: str, group_id: int, request: Request, user=User):
    if kind not in ("team", "project"):
        raise HTTPException(404)
    auth.require_admin(user)
    org.delete_group(kind, group_id)
    security.log(f"{kind}.delete", request, user, target=group_id)
    return {"ok": True}


# ---------- Notifications ----------

@app.get("/api/audit")
def audit_log(action: str | None = None, user=User):
    """Who did what (sign-ins, Jira, deletions, people and permissions). Admins only."""
    auth.require_admin(user)
    return security.entries(action=action)


@app.get("/api/outbox")
def outbox(user=User):
    auth.require_admin(user)
    return mailer.outbox()


@app.post("/api/reminders/run")
def run_reminders(user=User):
    """Send today's reminders now (they are sent only once per PM per day)."""
    auth.require_admin(user)
    people, pms = deadlines.run()
    return {"sent": notify.send_daily_reminders() + people + pms}


# ---------- Weekly report ----------

def _weekly_scope(user, project_id=None):
    """Projects in this user's weekly report: admin = all (or one), PM = theirs."""
    if not user["is_admin"] and not user["is_pm"]:
        raise HTTPException(403, "The weekly report is for PMs and administrators.")
    mine = None if user["is_admin"] else list(user["pm_project_ids"])
    if project_id:
        if mine is not None and project_id not in mine:
            raise HTTPException(403, "Choose one of your projects.")
        return [project_id]
    return mine


def _week_param(week):
    try:
        return date.fromisoformat(week) if week else date.today()
    except ValueError:
        raise HTTPException(400, "The week must be a date like 2026-11-09.")


@app.get("/api/weekly")
def weekly_report(week: str | None = None, project_id: int | None = None, period: str = "week", user=User):
    jira.sync_status()  # tickets closed in Jira count as finished
    r = weekly.build(_weekly_scope(user, project_id), _week_param(week), period=period)
    day, hour = weekly.schedule()
    r["schedule"] = (f"on the 1st of each month at {int(os.environ.get('MONTHLY_REPORT_HOUR') or '9'):02d}:00 "
                     "(for the month before)" if r["period"] == "month" else f"every {weekly.DAYS[day]} at {hour:02d}:00")
    return r


class WeeklySend(BaseModel):
    week: str | None = None
    project_id: int | None = None
    period: str = "week"


@app.post("/api/weekly/send")
def weekly_send_me(body: WeeklySend, user=User):
    """Email the report shown on the page to me."""
    scope = _weekly_scope(user, body.project_id)
    if not user.get("email"):
        raise HTTPException(400, "Your profile has no email.")
    status = weekly.send(user, scope, _week_param(body.week), body.period)
    return {"status": status, "to": user["email"]}


@app.post("/api/weekly/run")
def weekly_run(period: str = "week", user=User):
    """Send this week's report (or last month's) to every PM now (admin)."""
    auth.require_admin(user)
    return {"sent": weekly.send_all(force=True, period="month" if period == "month" else "week")}


# ---------- Ask the archive ----------

class Question(BaseModel):
    question: str
    history: list[dict] = []   # earlier questions and answers of this conversation: [{"q", "a"}]


@app.post("/api/ask")
def ask(body: Question, user=User):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Type a question.")
    try:
        history = [{"q": str(h.get("q", ""))[:1000], "a": str(h.get("a", ""))[:2000]} for h in body.history[-4:]]
        return assistant.ask(q[:1000], pipeline.has_claude_key(), auth.visible_meeting_ids(user), user["id"], history,
                             tasks=auth.sees_tasks(user))
    except assistant.llm.LLMError as e:
        raise HTTPException(502, str(e))


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index_page():
    """index.html with app.js/app.css links that change whenever those files change
    (app.js?v=…), so a browser can never keep running an old copy of the app."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "app.css"):
        v = int((STATIC_DIR / name).stat().st_mtime)
        html = html.replace(f'"/{name}"', f'"/{name}?v={v}"')
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
