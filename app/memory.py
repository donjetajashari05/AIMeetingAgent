"""
Long-term memory across meetings.

Before a meeting is analyzed, the agent gets what it should remember from earlier meetings
of the same project (or team, or all meetings when neither is set):
  - the work that is still open (tasks and tickets, with who, due date and Jira key)
  - recent decisions and short summaries of the last meetings
The AI then reports which earlier items were finished or moved forward in this meeting,
and links new tasks to earlier ones instead of creating duplicates.

Results are kept in `task_updates` (shown on the meeting page under "Earlier work").
An earlier task the AI marks as done is closed automatically; "Reopen" undoes it, and
"Analyze again" first undoes what this meeting closed, then decides again.
When work that was already finished as a ticket comes up again, that ticket is reopened
(in Jira too) instead of a second ticket being made. "Analyze again" leaves it open.
"""
from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    status TEXT NOT NULL,      -- done | in_progress | blocked | mentioned
    note TEXT,
    quote TEXT,
    closed_task INTEGER NOT NULL DEFAULT 0,   -- 1 = this update closed the task automatically
    created_at TEXT NOT NULL
);
"""

MAX_OPEN = 40        # open items given to the AI
MAX_DECISIONS = 15
MAX_SUMMARIES = 3
DAYS_BACK = 120


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def _scope(m):
    """SQL filter for the earlier meetings that matter to meeting m."""
    if m.get("project_id"):
        return "pm.project_id = ?", (m["project_id"],)
    if m.get("team_id"):
        return "pm.team_id = ?", (m["team_id"],)
    return "1 = 1", ()


def _earlier(m):
    """WHERE part for meetings before m (by date, then by id)."""
    scope, args = _scope(m)
    # Tickets written by hand hang on one hidden "meeting" per project: always part of the memory
    # Daily standups are kept out: they are a report, never a source of tasks
    return (f"pm.id != ? AND pm.kind != 'standup' AND (pm.source = 'manual' OR ((pm.meeting_date < ? OR (pm.meeting_date = ? AND pm.id < ?)) "
            f"AND pm.meeting_date >= date(?, '-{DAYS_BACK} days'))) AND {scope}",
            (m["id"], m["meeting_date"], m["meeting_date"], m["id"], m["meeting_date"], *args))


def open_items(m):
    """Open work from earlier meetings. Tasks already merged into a ticket are represented by the ticket."""
    where, args = _earlier(m)
    with db.connect() as c:
        return db.rows(c.execute(f"""
            SELECT t.id, t.description, COALESCE(e.full_name, t.assignee) AS who, t.due_date, t.jira_key,
                   pm.title AS meeting_title, pm.meeting_date
            FROM tasks t JOIN meetings pm ON pm.id = t.meeting_id
            LEFT JOIN employees e ON e.id = t.assignee_id
            WHERE t.status = 'open' AND t.merged_into IS NULL AND {where}
            ORDER BY pm.meeting_date DESC, t.id DESC LIMIT ?""", (*args, MAX_OPEN)))


def context(m):
    """{"items": [...], "text": prompt text or ""} for meeting m."""
    items = open_items(m)
    where, args = _earlier(m)
    with db.connect() as c:
        decisions = db.rows(c.execute(f"""
            SELECT d.description, pm.meeting_date FROM decisions d JOIN meetings pm ON pm.id = d.meeting_id
            WHERE {where} ORDER BY pm.meeting_date DESC, d.id DESC LIMIT ?""", (*args, MAX_DECISIONS)))
        summaries = db.rows(c.execute(f"""
            SELECT pm.title, pm.meeting_date, pm.summary FROM meetings pm
            WHERE pm.summary IS NOT NULL AND {where} ORDER BY pm.meeting_date DESC, pm.id DESC LIMIT ?""",
            (*args, MAX_SUMMARIES)))
    parts = []
    if items:
        lines = []
        for t in items:
            extra = [x for x in [t["who"], f"due {t['due_date']}" if t["due_date"] else None,
                                 f"Jira {t['jira_key']}" if t["jira_key"] else None] if x]
            lines.append(f"[{t['id']}] {t['description']}" + (f" ({', '.join(extra)})" if extra else "")
                         + f" — from “{t['meeting_title']}” {t['meeting_date']}")
        parts.append("Open work from earlier meetings:\n" + "\n".join(lines))
    if decisions:
        parts.append("Earlier decisions:\n" + "\n".join(f"- ({d['meeting_date']}) {d['description']}" for d in decisions))
    if summaries:
        parts.append("Last meetings:\n" + "\n".join(f"- {s['title']} ({s['meeting_date']}): {s['summary']}" for s in summaries))
    return {"items": items, "text": "\n\n".join(parts)}


def closed_tickets(m=None, project_id=None):
    """Finished tickets (also ones sent to Jira) that the same work can reopen instead of a new ticket.
    m: a meeting (its project / team scope, the meeting itself left out); otherwise project_id (None = all)."""
    if m is not None:
        scope, args = _scope(m)
        where, args = f"pm.id != ? AND {scope}", (m["id"], *args)
    else:
        where, args = "(? IS NULL OR pm.project_id = ?)", (project_id, project_id)
    with db.connect() as c:
        return db.rows(c.execute(f"""
            SELECT t.id, t.description, COALESCE(e.full_name, t.assignee) AS who, t.jira_key, t.is_ticket
            FROM tasks t JOIN meetings pm ON pm.id = t.meeting_id
            LEFT JOIN employees e ON e.id = t.assignee_id
            WHERE t.status = 'done' AND t.merged_into IS NULL AND (t.is_ticket = 1 OR t.jira_key IS NOT NULL)
              AND {where}
            ORDER BY t.completed_at DESC, t.id DESC LIMIT 300""", args))


def reopen(task_id, why=None):
    """Open a finished ticket again (the same work came up again) instead of making a new one.
    When it was sent to Jira, the issue is moved out of Done there too. Returns a Jira error or None."""
    from . import jira  # local import: jira imports db

    with db.connect() as c:
        row = c.execute("SELECT jira_key FROM tasks WHERE id = ?", (task_id,)).fetchone()
        c.execute("UPDATE tasks SET status = 'open', completed_at = NULL, done_by_jira = 0 WHERE id = ?", (task_id,))
    if not row or not row["jira_key"] or not jira.configured():
        return None
    try:
        name = jira.reopen_issue(row["jira_key"], why)
    except jira.JiraError as e:
        with db.connect() as c:
            c.execute("UPDATE tasks SET jira_error = ? WHERE id = ?", (f"Reopen in Jira failed: {e}"[:400], task_id))
        return str(e)
    with db.connect() as c:
        # the new Jira status is stored, so the status sync doesn't count it as a change
        c.execute("UPDATE tasks SET jira_status = COALESCE(?, jira_status), jira_error = NULL WHERE id = ?", (name, task_id))
    return None


def save_reopened(meeting_id, reopened):
    """reopened: [(ticket, note, quote)] found by the pipeline. Reopens them and records it on the meeting."""
    m = db.get_meeting(meeting_id)
    for ticket, note, quote in reopened:
        reopen(ticket["id"], f"Reopened by AnchorzUp Meeting Agent: the same work came up again in the meeting "
                             f"“{m['title']}” ({m['meeting_date']}). {note}")
        with db.connect() as c:
            c.execute("INSERT INTO task_updates (task_id, meeting_id, status, note, quote, closed_task, created_at) "
                      "VALUES (?,?,?,?,?,0,?)", (ticket["id"], meeting_id, "reopened", note[:500] or None,
                                                 (quote or "")[:500] or None, db.now()))
    return len(reopened)


def undo(meeting_id):
    """Before a meeting is analyzed again: reopen the tasks it closed and forget its updates."""
    with db.connect() as c:
        c.execute("""UPDATE tasks SET status = 'open', completed_at = NULL WHERE status = 'done' AND id IN
                     (SELECT task_id FROM task_updates WHERE meeting_id = ? AND closed_task = 1)""", (meeting_id,))
        c.execute("DELETE FROM task_updates WHERE meeting_id = ?", (meeting_id,))


def save(meeting_id, updates, items):
    """updates: [{"item_id", "status", "note", "quote"}] from the AI. Only ids that were given count.
    Returns the number of earlier tasks closed."""
    known = {t["id"] for t in items}
    closed, seen = 0, set()
    with db.connect() as c:
        for u in updates:
            tid, status = u.get("item_id"), (u.get("status") or "mentioned").lower()
            if tid not in known or tid in seen:
                continue
            seen.add(tid)
            if status not in ("done", "in_progress", "blocked", "mentioned"):
                status = "mentioned"
            close = status == "done"
            if close:
                cur = c.execute("UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status = 'open'",
                                (db.now(), tid))
                close = cur.rowcount > 0
                closed += close
            c.execute("INSERT INTO task_updates (task_id, meeting_id, status, note, quote, closed_task, created_at) "
                      "VALUES (?,?,?,?,?,?,?)", (tid, meeting_id, status, (u.get("note") or "")[:500] or None,
                                                 (u.get("quote") or "")[:500] or None, 1 if close else 0, db.now()))
    return closed


def for_meeting(meeting_id):
    """Updates this meeting made on earlier work, for the meeting page."""
    with db.connect() as c:
        return db.rows(c.execute("""
            SELECT u.*, t.description, t.status AS task_status, t.jira_key, t.jira_url,
                   COALESCE(e.full_name, t.assignee) AS who, pm.id AS from_meeting_id, pm.title AS from_title,
                   pm.meeting_date AS from_date
            FROM task_updates u JOIN tasks t ON t.id = u.task_id JOIN meetings pm ON pm.id = t.meeting_id
            LEFT JOIN employees e ON e.id = t.assignee_id
            WHERE u.meeting_id = ? ORDER BY CASE u.status WHEN 'done' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END, u.id""",
            (meeting_id,)))


def for_task(task_id):
    """What later meetings said about a task (newest first)."""
    with db.connect() as c:
        return db.rows(c.execute("""
            SELECT u.status, u.note, u.quote, m.id AS meeting_id, m.title AS meeting_title, m.meeting_date
            FROM task_updates u JOIN meetings m ON m.id = u.meeting_id WHERE u.task_id = ?
            ORDER BY m.meeting_date DESC, u.id DESC""", (task_id,)))
