"""
Tasks without an assignee.

Meeting work often has no owner at first; the PM chooses one later in Tasks. So it is not kept
in a separate list: work the AI finds that nobody took on becomes an ordinary task without an
assignee, and the PM gets a reminder (meeting note and email, daily digest, home page) until
someone is chosen.

The old "Needs an owner" list (table `unowned`) is kept only as history: init() moves its open
items into the tasks of their meeting, once.
"""
from . import db, dedupe

SCHEMA = """
CREATE TABLE IF NOT EXISTS unowned (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    why TEXT,
    quote TEXT,
    status TEXT NOT NULL DEFAULT 'open',   -- open | assigned | dismissed
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    resolved_by INTEGER,
    resolved_at TEXT,
    resolved_note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS unowned_meeting ON unowned(meeting_id);
"""


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)
        # Open items of the old list become confirmed tasks (confirmed, so "Analyze again" keeps them)
        for u in db.rows(c.execute("SELECT id, meeting_id, description, why, quote FROM unowned WHERE status = 'open'")):
            task_id = c.execute(
                "INSERT INTO tasks (meeting_id, description, details, quote, created_at, approved_at) VALUES (?,?,?,?,?,?)",
                (u["meeting_id"], u["description"], u["why"], u["quote"], db.now(), db.now())).lastrowid
            c.execute("UPDATE unowned SET status = 'assigned', task_id = ?, resolved_at = ?, resolved_note = ? WHERE id = ?",
                      (task_id, db.now(), "Moved to Tasks without an assignee", u["id"]))


def as_tasks(items, tasks):
    """The AI's `unowned` list as tasks without an assignee, skipping work already in `tasks`."""
    out = []
    for u in items or []:
        desc = " ".join((u.get("description") or "").split())[:500]
        if not desc or dedupe.best_match(desc, tasks + out):
            continue
        out.append({"description": desc, "assignee": None, "due_date": None, "due_date_text": None,
                    "quote": (u.get("quote") or "")[:500], "details": (u.get("why") or "")[:500] or None,
                    "priority": "medium", "priority_reason": None, "estimate_hours": None, "continues": None})
    return out


def _no_owner(t):
    """No real person: nobody, or only a speaker label such as 'Folësi 2'."""
    return not t.get("assignee_id") and not t.get("assignee_email") and dedupe._speaker_label(t.get("assignee"))


def unassigned(meeting_ids=None):
    """Open tasks nobody was given yet. meeting_ids: None = all meetings, else only these. Newest first."""
    only = "" if meeting_ids is None else f" AND m.id IN ({db._in(meeting_ids)})"
    with db.connect() as c:
        tasks = db.rows(c.execute(db.TASK_SELECT + f"""
            WHERE t.status = 'open' AND t.merged_into IS NULL AND t.jira_key IS NULL
              AND t.assignee_id IS NULL AND t.assignee_email IS NULL{only}
            ORDER BY m.meeting_date DESC, t.id""", tuple(meeting_ids or ())))
    return [t for t in tasks if _no_owner(t)]
