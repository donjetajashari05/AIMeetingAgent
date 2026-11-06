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
from pathlib import Path

import ctranslate2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import assistant, auth, db, jira, learning, mailer, notify, org, pipeline, tickets as ticketing  # noqa: E402

UPLOAD_DIR = db.ROOT / "data" / "uploads"
STATIC_DIR = Path(__file__).resolve().parent / "static"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".mp4", ".flac", ".aac"}
MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB
MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
BUSY = ("queued", "transcribing", "diarizing", "extracting")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    org.init()
    auth.init()
    mailer.init()
    notify.init()
    learning.init()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    pipeline.start_worker()
    notify.start_scheduler()
    if not os.environ.get("APP_SECRET"):
        print("WARNING: set APP_SECRET in .env (a long random string).")
    if not auth.admin_emails():
        print("WARNING: set ADMIN_EMAILS in .env so you can sign in as administrator.")
    yield


app = FastAPI(title="AnchorzUp Meeting Agent", lifespan=lifespan)
User = Depends(auth.current_user)


# ---------- Login ----------

class EmailIn(BaseModel):
    email: str


class CodeIn(BaseModel):
    email: str
    code: str


@app.post("/api/auth/request")
def auth_request(body: EmailIn):
    if auth.login_disabled():
        raise HTTPException(404, "Sign-in is disabled: the system has a single user.")
    emp, code = auth.request_code(body.email)
    if emp:
        status = mailer.send(emp["email"], f"Your sign-in code: {code}",
                             f"Hi {emp['full_name'].split()[0]},\n\nYour code for AnchorzUp Meeting Agent is: {code}\n\n"
                             f"The code is valid for {auth.CODE_MINUTES} minutes. If you didn’t request it, ignore this email.",
                             kind="login")
        if status == "dry-run":
            # No SMTP yet: show the code only in the server's own terminal
            print(f"\n[SMTP not configured] Sign-in code for {emp['email']}: {code}\n", flush=True)
            # Mock mode (no SMTP): hand the code to the login page instead of a real email
            return {"ok": True, "message": "Email is not configured: the code was filled in automatically.", "dev_code": code}
        elif status == "failed":
            raise HTTPException(502, "The email with the code could not be sent. Check the SMTP settings in .env.")
    # Same answer whether the email exists or not
    return {"ok": True, "message": "If the email is registered, the code was sent."}


@app.post("/api/auth/verify")
def auth_verify(body: CodeIn, response: Response, request: Request):
    if auth.login_disabled():
        raise HTTPException(404, "Sign-in is disabled: the system has a single user.")
    token = auth.verify_code(body.email, body.code)
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")
    return {"ok": True}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=User):
    return {**{k: user[k] for k in ("id", "full_name", "email", "role", "is_admin", "is_pm",
                                    "pm_project_ids", "project_ids", "team_ids")},
            "login_disabled": auth.login_disabled()}


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

    kind = "json" if ext == ".json" else ("recording" if source == "recording" else "audio")
    name = title.strip() or Path(file.filename or "Meeting").stem or "Meeting"
    meeting_id = db.create_meeting(name[:200], mdate.isoformat(), kind, str(dest), model,
                                   language if language in ("sq", "en") else None,
                                   team_id or None, project_id or None, created_by=user["id"])
    pipeline.enqueue(meeting_id)
    return {"id": meeting_id}


@app.get("/api/meetings/{meeting_id}")
def meeting(meeting_id: int, user=User):
    auth.check_meeting(user, meeting_id)
    m = db.meeting_detail(meeting_id)
    m["can_manage"] = auth.can_manage_meeting(user, m)
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
def delete(meeting_id: int, user=User):
    m = auth.check_meeting(user, meeting_id, manage=True)
    if m["status"] in BUSY[1:]:
        raise HTTPException(409, "Wait until processing finishes.")
    db.delete_meeting(meeting_id)
    if m["file_path"]:
        Path(m["file_path"]).unlink(missing_ok=True)
    return {"ok": True}


# ---------- Tasks & report ----------

@app.get("/api/tasks")
def tasks(status: str | None = None, assignee_id: int | None = None, user=User):
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
    if not t:
        raise HTTPException(404, "Task not found.")
    auth.check_meeting(user, t["meeting_id"])
    return t


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate, user=User):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found.")
    ticket_edit = body.set_email or body.details is not None or body.jira_project is not None
    pm_edit = (body.set_assignee or body.description is not None or body.set_due_date
               or body.approved is not None or ticket_edit)
    if not auth.can_edit_task(user, task, reassign=pm_edit):
        raise HTTPException(403, "You are not allowed to change this task.")
    if task["jira_key"] and (body.description is not None or body.set_due_date or body.set_assignee
                             or body.approved is False or ticket_edit):
        raise HTTPException(409, f"This task was sent to Jira ({task['jira_key']}). Change it there.")
    fields = {}
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
        db.set_task_status(task_id, body.status)
    if body.set_assignee:
        try:
            org.set_task_assignee(task_id, body.assignee_id)
        except org.OrgError as e:
            raise HTTPException(404, str(e))
    return db.get_task(task_id)


@app.post("/api/meetings/{meeting_id}/tasks")
def add_task(meeting_id: int, body: TaskIn, user=User):
    auth.check_meeting(user, meeting_id, manage=True)
    task_id = db.add_task(meeting_id, _check_desc(body.description), _check_due(body.due_date))
    if body.assignee_id:
        try:
            org.set_task_assignee(task_id, body.assignee_id)
        except org.OrgError as e:
            db.delete_task(task_id)
            raise HTTPException(404, str(e))
    return db.get_task(task_id)


