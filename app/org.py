"""
Employees, teams and projects.

An employee can belong to several teams and projects. A meeting can be linked
to a team and/or project; its members are passed to the LLM so tasks are
assigned to real people, and task assignees are linked to employee records.

Each PM (every account that can sign in) has their own workspace: their teams, projects and the
memberships in them are theirs alone (teams.owner_id, projects.pm_id). Only the employee list
itself (and the voice profiles) is shared by the whole company, so two PMs can put the same
person in their own teams.
"""
import csv
import io
import re
import sqlite3
import unicodedata
from datetime import datetime

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT,
    owner_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,  -- the PM whose workspace it is
    UNIQUE (owner_id, name)
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE,
    team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    description TEXT,
    pm_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,  -- the PM who owns the project
    jira_key TEXT,
    UNIQUE (pm_id, name)
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT,
    role TEXT,
    aliases TEXT,                 -- other names people use, comma separated (e.g. "Ari, A.K.")
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    PRIMARY KEY (team_id, employee_id)
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, employee_id)
);
"""

# Columns added to existing tables (SQLite has no "ADD COLUMN IF NOT EXISTS")
MIGRATIONS = {
    "meetings": {
        "team_id": "INTEGER REFERENCES teams(id) ON DELETE SET NULL",
        "project_id": "INTEGER REFERENCES projects(id) ON DELETE SET NULL",
        "created_by": "INTEGER REFERENCES employees(id) ON DELETE SET NULL",
        "notified_at": "TEXT",
        "completed": "TEXT",   # JSON list: work reported as already done (not tickets)
        "kind": "TEXT NOT NULL DEFAULT 'meeting'",  # meeting | standup (a daily standup: a report, never tasks)
        "topics": "TEXT",      # JSON list of {title, summary}: what was discussed (most of it is not a task)
        "speakers_hint": "INTEGER",  # how many people spoke, if the PM said so (helps split the voices)
    },
    "tasks": {
        "assignee_id": "INTEGER REFERENCES employees(id) ON DELETE SET NULL",
        "jira_key": "TEXT",
        "jira_url": "TEXT",
        "jira_error": "TEXT",
        "approved_at": "TEXT",   # NULL = untouched AI draft; set when the PM edits it (kept by "Analyze again")
        # Ticket review (page "Tickets"): the PM decides who gets the ticket and where it goes
        "assignee_email": "TEXT",  # recipient's work email; NULL = the linked employee's email
        "details": "TEXT",         # extra description for the Jira ticket
        "jira_project": "TEXT",    # Jira project key for this ticket; NULL = the project's key
        "sent_at": "TEXT",
        "sent_by": "INTEGER REFERENCES employees(id) ON DELETE SET NULL",
        # Tasks -> tickets: a ticket is made from one or more tasks (page "Tasks")
        "is_ticket": "INTEGER NOT NULL DEFAULT 0",  # 1 = shown on the Tickets page
        "merged_from": "TEXT",                       # ticket row: JSON list of the source task ids
        "merged_into": "INTEGER",                    # task row: the ticket it became part of
        # Jira -> app: the issue's status, read back from Jira (see jira.sync_status)
        "jira_status": "TEXT",                       # e.g. "To Do", "In Progress", "Done"
        "done_by_jira": "INTEGER NOT NULL DEFAULT 0",  # 1 = closed because the Jira issue is done
        # AI suggestion from the meeting, the PM can change it (sent to Jira as priority / original estimate)
        "priority": "TEXT",          # high | medium | low ; NULL = not set
        "priority_reason": "TEXT",   # why, a few words
        "estimate_hours": "REAL",    # rough effort in working hours (1 day = 8 h)
    },
    "employees": {
        "is_admin": "INTEGER NOT NULL DEFAULT 0",
    },
    "projects": {
        "pm_id": "INTEGER REFERENCES employees(id) ON DELETE SET NULL",  # one project manager
        "jira_key": "TEXT",                                              # Jira project key, e.g. PAY
    },
}

JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    if (table, col) == ("tasks", "is_ticket"):
                        # Before tickets were made by hand, every confirmed or sent task was a ticket
                        c.execute("UPDATE tasks SET is_ticket = 1 WHERE approved_at IS NOT NULL OR jira_key IS NOT NULL")
        needs_split = "owner_id" not in {r["name"] for r in c.execute("PRAGMA table_info(teams)")}
    if needs_split:
        _split_workspaces()


def _table_exists(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _split_workspaces():
    """One-time move from one shared workspace to one per PM. Everything that existed belonged to
    the single account of that time (the first administrator), so it all becomes theirs.
    The database is backed up first; SQLite can't change a UNIQUE constraint in place, so teams,
    projects and learned corrections are rebuilt (the documented create-copy-drop-rename way)."""
    backup = db.DB_PATH.with_name(f"{db.DB_PATH.name}.backup-{datetime.now():%Y%m%d-%H%M%S}-before-workspaces")
    src = sqlite3.connect(db.DB_PATH)
    try:
        dst = sqlite3.connect(backup)
        src.backup(dst)
        dst.close()
    finally:
        src.close()

    conn = sqlite3.connect(db.DB_PATH, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")  # must be off while tables are swapped
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT id FROM employees WHERE is_admin = 1 ORDER BY active DESC, id LIMIT 1").fetchone()
        owner = r[0] if r else None
        # Plain execute() only: executescript() would COMMIT and break the single transaction
        conn.execute("""
            CREATE TABLE teams_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE,
                description TEXT,
                owner_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                UNIQUE (owner_id, name)
            )""")
        conn.execute("""
            CREATE TABLE projects_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE,
                team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
                description TEXT,
                pm_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
                jira_key TEXT,
                UNIQUE (pm_id, name)
            )""")
        conn.execute("INSERT INTO teams_new (id, name, description, owner_id) SELECT id, name, description, ? FROM teams",
                     (owner,))
        conn.execute("INSERT INTO projects_new (id, name, team_id, description, pm_id, jira_key) "
                     "SELECT id, name, team_id, description, ?, jira_key FROM projects", (owner,))
        conn.execute("DROP TABLE teams")
        conn.execute("ALTER TABLE teams_new RENAME TO teams")
        conn.execute("DROP TABLE projects")
        conn.execute("ALTER TABLE projects_new RENAME TO projects")
        conn.execute("UPDATE meetings SET created_by = ? WHERE created_by IS NULL", (owner,))
        if _table_exists(conn, "online_meetings"):
            conn.execute("UPDATE online_meetings SET created_by = ? WHERE created_by IS NULL", (owner,))
        if _table_exists(conn, "outbox"):
            cols = {r[1] for r in conn.execute("PRAGMA table_info(outbox)")}
            if "owner_id" not in cols:
                conn.execute("ALTER TABLE outbox ADD COLUMN owner_id INTEGER")
            conn.execute("UPDATE outbox SET owner_id = ? WHERE owner_id IS NULL", (owner,))
        if _table_exists(conn, "corrections"):
            cols = {r[1] for r in conn.execute("PRAGMA table_info(corrections)")}
            if "owner_id" not in cols:
                conn.execute("""
                    CREATE TABLE corrections_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id INTEGER,
                        wrong TEXT NOT NULL,
                        right TEXT NOT NULL,
                        times INTEGER NOT NULL DEFAULT 1,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (owner_id, wrong, right)
                    )""")
                conn.execute("INSERT INTO corrections_new (id, owner_id, wrong, right, times, active, created_at, updated_at) "
                             "SELECT id, ?, wrong, right, times, active, created_at, updated_at FROM corrections", (owner,))
                conn.execute("DROP TABLE corrections")
                conn.execute("ALTER TABLE corrections_new RENAME TO corrections")
        if owner and _table_exists(conn, "archive_reports"):
            # The saved weekly reports so far were that PM's reports
            conn.execute("INSERT OR IGNORE INTO archive_reports (week_start, scope, data, created_at) "
                         "SELECT week_start, ?, data, created_at FROM archive_reports WHERE scope = 'all'", (f"pm:{owner}",))
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"Workspace migration stopped, broken references: {bad[:5]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    print(f"Workspaces: the existing data now belongs to employee {owner}. Backup: {backup}")


class OrgError(ValueError):
    pass


def _clean(s, limit=200):
    s = (s or "").strip()
    return s[:limit] or None


# ---------- Read ----------

def overview(owner_id=None, meeting_ids=None):
    """All employees (shared), with only this PM's teams, projects and memberships.
    owner_id None = every workspace (local single-user mode). meeting_ids: the meetings whose open
    tasks are counted per person (None = all)."""
    team_where = "" if owner_id is None else " WHERE owner_id = ?"
    proj_where = "" if owner_id is None else " WHERE pm_id = ?"
    own = () if owner_id is None else (owner_id,)
    only = "" if meeting_ids is None else f" AND meeting_id IN ({db._in(meeting_ids)})"
    with db.connect() as c:
        employees = db.rows(c.execute("SELECT * FROM employees ORDER BY active DESC, full_name COLLATE NOCASE"))
        teams = db.rows(c.execute(f"SELECT * FROM teams{team_where} ORDER BY name COLLATE NOCASE", own))
        projects = db.rows(c.execute(f"SELECT * FROM projects{proj_where} ORDER BY name COLLATE NOCASE", own))
        tm = db.rows(c.execute(f"SELECT * FROM team_members WHERE team_id IN (SELECT id FROM teams{team_where})", own))
        pm = db.rows(c.execute(f"SELECT * FROM project_members WHERE project_id IN (SELECT id FROM projects{proj_where})", own))
        open_tasks = {r["assignee_id"]: r["n"] for r in c.execute(
            "SELECT assignee_id, COUNT(*) AS n FROM tasks WHERE status = 'open' AND assignee_id IS NOT NULL "
            f"AND merged_from IS NULL{only} GROUP BY assignee_id", tuple(meeting_ids or ()))}
    for e in employees:
        e["team_ids"] = [r["team_id"] for r in tm if r["employee_id"] == e["id"]]
        e["project_ids"] = [r["project_id"] for r in pm if r["employee_id"] == e["id"]]
        e["open_tasks"] = open_tasks.get(e["id"], 0)
        e["active"] = bool(e["active"])
    for t in teams:
        t["member_ids"] = [r["employee_id"] for r in tm if r["team_id"] == t["id"]]
    for p in projects:
        p["member_ids"] = [r["employee_id"] for r in pm if r["project_id"] == p["id"]]
    return {"employees": employees, "teams": teams, "projects": projects}


# ---------- Employees ----------

def _set_links(c, table, key, owner_key, owner_id, ids):
    c.execute(f"DELETE FROM {table} WHERE {owner_key} = ?", (owner_id,))
    c.executemany(f"INSERT OR IGNORE INTO {table} ({owner_key}, {key}) VALUES (?, ?)",
                  [(owner_id, i) for i in set(ids or [])])


def _sync_team_projects(c, team_id):
    """Project members must be members of the project's team: drop the ones who aren't."""
    c.execute("""
        DELETE FROM project_members
        WHERE project_id IN (SELECT id FROM projects WHERE team_id = ?)
          AND employee_id NOT IN (SELECT employee_id FROM team_members WHERE team_id = ?)
    """, (team_id, team_id))


def _add_to_project_team(c, project_id, employee_ids):
    """Anyone added to a project also becomes a member of its team."""
    row = c.execute("SELECT team_id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row and row["team_id"]:
        c.executemany("INSERT OR IGNORE INTO team_members (team_id, employee_id) VALUES (?, ?)",
                      [(row["team_id"], e) for e in employee_ids])


def get_employee(employee_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
        return dict(r) if r else None


def _owned(c, kind, owner_id):
    """Ids of the teams or projects in this PM's workspace (None = all workspaces)."""
    table, col = ("teams", "owner_id") if kind == "team" else ("projects", "pm_id")
    if owner_id is None:
        return {r[0] for r in c.execute(f"SELECT id FROM {table}")}
    return {r[0] for r in c.execute(f"SELECT id FROM {table} WHERE {col} = ?", (owner_id,))}


