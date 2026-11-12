"""
Work without an owner ("Needs an owner").

Meetings often agree that something must be done, but nobody takes it on. That work must not
be forgotten, so it is kept in its own list until the PM gives it to someone or says it is
not needed:
  - issues the AI found in a meeting that nobody took on (table `unowned`)
  - open tasks and tickets from meetings without an assignee (or only a speaker label)
When someone takes on the same work in a later meeting, the issue is closed automatically.
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


class OwnersError(Exception):
    pass


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def save(meeting_id, items):
    """items: the AI's `unowned` list for this meeting. "Analyze again" replaces the open ones;
    the ones the PM already handled stay. Returns how many are open now."""
    m = db.get_meeting(meeting_id)
    scope, args = _scope_sql(m)
    with db.connect() as c:
        c.execute("DELETE FROM unowned WHERE meeting_id = ? AND status = 'open'", (meeting_id,))
        known = [{"description": r[0]} for r in c.execute(
            "SELECT description FROM unowned WHERE meeting_id = ?", (meeting_id,))]
        # Still open from an earlier meeting: it is already on the list, not twice
        known += [{"description": r[0]} for r in c.execute(
            f"SELECT u.description FROM unowned u JOIN meetings m ON m.id = u.meeting_id "
            f"WHERE u.status = 'open' AND u.meeting_id != ? AND {scope}", (meeting_id, *args))]
        # The same work as a task of this meeting is not an issue on its own (an unassigned task shows anyway)
        known += [{"description": r[0]} for r in c.execute(
            "SELECT description FROM tasks WHERE meeting_id = ?", (meeting_id,))]
        n = 0
        for u in items or []:
            desc = " ".join((u.get("description") or "").split())[:500]
            if not desc or dedupe.best_match(desc, known):
                continue
            known.append({"description": desc})
            c.execute("INSERT INTO unowned (meeting_id, description, why, quote, created_at) VALUES (?,?,?,?,?)",
                      (meeting_id, desc, (u.get("why") or "")[:500] or None, (u.get("quote") or "")[:500] or None,
                       db.now()))
            n += 1
    return n


def _scope_sql(m):
    """Earlier issues that a meeting can resolve: same project, else same team, else all."""
    if m.get("project_id"):
        return "m.project_id = ?", (m["project_id"],)
    if m.get("team_id"):
        return "m.team_id = ?", (m["team_id"],)
    return "1 = 1", ()


def resolve_from_meeting(meeting_id):
    """Someone took on the work in this meeting: open issues from earlier meetings about the
    same work are closed and linked to the new task. Returns how many."""
    m = db.get_meeting(meeting_id)
    scope, args = _scope_sql(m)
    with db.connect() as c:
        taken = [t for t in db.rows(c.execute(
            "SELECT id, description, assignee FROM tasks WHERE meeting_id = ? AND merged_into IS NULL", (meeting_id,)))
            if not _no_owner(t)]
        if not taken:
            return 0
        issues = db.rows(c.execute(
            f"SELECT u.id, u.description FROM unowned u JOIN meetings m ON m.id = u.meeting_id "
            f"WHERE u.status = 'open' AND u.meeting_id != ? AND {scope}", (meeting_id, *args)))
        n = 0
        for u in issues:
            t = dedupe.best_match(u["description"], taken)
            if t:
                c.execute("UPDATE unowned SET status = 'assigned', task_id = ?, resolved_at = ?, resolved_note = ? "
                          "WHERE id = ?", (t["id"], db.now(), f"{t['assignee']} took it on in “{m['title']}”", u["id"]))
                n += 1
    return n


def _no_owner(t):
    """No real person: nobody, or only a speaker label such as 'Folësi 2'."""
    return not t.get("assignee_id") and not t.get("assignee_email") and dedupe._speaker_label(t.get("assignee"))


def open_items(meeting_ids=None):
    """Everything that still needs an owner. meeting_ids: None = all meetings, else only these.
    {"issues": [...], "tasks": [...]}, newest meeting first."""
    only = "" if meeting_ids is None else f" AND m.id IN ({db._in(meeting_ids)})"
    args = tuple(meeting_ids or ())
    with db.connect() as c:
        issues = db.rows(c.execute(f"""
            SELECT u.id, u.meeting_id, u.description, u.why, u.quote, u.created_at,
                   m.title AS meeting_title, m.meeting_date, m.project_id, p.name AS project_name
            FROM unowned u JOIN meetings m ON m.id = u.meeting_id LEFT JOIN projects p ON p.id = m.project_id
            WHERE u.status = 'open'{only}
            ORDER BY m.meeting_date DESC, u.id""", args))
        tasks = db.rows(c.execute(db.TASK_SELECT + f"""
            WHERE t.status = 'open' AND t.merged_into IS NULL AND t.jira_key IS NULL
              AND t.assignee_id IS NULL AND t.assignee_email IS NULL{only}
            ORDER BY m.meeting_date DESC, t.id""", args))
    return {"issues": issues, "tasks": [t for t in tasks if _no_owner(t)]}


def count(meeting_ids=None):
    r = open_items(meeting_ids)
    return len(r["issues"]) + len(r["tasks"])


def for_meeting(meeting_id):
    """The issues of one meeting (open ones first), for the meeting page."""
    with db.connect() as c:
        return db.rows(c.execute("""
            SELECT u.*, COALESCE(e.full_name, t.assignee) AS assignee_name, t.jira_key
            FROM unowned u LEFT JOIN tasks t ON t.id = u.task_id LEFT JOIN employees e ON e.id = t.assignee_id
            WHERE u.meeting_id = ? ORDER BY u.status != 'open', u.id""", (meeting_id,)))


def get(issue_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM unowned WHERE id = ?", (issue_id,)).fetchone()
        return dict(r) if r else None


def assign(issue_id, employee_id, due_date, user_id):
    """Give the issue to someone: it becomes a confirmed task of its meeting. Returns the task id."""
    from . import org  # local import: org imports db

    u = get(issue_id)
    if not u or u["status"] != "open":
        raise OwnersError("This issue is already handled.")
    task_id = db.add_task(u["meeting_id"], u["description"], due_date)
    with db.connect() as c:
        c.execute("UPDATE tasks SET quote = ?, details = ? WHERE id = ?", (u["quote"], u["why"], task_id))
    try:
        org.set_task_assignee(task_id, employee_id)
    except org.OrgError as e:
        db.delete_task(task_id)
        raise OwnersError(str(e))
    with db.connect() as c:
        c.execute("UPDATE unowned SET status = 'assigned', task_id = ?, resolved_by = ?, resolved_at = ? WHERE id = ?",
                  (task_id, user_id, db.now(), issue_id))
    return task_id


def dismiss(issue_id, user_id, note=None):
    """The PM says nobody has to do it."""
    u = get(issue_id)
    if not u or u["status"] != "open":
        raise OwnersError("This issue is already handled.")
    with db.connect() as c:
        c.execute("UPDATE unowned SET status = 'dismissed', resolved_by = ?, resolved_at = ?, resolved_note = ? "
                  "WHERE id = ?", (user_id, db.now(), (note or "").strip()[:300] or None, issue_id))


def restore(issue_id):
    """Undo "Not needed"."""
    with db.connect() as c:
        c.execute("UPDATE unowned SET status = 'open', resolved_by = NULL, resolved_at = NULL, resolved_note = NULL "
                  "WHERE id = ? AND status = 'dismissed'", (issue_id,))
