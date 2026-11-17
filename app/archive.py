"""
Weekly archive: Year -> Month -> Week.

Every Monday the week that just ended moves to the Archive, so the main pages start fresh:
  - Meetings, Daily standups and the Dashboard show only this week (plus anything still being
    processed or added this week);
  - Tasks hides work finished before this week (open work always stays, so nothing is forgotten);
  - the weekly report of the past week is saved as a fixed copy (`archive_reports`): one for
    all projects (administrators) and one per PM with only their projects.
Nothing is deleted: a past meeting still opens in full, "Ask the archive" still searches
everything, and the Archive page lists every past week with its meetings, standups, finished
work and saved report. A week belongs to the month its Monday is in.
"""
import json
import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from . import db, weekly

SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_reports (
    week_start TEXT NOT NULL,      -- Monday of the week (YYYY-MM-DD)
    scope TEXT NOT NULL,           -- 'all' (administrators) or 'pm:<employee id>'
    data TEXT NOT NULL,            -- the weekly report as JSON, as it was when the week ended
    created_at TEXT NOT NULL,
    PRIMARY KEY (week_start, scope)
);
"""


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def current_week_start(today=None):
    """Monday of this week (YYYY-MM-DD): anything before it is archived."""
    return weekly.week_of(today or date.today())[0].isoformat()


def week_start_of(day_iso):
    return weekly.week_of(date.fromisoformat(day_iso[:10]))[0].isoformat()


# ---------- What the main pages show ----------

BUSY = ("queued", "transcribing", "diarizing", "extracting")


def is_current_meeting(m, ws):
    """This week's meeting, one added this week (even with an older date), or one still in progress."""
    return ((m.get("meeting_date") or "") >= ws or (m.get("created_at") or "") >= ws
            or m.get("status") in BUSY or m.get("status") == "error")


def is_current_task(t, ws):
    """Open work always; finished work only if it was finished this week."""
    return t.get("status") != "done" or (t.get("completed_at") or t.get("created_at") or "") >= ws


# ---------- Saved weekly reports ----------

def _save(week_start, scope, report):
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO archive_reports (week_start, scope, data, created_at) VALUES (?,?,?,?)",
                  (week_start, scope, json.dumps(report, ensure_ascii=False, default=str), db.now()))


def saved_weeks(scope="all"):
    with db.connect() as c:
        return {r[0] for r in c.execute("SELECT DISTINCT week_start FROM archive_reports WHERE scope = ?", (scope,))}


def snapshot(week_start, ai=False):
    """Save the report of one finished week: for all projects and for each PM."""
    day = date.fromisoformat(week_start)
    end = day + timedelta(days=6)
    _save(week_start, "all", weekly.build(None, day, today=end, ai=ai))
    for pm in weekly.pms():
        _save(week_start, f"pm:{pm['id']}", weekly.build(pm["project_ids"], day, today=end, ai=ai))


def snapshot_due(today=None):
    """Save every finished week that has no saved report yet. The week that just ended gets the
    AI highlights (when a key is set); older weeks found the first time are saved without them."""
    today = today or date.today()
    ws = current_week_start(today)
    with db.connect() as c:
        first = c.execute("SELECT MIN(meeting_date) FROM meetings WHERE source != 'manual'").fetchone()[0]
    if not first:
        return 0
    have = saved_weeks()
    last = (date.fromisoformat(ws) - timedelta(days=7)).isoformat()
    week = date.fromisoformat(week_start_of(first))
    n = 0
    while week.isoformat() < ws:
        w = week.isoformat()
        if w not in have:
            try:
                snapshot(w, ai=(w == last and weekly.llm.available()))
                n += 1
            except Exception:
                traceback.print_exc()
        week += timedelta(days=7)
    return n


def report(week_start, scope):
    with db.connect() as c:
        r = c.execute("SELECT data FROM archive_reports WHERE week_start = ? AND scope = ?", (week_start, scope)).fetchone()
    return json.loads(r[0]) if r else None


