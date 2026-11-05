"""
AnchorzUp Meeting Agent — web app.

Run (from the project root, with the venv active):
    python -m app
Then open http://127.0.0.1:8000
"""
import mimetypes
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import anthropic
import ctranslate2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import assistant, auth, db, jira, mailer, notify, org, pipeline  # noqa: E402

UPLOAD_DIR = db.ROOT / "data" / "uploads"
STATIC_DIR = Path(__file__).resolve().parent / "static"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".mp4", ".flac", ".aac"}
MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB
MODELS = ["tiny", "base", "small", "medium", "large-v3"]
BUSY = ("queued", "transcribing", "diarizing", "extracting")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    org.init()
    auth.init()
    mailer.init()
    notify.init()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    pipeline.start_worker()
    notify.start_scheduler()
    if not os.environ.get("APP_SECRET"):
        print("KUJDES: vendos APP_SECRET në .env (një varg i gjatë i rastësishëm).")
    if not auth.admin_emails():
        print("KUJDES: vendos ADMIN_EMAILS në .env që të mund të hysh si administrator.")
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
        raise HTTPException(404, "Hyrja është e çaktivizuar: sistemi ka vetëm një përdorues.")
    emp, code = auth.request_code(body.email)
    if emp:
        status = mailer.send(emp["email"], f"Kodi i hyrjes: {code}",
                             f"Përshëndetje {emp['full_name'].split()[0]},\n\nKodi yt për AnchorzUp Meeting Agent është: {code}\n\n"
                             f"Kodi vlen {auth.CODE_MINUTES} minuta. Nëse s’e ke kërkuar ti, injoroje këtë email.",
                             kind="login")
        if status == "dry-run":
            # No SMTP yet: show the code only in the server's own terminal
            print(f"\n[SMTP s'është konfiguruar] Kodi i hyrjes për {emp['email']}: {code}\n", flush=True)
            # Mock mode (no SMTP): hand the code to the login page instead of a real email
            return {"ok": True, "message": "Emaili nuk është konfiguruar: kodi u plotësua automatikisht.", "dev_code": code}
        elif status == "failed":
            raise HTTPException(502, "Emaili me kod nuk u dërgua. Kontrollo cilësimet SMTP në .env.")
    # Same answer whether the email exists or not
    return {"ok": True, "message": "Nëse emaili është i regjistruar, kodi u dërgua."}


@app.post("/api/auth/verify")
def auth_verify(body: CodeIn, response: Response, request: Request):
    if auth.login_disabled():
        raise HTTPException(404, "Hyrja është e çaktivizuar: sistemi ka vetëm një përdorues.")
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
        "claude": pipeline.has_claude_key(),
        "speakers": pipeline.has_hf_token(),
        "email": mailer.configured(),
        "jira": jira.configured(),
        "jira_mock": jira.mock(),
        "jira_url": os.environ.get("JIRA_BASE_URL", "").rstrip("/") if jira.configured() and not jira.mock() else None,
        "gpu": gpu,
        "models": MODELS,
        "default_model": "large-v3" if gpu else "small",
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
    model: str = Form("small"),
    source: str = Form("audio"),
    team_id: int | None = Form(None),
    project_id: int | None = Form(None),
    user=User,
):
    # Admins add any meeting; a PM only for the projects they manage
    if not user["is_admin"]:
        if not user["is_pm"]:
            raise HTTPException(403, "Vetëm PM-të dhe administratorët shtojnë mbledhje.")
        if project_id not in user["pm_project_ids"]:
            raise HTTPException(403, "Zgjidh një nga projektet që menaxhon.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT | {".json"}:
        raise HTTPException(400, "Format i panjohur. Përdor mp3, wav, m4a, webm, ogg, mp4, flac ose json.")
    if model not in MODELS:
        raise HTTPException(400, "Model i panjohur.")
    try:
        mdate = date.fromisoformat(meeting_date) if meeting_date else date.today()
    except ValueError:
        raise HTTPException(400, "Data duhet të jetë në formatin YYYY-MM-DD.")
    if project_id:  # the project decides the team
        with db.connect() as c:
            row = c.execute("SELECT team_id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(400, "Projekti nuk u gjet.")
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
                raise HTTPException(413, "Skedari është më i madh se 1 GB.")
            out.write(chunk)

    kind = "json" if ext == ".json" else ("recording" if source == "recording" else "audio")
    name = title.strip() or Path(file.filename or "Mbledhje").stem or "Mbledhje"
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
        raise HTTPException(404, "S'ka audio për këtë mbledhje.")
    path = Path(m["file_path"])
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@app.post("/api/meetings/{meeting_id}/retry")
def retry(meeting_id: int, user=User):
    m = auth.check_meeting(user, meeting_id, manage=True)
    if m["status"] in BUSY:
        raise HTTPException(409, "Mbledhja po përpunohet.")
    with db.connect() as c:
        sent = c.execute("SELECT COUNT(*) FROM tasks WHERE meeting_id = ? AND jira_key IS NOT NULL", (meeting_id,)).fetchone()[0]
    if sent:
        raise HTTPException(409, f"{sent} detyra janë dërguar tashmë në Jira; analiza e re do t’i zëvendësonte. Ndryshoji detyrat me dorë.")
    # With a transcript we only redo the extraction; otherwise start from scratch
    job = "extract" if m["transcript"] else "process"
    db.update_meeting(meeting_id, status="queued", error=None, note=None)
    pipeline.enqueue(meeting_id, job)
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
        raise HTTPException(409, "Prit sa të përfundojë përpunimi.")
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
        raise HTTPException(400, "Afati duhet të jetë në formatin YYYY-MM-DD.")


def _check_desc(value):
    value = (value or "").strip()
    if not value:
        raise HTTPException(400, "Shkruaj përshkrimin e detyrës.")
    return value[:1000]


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate, user=User):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Detyra nuk u gjet.")
    pm_edit = body.set_assignee or body.description is not None or body.set_due_date or body.approved is not None
    if not auth.can_edit_task(user, task, reassign=pm_edit):
        raise HTTPException(403, "Nuk ke leje ta ndryshosh këtë detyrë.")
    if task["jira_key"] and (body.description is not None or body.set_due_date or body.set_assignee or body.approved is False):
        raise HTTPException(409, f"Detyra është dërguar në Jira ({task['jira_key']}). Ndryshoje atje.")
    fields = {}
    if body.description is not None:
        fields["description"] = _check_desc(body.description)
    if body.set_due_date:
        fields["due_date"] = _check_due(body.due_date)
    if body.approved is not None:
        fields["approved"] = body.approved
    if fields:
        db.edit_task(task_id, **fields)
    if body.status is not None:
        if body.status not in ("open", "done"):
            raise HTTPException(400, "Statusi duhet të jetë 'open' ose 'done'.")
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
        raise HTTPException(404, "Detyra nuk u gjet.")
    auth.check_meeting(user, task["meeting_id"], manage=True)
    if task["jira_key"]:
        raise HTTPException(409, f"Detyra është dërguar në Jira ({task['jira_key']}). Fshije atje.")
    db.delete_task(task_id)
    return {"ok": True}


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
        raise HTTPException(413, "Lista është shumë e madhe.")
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
        raise HTTPException(400, "Shkruaj një pyetje.")
    try:
        return assistant.ask(q[:1000], pipeline.has_claude_key(), auth.visible_meeting_ids(user))
    except anthropic.AuthenticationError:
        raise HTTPException(502, "ANTHROPIC_API_KEY nuk është i vlefshëm.")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "S'u arrit lidhja me Claude. Kontrollo internetin.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Gabim nga Claude API: {e.message}")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