@app.delete("/api/tasks/{task_id}")
def remove_task(task_id: int, user=User):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found.")
    auth.check_meeting(user, task["meeting_id"], manage=True)
    if task["jira_key"]:
        raise HTTPException(409, f"This task was sent to Jira ({task['jira_key']}). Delete it there.")
    db.delete_task(task_id)
    return {"ok": True}


def _manager_meeting_ids(user):
    """Meetings whose tickets this user reviews: all for admins, their projects' for PMs."""
    if user["is_admin"]:
        return None
    if not user["is_pm"]:
        raise HTTPException(403, "Tickets are reviewed by PMs and administrators.")
    return auth.task_scope(user)[0]


@app.get("/api/tickets")
def tickets(user=User):
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


@app.delete("/api/tickets/{ticket_id}")
def delete_ticket(ticket_id: int, user=User):
    """Remove a ticket that wasn't sent; its tasks stay on the Tasks page."""
    allowed = _manager_meeting_ids(user)
    t = db.get_task(ticket_id)
    if not t or not t["is_ticket"] or (allowed is not None and t["meeting_id"] not in allowed):
        raise HTTPException(404, "Ticket not found.")
    if t["jira_key"]:
        raise HTTPException(409, f"This ticket was sent to Jira ({t['jira_key']}). Delete it there.")
    db.remove_ticket(ticket_id)
    return {"ok": True}


class TicketsSend(BaseModel):
    task_ids: list[int]


@app.post("/api/tickets/send")
def send_tickets(body: TicketsSend, user=User):
    allowed = _manager_meeting_ids(user)
    ids = list(dict.fromkeys(body.task_ids))[:200]
    if not ids:
        raise HTTPException(400, "Select at least one ticket.")
    for tid in ids:
        t = db.get_task(tid)
        if not t or (allowed is not None and t["meeting_id"] not in allowed):
            raise HTTPException(404, "Ticket not found.")
    try:
        return jira.push_tasks(ids, user["id"])
    except jira.JiraError as e:
        raise HTTPException(400, str(e))


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
        return data
    # Others see only their own teams/projects and the people in them
    projects = [p for p in data["projects"] if p["id"] in user["project_ids"] or p["id"] in user["pm_project_ids"]]
    teams = [t for t in data["teams"] if t["id"] in user["team_ids"]]
    people = {user["id"]} | {m for p in projects for m in p["member_ids"]}
    return {"employees": [e for e in data["employees"] if e["id"] in people], "teams": teams, "projects": projects}


@app.post("/api/employees")
def add_employee(body: EmployeeIn, user=User):
    auth.require_admin(user)
    data = body.model_dump()
    data.pop("is_admin")  # participants never get system access
    return {"id": _org_call(org.save_employee, data)}


@app.put("/api/employees/{employee_id}")
def edit_employee(employee_id: int, body: EmployeeIn, user=User):
    auth.require_admin(user)
    data = body.model_dump()
    data.pop("is_admin")  # roles are fixed: only the owner uses the system
    return {"id": _org_call(org.save_employee, data, employee_id)}


@app.delete("/api/employees/{employee_id}")
def remove_employee(employee_id: int, user=User):
    auth.require_admin(user)
    if employee_id == user["id"]:
        raise HTTPException(400, "S’mund ta fshish veten.")
    org.delete_employee(employee_id)
    return {"ok": True}


@app.post("/api/employees/import")
def import_employees(body: CsvIn, user=User):
    auth.require_admin(user)
    if len(body.csv) > 500_000:
        raise HTTPException(413, "The list is too large.")
    return org.import_csv(body.csv)


@app.post("/api/{kind}s")
def add_group(kind: str, body: GroupIn, user=User):
    if kind not in ("team", "project"):
        raise HTTPException(404)
    auth.require_admin(user)
    return {"id": _org_call(org.save_group, kind, body.model_dump())}


@app.put("/api/{kind}s/{group_id}")
def edit_group(kind: str, group_id: int, body: GroupIn, user=User):
    if kind not in ("team", "project"):
        raise HTTPException(404)
    auth.require_admin(user)
    return {"id": _org_call(org.save_group, kind, body.model_dump(), group_id)}


@app.delete("/api/{kind}s/{group_id}")
def remove_group(kind: str, group_id: int, user=User):
    if kind not in ("team", "project"):
        raise HTTPException(404)
    auth.require_admin(user)
    org.delete_group(kind, group_id)
    return {"ok": True}


# ---------- Notifications ----------

@app.get("/api/outbox")
def outbox(user=User):
    auth.require_admin(user)
    return mailer.outbox()


@app.post("/api/reminders/run")
def run_reminders(user=User):
    """Send today's reminders now (they are sent only once per PM per day)."""
    auth.require_admin(user)
    return {"sent": notify.send_daily_reminders()}


# ---------- Ask the archive ----------

class Question(BaseModel):
    question: str


@app.post("/api/ask")
def ask(body: Question, user=User):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Type a question.")
    try:
        return assistant.ask(q[:1000], pipeline.has_claude_key(), auth.visible_meeting_ids(user))
    except assistant.llm.LLMError as e:
        raise HTTPException(502, str(e))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