# ---------- The Archive page ----------

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def _past_meetings(meeting_ids, ws, kind=None):
    """Past meetings (before this week) the user may see, with their week."""
    where = ["m.source != 'manual'", "m.meeting_date < ?", "m.created_at < ?"]
    args = [ws, ws]
    if kind:
        where.append("m.kind = ?")
        args.append(kind)
    if meeting_ids is not None:
        where.append(f"m.id IN ({db._in(meeting_ids)})")
        args += list(meeting_ids)
    with db.connect() as c:
        return db.rows(c.execute(f"""
            SELECT m.id, m.title, m.meeting_date, m.kind, m.status, m.duration_sec, m.language, pr.name AS project_name,
                   (SELECT COUNT(*) FROM tasks t WHERE t.meeting_id = m.id AND t.merged_from IS NULL) AS task_count,
                   (SELECT COUNT(*) FROM decisions d WHERE d.meeting_id = m.id) AS decision_count
            FROM meetings m LEFT JOIN projects pr ON pr.id = m.project_id
            WHERE {' AND '.join(where)} ORDER BY m.meeting_date DESC, m.id DESC""", args))


def _past_done(task_meeting_ids, ws):
    """Work finished before this week (tasks and tickets) in the meetings the user manages."""
    only = "" if task_meeting_ids is None else f" AND t.meeting_id IN ({db._in(task_meeting_ids)})"
    with db.connect() as c:
        rows = db.rows(c.execute(db.TASK_SELECT + f"""
            WHERE t.status = 'done' AND t.merged_into IS NULL AND COALESCE(t.completed_at, t.created_at) < ?{only}
            ORDER BY COALESCE(t.completed_at, t.created_at) DESC""", (ws, *(task_meeting_ids or ()))))
    for t in rows:
        t["done_on"] = (t.get("completed_at") or t["created_at"])[:10]
    return rows


def tree(meeting_ids, task_meeting_ids, today=None, report_scope="all"):
    """Years -> months -> weeks, newest first, with how much each week holds."""
    ws = current_week_start(today)
    weeks = {}

    def slot(day_iso):
        w = week_start_of(day_iso)
        return weeks.setdefault(w, {"start": w, "end": (date.fromisoformat(w) + timedelta(days=6)).isoformat(),
                                    "meetings": 0, "standups": 0, "done": 0, "report": False})

    for m in _past_meetings(meeting_ids, ws):
        slot(m["meeting_date"])["standups" if m["kind"] == "standup" else "meetings"] += 1
    if task_meeting_ids != []:
        for t in _past_done(task_meeting_ids, ws):
            slot(t["done_on"])["done"] += 1
    for w in (saved_weeks(report_scope) if report_scope else ()):
        if w < ws:
            slot(w)["report"] = True

    years = {}
    for w in sorted(weeks.values(), key=lambda x: x["start"], reverse=True):
        d = date.fromisoformat(w["start"])
        y = years.setdefault(d.year, {"year": d.year, "months": {}})
        mo = y["months"].setdefault(d.month, {"month": f"{d.year}-{d.month:02d}", "name": MONTHS[d.month - 1], "weeks": []})
        mo["weeks"].append(w)
    return [{"year": y["year"], "months": list(y["months"].values())} for y in years.values()]


def week(week_start, meeting_ids, task_meeting_ids, report_scope, today=None):
    """Everything of one past week."""
    ws = current_week_start(today)
    if week_start >= ws:
        return None
    end = (date.fromisoformat(week_start) + timedelta(days=6)).isoformat()
    in_week = lambda d: week_start <= (d or "")[:10] <= end  # noqa: E731
    meetings = [m for m in _past_meetings(meeting_ids, ws) if in_week(m["meeting_date"])]
    done = [t for t in _past_done(task_meeting_ids, ws) if in_week(t["done_on"])] if task_meeting_ids != [] else []
    return {
        "start": week_start, "end": end,
        "meetings": [m for m in meetings if m["kind"] != "standup"],
        "standups": [m for m in meetings if m["kind"] == "standup"],
        "done": done,
        "report": report(week_start, report_scope) if report_scope else None,
    }


# ---------- Every Monday ----------

def start_scheduler():
    """Save the week that just ended (checked every hour, so a computer that was off catches up)."""
    if os.environ.get("ARCHIVE", "1") == "0":
        return

    def loop():
        while True:
            try:
                snapshot_due()
            except Exception:
                traceback.print_exc()
            time.sleep(3600)

    threading.Thread(target=loop, daemon=True, name="archive").start()