def _set_member_links(c, table, key, employee_id, ids, owned):
    """Set one employee's memberships, touching only the groups of this workspace:
    the same person's memberships in other PMs' teams and projects stay as they are."""
    c.execute(f"DELETE FROM {table} WHERE employee_id = ? AND {key} IN ({db._in(owned)})", (employee_id, *owned))
    c.executemany(f"INSERT OR IGNORE INTO {table} (employee_id, {key}) VALUES (?, ?)",
                  [(employee_id, i) for i in set(ids) & owned])


def save_employee(data, employee_id=None, owner_id=None):
    """The employee record is shared by every PM; team_ids / project_ids are this PM's only."""
    name = _clean(data.get("full_name"))
    if not name:
        raise OrgError("The name is required.")
    email = (_clean(data.get("email")) or "").lower() or None
    if email and not EMAIL_RE.match(email):
        raise OrgError("The email is not valid.")
    values = (name, email, _clean(data.get("role")),
              _clean(data.get("aliases"), 300), 1 if data.get("active", True) else 0)
    with db.connect() as c:
        # The email is the login: it must belong to one person only
        if email and c.execute("SELECT 1 FROM employees WHERE email = ? COLLATE NOCASE AND id != ?",
                               (email, employee_id or -1)).fetchone():
            raise OrgError(f"The email {email} already belongs to another employee.")
        if employee_id is None:
            employee_id = c.execute(
                "INSERT INTO employees (full_name, email, role, aliases, active, created_at) VALUES (?,?,?,?,?,?)",
                (*values, db.now())).lastrowid
        else:
            cur = c.execute("UPDATE employees SET full_name=?, email=?, role=?, aliases=?, active=? WHERE id=?",
                            (*values, employee_id))
            if not cur.rowcount:
                raise OrgError("Employee not found.")
        my_teams, my_projects = _owned(c, "team", owner_id), _owned(c, "project", owner_id)
        project_ids = set(data.get("project_ids") or []) & my_projects
        team_ids = set(data.get("team_ids") or []) & my_teams
        # Choosing a project implies membership in its team
        if project_ids:
            marks = ",".join("?" * len(project_ids))
            team_ids |= {r["team_id"] for r in c.execute(
                f"SELECT team_id FROM projects WHERE id IN ({marks}) AND team_id IS NOT NULL", tuple(project_ids))}
        _set_member_links(c, "team_members", "team_id", employee_id, team_ids, my_teams)
        _set_member_links(c, "project_members", "project_id", employee_id, project_ids, my_projects)
        # ...and leaving a team means leaving its projects
        c.execute(f"""
            DELETE FROM project_members WHERE employee_id = ? AND project_id IN (
                SELECT p.id FROM projects p WHERE p.team_id IS NOT NULL AND p.id IN ({db._in(my_projects)})
                  AND p.team_id NOT IN (SELECT team_id FROM team_members WHERE employee_id = ?))
        """, (employee_id, *my_projects, employee_id))
    return employee_id


