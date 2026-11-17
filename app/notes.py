"""Sticky notes: a private notes board for each PM/administrator.

A note is seen and changed only by the person who wrote it (administrators too cannot read
other people's notes). Notes never leave the app: no email, no Google, no AI.
"""
from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    body TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT 'yellow',
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_owner ON notes(employee_id);
"""
COLORS = ("yellow", "pink", "green", "blue", "violet")
MAX_BODY = 10000
MAX_NOTES = 500


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)
        if "position" not in {r["name"] for r in c.execute("PRAGMA table_info(notes)")}:
            c.execute("ALTER TABLE notes ADD COLUMN position INTEGER NOT NULL DEFAULT 0")  # order set by drag & drop


def _out(r):
    r = dict(r)
    r["pinned"] = bool(r["pinned"])
    return r


def list_notes(employee_id):
    with db.connect() as c:
        return [_out(r) for r in c.execute(
            "SELECT * FROM notes WHERE employee_id = ? ORDER BY pinned DESC, position, id DESC", (employee_id,))]


def create(employee_id, body="", color="yellow"):
    with db.connect() as c:
        if c.execute("SELECT COUNT(*) FROM notes WHERE employee_id = ?", (employee_id,)).fetchone()[0] >= MAX_NOTES:
            raise ValueError(f"You have {MAX_NOTES} notes. Delete some old ones first.")
        now = db.now()
        # a new note goes first among the unpinned ones
        top = c.execute("SELECT COALESCE(MIN(position), 0) FROM notes WHERE employee_id = ?", (employee_id,)).fetchone()[0]
        nid = c.execute("INSERT INTO notes (employee_id, body, color, position, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (employee_id, body, color if color in COLORS else "yellow", top - 1, now, now)).lastrowid
        return _out(c.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone())


def update(employee_id, note_id, body=None, color=None, pinned=None):
    """Change only the given fields. None = the note is not this person's (or does not exist)."""
    sets, args = [], []
    if body is not None:
        sets.append("body = ?"); args.append(body)
    if color is not None and color in COLORS:
        sets.append("color = ?"); args.append(color)
    if pinned is not None:
        sets.append("pinned = ?"); args.append(int(bool(pinned)))
    with db.connect() as c:
        if sets:
            c.execute(f"UPDATE notes SET {', '.join(sets)}, updated_at = ? WHERE id = ? AND employee_id = ?",
                      (*args, db.now(), note_id, employee_id))
        r = c.execute("SELECT * FROM notes WHERE id = ? AND employee_id = ?", (note_id, employee_id)).fetchone()
    return _out(r) if r else None


def delete(employee_id, note_id):
    with db.connect() as c:
        return c.execute("DELETE FROM notes WHERE id = ? AND employee_id = ?", (note_id, employee_id)).rowcount > 0


def reorder(employee_id, ids):
    """Save the order the person dragged the notes into. Ids of other people's notes are ignored."""
    with db.connect() as c:
        for pos, nid in enumerate(dict.fromkeys(ids)):
            c.execute("UPDATE notes SET position = ? WHERE id = ? AND employee_id = ?", (pos, nid, employee_id))
