"""
SQLite storage for meetings, decisions and tasks, plus a full-text index used
by "Ask the archive". The schema mirrors what will later move to PostgreSQL.
"""
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# MEETING_AGENT_DB lets tests use a separate database file
DB_PATH = Path(os.environ.get("MEETING_AGENT_DB") or ROOT / "data" / "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,             -- audio | recording | json
    file_path TEXT,
    model TEXT,
    language_hint TEXT,
    status TEXT NOT NULL,             -- queued | transcribing | diarizing | extracting | done | needs_key | error
    progress REAL NOT NULL DEFAULT 0,
    note TEXT,
    error TEXT,
    language TEXT,
    duration_sec REAL,
    summary TEXT,
    open_questions TEXT,              -- JSON list
    transcript TEXT                   -- JSON (output of transcribe.py)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    quote TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    assignee TEXT,
    due_date TEXT,
    due_date_text TEXT,
    quote TEXT,
    status TEXT NOT NULL DEFAULT 'open',   -- open | done
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    text,
    meeting_id UNINDEXED,
    kind UNINDEXED,       -- utterance | summary | decision | task
    start UNINDEXED,
    speaker UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def now():
    return datetime.now().isoformat(timespec="seconds")


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        # Jobs interrupted by a server restart are marked so they can be retried
        c.execute("UPDATE meetings SET status='error', error='The server stopped during processing. Try again.' "
                  "WHERE status IN ('queued','transcribing','diarizing','extracting')")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


# ---------- Meetings ----------

def create_meeting(title, meeting_date, source, file_path, model, language_hint, team_id=None, project_id=None,
                   created_by=None):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO meetings (title, meeting_date, created_at, source, file_path, model, "
            "language_hint, status, team_id, project_id, created_by) VALUES (?,?,?,?,?,?,?, 'queued', ?, ?, ?)",
            (title, meeting_date, now(), source, file_path, model, language_hint, team_id, project_id, created_by),
        )
        return cur.lastrowid


def update_meeting(meeting_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE meetings SET {cols} WHERE id = ?", (*fields.values(), meeting_id))


def get_meeting(meeting_id):
    with connect() as c:
        r = c.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return dict(r) if r else None


def _in(ids):
    """SQL placeholder list for an id set (an empty set matches nothing)."""
    return ",".join("?" * len(ids)) or "NULL"


def list_meetings(meeting_ids=None):
    """meeting_ids: None = all, otherwise only these."""
    where = "" if meeting_ids is None else f"WHERE m.id IN ({_in(meeting_ids)})"
    with connect() as c:
        return rows(c.execute(f"""
            SELECT m.id, m.title, m.meeting_date, m.created_at, m.source, m.status, m.progress,
                   m.note, m.error, m.language, m.duration_sec, m.model,
                   (SELECT COUNT(*) FROM tasks t WHERE t.meeting_id = m.id AND t.merged_from IS NULL) AS task_count,
                   (SELECT COUNT(*) FROM decisions d WHERE d.meeting_id = m.id) AS decision_count,
                   m.transcript IS NOT NULL AS has_transcript,
                   json_extract(m.transcript, '$.utterances') IS NOT NULL AS has_speakers,
                   m.team_id, m.project_id, tm.name AS team_name, pr.name AS project_name
            FROM meetings m
            LEFT JOIN teams tm ON tm.id = m.team_id
            LEFT JOIN projects pr ON pr.id = m.project_id
            {where}
            ORDER BY m.meeting_date DESC, m.id DESC
        """, tuple(meeting_ids or ())))


def delete_meeting(meeting_id):
    with connect() as c:
        c.execute("DELETE FROM chunks WHERE meeting_id = ?", (meeting_id,))
        c.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def save_transcript(meeting_id, data):
    """Store the transcript and index every line for search."""
    lines = data.get("utterances") or data.get("segments") or []
    with connect() as c:
        c.execute("UPDATE meetings SET transcript = ?, language = ?, duration_sec = ? WHERE id = ?",
                  (json.dumps(data, ensure_ascii=False), data.get("language"),
                   data.get("duration_sec"), meeting_id))
        c.execute("DELETE FROM chunks WHERE meeting_id = ? AND kind = 'utterance'", (meeting_id,))
        c.executemany(
            "INSERT INTO chunks (text, meeting_id, kind, start, speaker) VALUES (?,?, 'utterance', ?, ?)",
            [(l["text"], meeting_id, l["start"], l.get("speaker")) for l in lines if l.get("text")],
        )


def save_notes(meeting_id, notes):
    """Store the LLM output (summary, decisions, tasks) and index it."""
    with connect() as c:
        c.execute("DELETE FROM decisions WHERE meeting_id = ?", (meeting_id,))
        # Re-analysis replaces only AI drafts: tickets the PM confirmed or sent to Jira stay
        # Tasks already turned into tickets (and the tickets) stay too
        c.execute("DELETE FROM tasks WHERE meeting_id = ? AND approved_at IS NULL AND jira_key IS NULL "
                  "AND is_ticket = 0 AND merged_into IS NULL AND merged_from IS NULL", (meeting_id,))
        c.execute("DELETE FROM chunks WHERE meeting_id = ? AND kind != 'utterance'", (meeting_id,))
        c.execute("UPDATE meetings SET summary = ?, open_questions = ?, completed = ? WHERE id = ?",
                  (notes["summary"], json.dumps(notes["open_questions"], ensure_ascii=False),
                   json.dumps(notes.get("completed") or [], ensure_ascii=False), meeting_id))
        for d in notes["decisions"]:
            c.execute("INSERT INTO decisions (meeting_id, description, quote) VALUES (?,?,?)",
                      (meeting_id, d["description"], d["quote"]))
        kept = {r[0].strip().lower() for r in c.execute(
            "SELECT description FROM tasks WHERE meeting_id = ? AND merged_from IS NULL", (meeting_id,))}
        for t in notes["tasks"]:
            if t["description"].strip().lower() in kept:
                continue  # already confirmed/sent with the same title
            c.execute(
                "INSERT INTO tasks (meeting_id, description, assignee, due_date, due_date_text, quote, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (meeting_id, t["description"], t["assignee"], t["due_date"], t["due_date_text"], t["quote"], now()),
            )
        index = [(notes["summary"], meeting_id, "summary", None, None)]
        index += [(d["description"], meeting_id, "decision", None, None) for d in notes["decisions"]]
        index += [(t["description"] + (f" ({t['assignee']})" if t["assignee"] else ""), meeting_id, "task", None, None)
                  for t in notes["tasks"]]
        c.executemany("INSERT INTO chunks (text, meeting_id, kind, start, speaker) VALUES (?,?,?,?,?)", index)


def meeting_detail(meeting_id):
    m = get_meeting(meeting_id)
    if not m:
        return None
    with connect() as c:
        m["decisions"] = rows(c.execute("SELECT * FROM decisions WHERE meeting_id = ? ORDER BY id", (meeting_id,)))
        m["tasks"] = rows(c.execute(TASK_SELECT + " WHERE t.meeting_id = ? AND t.merged_from IS NULL ORDER BY t.id",
                                    (meeting_id,)))
        m["tickets"] = rows(c.execute("SELECT id, jira_key, approved_at, status FROM tasks t WHERE t.is_ticket = 1 AND "
                                      "(t.meeting_id = ? OR t.id IN (SELECT merged_into FROM tasks WHERE meeting_id = ?))",
                                      (meeting_id, meeting_id)))
        names = c.execute("SELECT (SELECT name FROM teams WHERE id = ?) AS team_name, "
                          "(SELECT name FROM projects WHERE id = ?) AS project_name",
                          (m["team_id"], m["project_id"])).fetchone()
        m.update(dict(names))
    m["transcript"] = json.loads(m["transcript"]) if m["transcript"] else None
    m["open_questions"] = json.loads(m["open_questions"]) if m["open_questions"] else []
    m["completed"] = json.loads(m["completed"]) if m.get("completed") else []
    m.pop("file_path", None)
    return m


# ---------- Tasks ----------

TASK_SELECT = """
    SELECT t.*, COALESCE(e.full_name, t.assignee) AS assignee_name,
           COALESCE(t.assignee_email, e.email) AS recipient_email,
           m.title AS meeting_title, m.meeting_date, m.project_id,
           pr.name AS project_name, COALESCE(t.jira_project, pr.jira_key) AS ticket_project,
           sb.full_name AS sent_by_name,
           tk.description AS ticket_title, tk.jira_key AS ticket_jira_key, tk.approved_at AS ticket_approved_at
    FROM tasks t
    JOIN meetings m ON m.id = t.meeting_id
    LEFT JOIN employees e ON e.id = t.assignee_id
    LEFT JOIN projects pr ON pr.id = m.project_id
    LEFT JOIN employees sb ON sb.id = t.sent_by
    LEFT JOIN tasks tk ON tk.id = t.merged_into
"""


def list_tickets(meeting_ids=None):
    """Tickets for the review page, newest first, each with the tasks it was made from.
    meeting_ids: None = all, otherwise only tickets of these meetings."""
    where = " WHERE t.is_ticket = 1" + ("" if meeting_ids is None else f" AND t.meeting_id IN ({_in(meeting_ids)})")
    with connect() as c:
        tickets = rows(c.execute(TASK_SELECT + where + " ORDER BY t.created_at DESC, t.id DESC", tuple(meeting_ids or ())))
        for t in tickets:
            ids = json.loads(t["merged_from"]) if t["merged_from"] else []
            t["sources"] = rows(c.execute(
                f"SELECT t.id, t.description, t.quote, t.meeting_id, m.title AS meeting_title, m.meeting_date "
                f"FROM tasks t JOIN meetings m ON m.id = t.meeting_id WHERE t.id IN ({_in(ids)}) ORDER BY t.id", ids))
    return tickets


def create_ticket(sources, title, details, assignee_id=None, assignee=None, due_date=None):
    """One ticket (a draft on the Tickets page) made from one or more tasks.
    The ticket belongs to the newest source meeting; the tasks point to it."""
    ids = [t["id"] for t in sources]
    home = max(sources, key=lambda t: (t["meeting_date"], t["meeting_id"]))
    quote = sources[0]["quote"] if len(sources) == 1 else None
    with connect() as c:
        ticket_id = c.execute(
            "INSERT INTO tasks (meeting_id, description, details, assignee_id, assignee, due_date, quote, created_at, "
            "is_ticket, merged_from) VALUES (?,?,?,?,?,?,?,?,1,?)",
            (home["meeting_id"], title, details, assignee_id, assignee, due_date, quote, now(), json.dumps(ids)),
        ).lastrowid
        c.execute(f"UPDATE tasks SET merged_into = ? WHERE id IN ({_in(ids)})", (ticket_id, *ids))
    return ticket_id


def remove_ticket(ticket_id):
    """Delete a ticket that wasn't sent. Its tasks become free again.
    An older ticket that is itself the task (no merged_from) goes back to being only a task."""
    with connect() as c:
        t = c.execute("SELECT merged_from FROM tasks WHERE id = ?", (ticket_id,)).fetchone()
        c.execute("UPDATE tasks SET merged_into = NULL WHERE merged_into = ?", (ticket_id,))
        if t and t["merged_from"]:
            c.execute("DELETE FROM tasks WHERE id = ?", (ticket_id,))
        else:
            c.execute("UPDATE tasks SET is_ticket = 0, approved_at = NULL WHERE id = ?", (ticket_id,))


def _scope_sql(scope):
    """scope = (meeting_ids, me) from auth.task_scope: tasks in those meetings OR assigned to me."""
    meeting_ids, me = scope or (None, None)
    if meeting_ids is None and me is None:
        return None, []
    return f"(t.meeting_id IN ({_in(meeting_ids)}) OR t.assignee_id = ?)", [*meeting_ids, me]


def list_tasks(status=None, assignee_id=None, scope=None):
    where, args = [], []
    sql, a = _scope_sql(scope)
    if sql:
        where.append(sql)
        args += a
    where.append("t.merged_from IS NULL")  # tickets made from tasks are listed on the Tickets page
    if status:
        where.append("t.status = ?")
        args.append(status)
    if assignee_id:
        where.append("t.assignee_id = ?")
        args.append(assignee_id)
    sql = TASK_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY t.status = 'done', t.due_date IS NULL, t.due_date, t.id"
    with connect() as c:
        return rows(c.execute(sql, args))


def get_task(task_id):
    with connect() as c:
        r = c.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
        return dict(r) if r else None


def edit_task(task_id, **fields):
    """PM edits: description, due_date, approved (bool), assignee_email, details, jira_project."""
    sets, args = [], []
    for col in ("assignee_email", "details", "jira_project"):
        if col in fields:
            sets.append(f"{col} = ?")
            args.append(fields[col])
    if "description" in fields:
        sets.append("description = ?")
        args.append(fields["description"])
    if "due_date" in fields:
        sets += ["due_date = ?", "due_date_text = NULL"]
        args.append(fields["due_date"])
    if "approved" in fields:
        sets.append("approved_at = ?")
        args.append(now() if fields["approved"] else None)
    if sets:
        with connect() as c:
            c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", (*args, task_id))
    return get_task(task_id)


def add_task(meeting_id, description, due_date=None):
    """A task the PM adds by hand: it is confirmed from the start."""
    with connect() as c:
        return c.execute("INSERT INTO tasks (meeting_id, description, due_date, quote, created_at, approved_at) "
                         "VALUES (?,?,?,NULL,?,?)", (meeting_id, description, due_date, now(), now())).lastrowid


def delete_task(task_id):
    with connect() as c:
        c.execute("UPDATE tasks SET merged_into = NULL WHERE merged_into = ?", (task_id,))
        c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


def approve_all(meeting_id):
    with connect() as c:
        return c.execute("UPDATE tasks SET approved_at = ? WHERE meeting_id = ? AND approved_at IS NULL AND jira_key IS NULL "
                         "AND merged_from IS NULL",
                         (now(), meeting_id)).rowcount


def set_task_status(task_id, status):
    with connect() as c:
        c.execute("UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
                  (status, now() if status == "done" else None, task_id))
    return get_task(task_id)


def report(today, scope=None):
    """Status of the (visible) tasks from the most recent meeting that produced them."""
    sql, args = _scope_sql(scope)
    cond = f" AND {sql}" if sql else ""
    with connect() as c:
        m = c.execute(f"""
            SELECT m.id, m.title, m.meeting_date FROM meetings m
            WHERE EXISTS (SELECT 1 FROM tasks t WHERE t.meeting_id = m.id AND t.merged_from IS NULL{cond})
            ORDER BY m.meeting_date DESC, m.id DESC LIMIT 1
        """, args).fetchone()
        if not m:
            return None
        tasks = rows(c.execute(TASK_SELECT + f" WHERE t.meeting_id = ? AND t.merged_from IS NULL{cond} ORDER BY t.due_date",
                               (m["id"], *args)))
    done = [t for t in tasks if t["status"] == "done"]
    late = [t for t in tasks if t["status"] != "done" and t["due_date"] and t["due_date"] < today]
    open_ = [t for t in tasks if t["status"] != "done" and t not in late]
    return {"meeting": dict(m), "done": len(done), "late": len(late), "open": len(open_), "late_tasks": late}


# ---------- Search ----------

def _fts_query(question):
    """Build a forgiving FTS query: prefix-match each word so Albanian endings
    (buxhetin / buxheti / buxhetit) still match."""
    words = re.findall(r"\w+", question.lower())
    terms = []
    for w in words:
        if len(w) < 3:
            continue
        stem = w[: max(4, len(w) - 3)] if len(w) > 5 else w
        terms.append(f'"{stem}"*')
    return " OR ".join(terms)


def search(question, limit=8, meeting_ids=None):
    """meeting_ids: None = search everything, otherwise only these meetings."""
    q = _fts_query(question)
    if not q:
        return []
    only = "" if meeting_ids is None else f" AND m.id IN ({_in(meeting_ids)})"
    with connect() as c:
        return rows(c.execute(f"""
            SELECT ch.text, ch.kind, ch.start, ch.speaker, ch.meeting_id,
                   m.title AS meeting_title, m.meeting_date
            FROM chunks ch JOIN meetings m ON m.id = ch.meeting_id
            WHERE chunks MATCH ?{only} ORDER BY bm25(chunks) LIMIT ?
        """, (q, *(meeting_ids or ()), limit)))