def delete_employee(employee_id):
    with db.connect() as c:
        c.execute("DELETE FROM employees WHERE id = ?", (employee_id,))


# ---------- Teams & projects ----------

def save_group(kind, data, group_id=None, owner_id=None):
    """kind = 'team' or 'project'. The team or project belongs to owner_id (the signed-in PM):
    a new one is created in their workspace, and only their own can be changed."""
    name = _clean(data.get("name"), 120)
    if not name:
        raise OrgError("The name is required.")
    table, members, key = ("teams", "team_members", "team_id") if kind == "team" else \
                          ("projects", "project_members", "project_id")
    owner_col = "owner_id" if kind == "team" else "pm_id"
    cols = {"name": name, "description": _clean(data.get("description"), 500)}
    with db.connect() as c:
        if kind == "project":
            if not data.get("team_id"):
                raise OrgError("Choose the team the project belongs to.")
            if data["team_id"] not in _owned(c, "team", owner_id):
                raise OrgError("Choose one of your teams.")
            cols["team_id"] = data["team_id"]
            jira_key = (_clean(data.get("jira_key"), 10) or "").upper() or None
            if jira_key and not JIRA_KEY_RE.match(jira_key):
                raise OrgError("The Jira key must match Jira, e.g. PAY or HR2 (capital letters).")
            cols["jira_key"] = jira_key
        if owner_id is not None:
            # The PM who makes the project is its PM (and part of it)
            if kind == "project":
                data["member_ids"] = list(set(data.get("member_ids") or []) | {owner_id})
        elif kind == "project":
            cols["pm_id"] = data.get("pm_id") or None
        try:
            if group_id is None:
                if owner_id is not None:
                    cols[owner_col] = owner_id
                group_id = c.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    tuple(cols.values())).lastrowid
            else:
                mine = "" if owner_id is None else f" AND {owner_col} = ?"
                cur = c.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?{mine}",
                                (*cols.values(), group_id, *(() if owner_id is None else (owner_id,))))
                if not cur.rowcount:
                    raise OrgError("Not found.")
        except db.sqlite3.IntegrityError:
            raise OrgError(f"You already have a {kind} named “{name}”.")
        if "member_ids" in data:
            _set_links(c, members, "employee_id", key, group_id, data.get("member_ids"))
        if kind == "team":
            _sync_team_projects(c, group_id)
        else:
            # Project members (also when the project moves to another team) join its team
            ids = [r["employee_id"] for r in c.execute(
                "SELECT employee_id FROM project_members WHERE project_id = ?", (group_id,))]
            _add_to_project_team(c, group_id, ids)
    return group_id


def delete_group(kind, group_id, owner_id=None):
    """Delete one of this PM's teams or projects. Returns False when it isn't theirs."""
    table, owner_col = ("teams", "owner_id") if kind == "team" else ("projects", "pm_id")
    mine = "" if owner_id is None else f" AND {owner_col} = ?"
    with db.connect() as c:
        return c.execute(f"DELETE FROM {table} WHERE id = ?{mine}",
                         (group_id, *(() if owner_id is None else (owner_id,)))).rowcount > 0


# ---------- CSV import ----------

def import_csv(text, owner_id=None):
    """
    Each line: Emri, Email, Roli, Ekipet, Projektet
    Several teams/projects are separated with ';'. A header line is optional.
    Existing employees (same email, or same name) are updated, never duplicated.
    Teams and projects are looked up and created in owner_id's workspace only.
    """
    reader = csv.reader(io.StringIO(text.strip()))
    created = updated = 0
    errors = []
    with db.connect() as c:
        def team_id(name):
            row = c.execute("SELECT id FROM teams WHERE name = ? COLLATE NOCASE AND owner_id IS ?", (name, owner_id)).fetchone()
            return row["id"] if row else c.execute("INSERT INTO teams (name, owner_id) VALUES (?, ?)",
                                                   (name, owner_id)).lastrowid

        def project_id(name, row_team_ids):
            row = c.execute("SELECT id, team_id FROM projects WHERE name = ? COLLATE NOCASE AND pm_id IS ?",
                            (name, owner_id)).fetchone()
            if row:
                if row["team_id"] is None and row_team_ids:
                    c.execute("UPDATE projects SET team_id = ? WHERE id = ?", (row_team_ids[0], row["id"]))
                return row["id"]
            # A new project belongs to the (first) team named on the same line
            return c.execute("INSERT INTO projects (name, team_id, pm_id) VALUES (?, ?, ?)",
                             (name, row_team_ids[0] if row_team_ids else None, owner_id)).lastrowid

        for n, row in enumerate(reader, 1):
            row = [x.strip() for x in row] + [""] * 5
            name, email, role, teams, projects = row[:5]
            if not name or (n == 1 and name.lower() in ("emri", "name", "full_name", "emri i plotë")):
                continue
            if len(name) > 200:
                errors.append(f"Line {n}: the name is too long.")
                continue
            existing = None
            if email:
                existing = c.execute("SELECT id FROM employees WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
            if not existing:
                existing = c.execute("SELECT id FROM employees WHERE full_name = ? COLLATE NOCASE", (name,)).fetchone()
            if existing:
                emp_id = existing["id"]
                c.execute("UPDATE employees SET full_name=?, email=COALESCE(?, email), role=COALESCE(?, role), active=1 "
                          "WHERE id=?", (name, email or None, role or None, emp_id))
                updated += 1
            else:
                emp_id = c.execute("INSERT INTO employees (full_name, email, role, active, created_at) VALUES (?,?,?,1,?)",
                                   (name, email or None, role or None, db.now())).lastrowid
                created += 1
            row_team_ids = []
            for t in filter(None, (x.strip() for x in teams.split(";"))):
                tid = team_id(t[:120])
                row_team_ids.append(tid)
                c.execute("INSERT OR IGNORE INTO team_members (team_id, employee_id) VALUES (?,?)", (tid, emp_id))
            for p in filter(None, (x.strip() for x in projects.split(";"))):
                pid = project_id(p[:120], row_team_ids)
                c.execute("INSERT OR IGNORE INTO project_members (project_id, employee_id) VALUES (?,?)", (pid, emp_id))
                _add_to_project_team(c, pid, [emp_id])
    return {"created": created, "updated": updated, "errors": errors}


# ---------- Meetings: participants & assignee matching ----------

def participants(team_id=None, project_id=None):
    """Members of the meeting's team/project; all active employees if none is set."""
    with db.connect() as c:
        if team_id or project_id:
            return db.rows(c.execute("""
                SELECT DISTINCT e.* FROM employees e
                LEFT JOIN team_members tm ON tm.employee_id = e.id
                LEFT JOIN project_members pm ON pm.employee_id = e.id
                WHERE e.active = 1 AND (tm.team_id = ? OR pm.project_id = ?)
                ORDER BY e.full_name
            """, (team_id or -1, project_id or -1)))
        return db.rows(c.execute("SELECT * FROM employees WHERE active = 1 ORDER BY full_name LIMIT 300"))


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").lower()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9 ]+", " ", s).split()


def match_employee(name, candidates):
    """Return the id of the one employee this name refers to, or None if unclear."""
    words = _norm(name)
    if not words:
        return None
    full = " ".join(words)
    exact, partial = [], []
    for e in candidates:
        names = [_norm(e["full_name"])] + [_norm(a) for a in (e.get("aliases") or "").split(",") if a.strip()]
        if any(" ".join(n) == full for n in names):
            exact.append(e["id"])
        elif any(n and n[0] == words[0] for n in names) and len(words) == 1:
            partial.append(e["id"])  # only a first name was said
    if len(exact) == 1:
        return exact[0]
    if not exact and len(set(partial)) == 1:
        return partial[0]
    return None


def link_assignees(meeting_id, candidates):
    with db.connect() as c:
        # Only the AI's drafts: the PM's own links on confirmed tasks and tickets stay
        for t in db.rows(c.execute("SELECT id, assignee FROM tasks WHERE meeting_id = ? AND approved_at IS NULL "
                                   "AND jira_key IS NULL AND is_ticket = 0", (meeting_id,))):
            c.execute("UPDATE tasks SET assignee_id = ? WHERE id = ?",
                      (match_employee(t["assignee"], candidates), t["id"]))


def set_task_assignee(task_id, employee_id):
    with db.connect() as c:
        name = None
        if employee_id:
            row = c.execute("SELECT full_name FROM employees WHERE id = ?", (employee_id,)).fetchone()
            if not row:
                raise OrgError("Employee not found.")
            name = row["full_name"]
        # assignee_email = NULL: the ticket goes to the (new) employee's own email
        c.execute("UPDATE tasks SET assignee_id = ?, assignee = COALESCE(?, assignee), assignee_email = NULL WHERE id = ?",
                  (employee_id, name, task_id))


def employee_by_email(email):
    with db.connect() as c:
        r = c.execute("SELECT * FROM employees WHERE email = ? COLLATE NOCASE AND active = 1", (email,)).fetchone()
        return dict(r) if r else None
