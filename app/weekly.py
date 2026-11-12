"""
Weekly and monthly reports for project managers.
Monthly: on the 1st of each month (from MONTHLY_REPORT_HOUR, default 9:00) for the month that
just ended; MONTHLY_REPORT=0 turns it off. "Due in the next 7 days" is counted from the end of
the period (or today).

Every week (WEEKLY_REPORT_DAY, default Friday, from WEEKLY_REPORT_HOUR, default 15:00) each PM
gets one email about their projects for the week (Monday to Sunday):
  - highlights written by the AI (when a key is set)
  - meetings held, decisions taken, questions left open
  - work finished (also tickets closed in Jira), new work, tickets sent to Jira
  - overdue work, work due in the next 7 days, work reported as blocked
  - open work per person
The same report is on the page "Weekly report" (any week), with "Email it to me".
"""
import json
import os
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from . import db, mailer

if str(db.ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(db.ROOT / "scripts"))

import llm  # noqa: E402  (scripts/llm.py)

SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_log (
    pm_id INTEGER NOT NULL,
    week_start TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (pm_id, week_start)
);
"""

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def week_of(d):
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def month_of(d):
    start = d.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def period_of(d, period="week"):
    return month_of(d) if period == "month" else week_of(d)


def _in(ids):
    return ",".join("?" * len(ids)) or "NULL"


def build(project_ids=None, day=None, today=None, ai=True, period="week"):
    """The report for the week (or month) containing `day`. project_ids: None = all projects."""
    today = today or date.today()
    period = "month" if period == "month" else "week"
    start, end = period_of(day or today, period)
    s, e = start.isoformat(), end.isoformat()
    proj = "1 = 1" if project_ids is None else f"m.project_id IN ({_in(project_ids)})"
    pa = tuple(project_ids or ())
    # Overdue / due soon are "as of" the end of a past week, or today for the current week
    as_of = min(today, end).isoformat()
    soon = (min(today, end) + timedelta(days=7)).isoformat()
    task_cols = """t.id, t.description, t.due_date, t.jira_key, t.jira_status, t.is_ticket, t.done_by_jira,
                   COALESCE(e.full_name, t.assignee) AS who, m.id AS meeting_id, m.title AS meeting_title,
                   m.source AS meeting_source, pr.name AS project"""
    task_from = """FROM tasks t JOIN meetings m ON m.id = t.meeting_id
                   LEFT JOIN employees e ON e.id = t.assignee_id LEFT JOIN projects pr ON pr.id = m.project_id"""
    live = "t.merged_into IS NULL"  # tasks that became part of a ticket are counted as the ticket

    with db.connect() as c:
        q = lambda sql, args=(): db.rows(c.execute(sql, args))
        projects = q(f"SELECT id, name FROM projects m_ WHERE {'1 = 1' if project_ids is None else f'id IN ({_in(project_ids)})'} "
                     "ORDER BY name", pa)
        meetings = q(f"""SELECT m.id, m.title, m.meeting_date, m.status, m.open_questions, pr.name AS project,
                                (SELECT COUNT(*) FROM tasks t WHERE t.meeting_id = m.id AND t.merged_from IS NULL) AS tasks
                         FROM meetings m LEFT JOIN projects pr ON pr.id = m.project_id
                         WHERE m.source != 'manual' AND m.meeting_date BETWEEN ? AND ? AND {proj}
                         ORDER BY m.meeting_date, m.id""", (s, e, *pa))
        decisions = q(f"""SELECT d.description, m.id AS meeting_id, m.title AS meeting_title, m.meeting_date
                          FROM decisions d JOIN meetings m ON m.id = d.meeting_id
                          WHERE m.meeting_date BETWEEN ? AND ? AND {proj} ORDER BY m.meeting_date, d.id""", (s, e, *pa))
        completed = q(f"""SELECT {task_cols}, substr(t.completed_at, 1, 10) AS completed_on {task_from}
                          WHERE t.status = 'done' AND substr(t.completed_at, 1, 10) BETWEEN ? AND ? AND {live} AND {proj}
                          ORDER BY t.completed_at""", (s, e, *pa))
        created = q(f"""SELECT {task_cols} {task_from}
                        WHERE substr(t.created_at, 1, 10) BETWEEN ? AND ? AND {live} AND {proj}
                          AND (t.is_ticket = 1 OR t.merged_from IS NULL) ORDER BY t.created_at""", (s, e, *pa))
        sent = q(f"""SELECT {task_cols} {task_from}
                     WHERE t.is_ticket = 1 AND substr(t.sent_at, 1, 10) BETWEEN ? AND ? AND {proj}
                     ORDER BY t.sent_at""", (s, e, *pa))
        overdue = q(f"""SELECT {task_cols} {task_from}
                        WHERE t.status = 'open' AND t.due_date < ? AND {live} AND {proj}
                        ORDER BY t.due_date""", (as_of, *pa))
        due_soon = q(f"""SELECT {task_cols} {task_from}
                         WHERE t.status = 'open' AND t.due_date BETWEEN ? AND ? AND {live} AND {proj}
                         ORDER BY t.due_date""", (as_of, soon, *pa))
        blocked = q(f"""SELECT {task_cols}, u.note, um.title AS said_in {task_from}
                        JOIN task_updates u ON u.task_id = t.id JOIN meetings um ON um.id = u.meeting_id
                        WHERE u.status = 'blocked' AND um.meeting_date BETWEEN ? AND ? AND t.status = 'open' AND {proj}
                        GROUP BY t.id ORDER BY um.meeting_date DESC""", (s, e, *pa))
        workload = q(f"""SELECT COALESCE(e.full_name, t.assignee, 'Unassigned') AS who, COUNT(*) AS open,
                                SUM(CASE WHEN t.due_date < ? THEN 1 ELSE 0 END) AS overdue,
                                SUM(CASE WHEN t.priority = 'high' THEN 1 ELSE 0 END) AS high,
                                SUM(COALESCE(t.estimate_hours, 0)) AS hours
                         {task_from} WHERE t.status = 'open' AND {live} AND {proj}
                         GROUP BY 1 ORDER BY open DESC, who""", (as_of, *pa))

    questions = {}  # the same question asked in several meetings: keep the latest
    for m in meetings:
        for oq in json.loads(m.pop("open_questions") or "[]"):
            questions[" ".join(oq.lower().split()).rstrip("?.")] = {"text": oq, "meeting_id": m["id"], "meeting_title": m["title"]}
    questions = list(questions.values())

    report = {
        "period": period, "label": _label(start, period),
        "week_start": s, "week_end": e, "as_of": as_of, "current": start <= today <= end,
        "projects": [p["name"] for p in projects],
        "counts": {"meetings": len(meetings), "completed": len(completed), "created": len(created),
                   "sent": len(sent), "overdue": len(overdue), "blocked": len(blocked)},
        "meetings": meetings, "decisions": decisions, "open_questions": questions,
        "completed": completed, "created": created, "sent": sent,
        "overdue": overdue, "due_soon": due_soon, "blocked": blocked, "workload": workload,
        "highlights": None,
    }
    if ai and llm.available() and (meetings or completed or overdue or created):
        try:
            report["highlights"] = _highlights(report)
        except Exception:
            traceback.print_exc()  # the report works without the AI part
    return report


def _label(start, period):
    if period == "month":
        return start.strftime("%B %Y")
    return f"{_d(start.isoformat())} – {_d((start + timedelta(days=6)).isoformat())}"


HIGHLIGHTS_PROMPT = """You write the "highlights" of a project manager's weekly or monthly report for a
software team, from the data given. 3-5 short sentences in English, plain text, no lists, no
markdown. Say what moved forward, what is at risk (overdue, blocked, one person overloaded) and
what needs the PM's attention next (week or month, as in the data). Use names, numbers and dates from the data only.
Write dates like "9 Nov", never as 2026-11-09."""


def _highlights(r):
    def short(rows, *keys):
        return [{k: x.get(k) for k in keys if x.get(k)} for x in rows[:25]]
    data = {
        "report": f"{r['period']}ly report, {r['week_start']} to {r['week_end']}", "as_of": r["as_of"], "projects": r["projects"],
        "meetings": short(r["meetings"], "title", "meeting_date"),
        "decisions": [d["description"] for d in r["decisions"][:20]],
        "open_questions": [q["text"] for q in r["open_questions"][:15]],
        "completed": short(r["completed"], "description", "who"),
        "new_work": short(r["created"], "description", "who", "due_date"),
        "overdue": short(r["overdue"], "description", "who", "due_date"),
        "due_next_7_days": short(r["due_soon"], "description", "who", "due_date"),
        "blocked": short(r["blocked"], "description", "who", "note"),
        "open_per_person": r["workload"][:15],
    }
    return llm.text(HIGHLIGHTS_PROMPT, json.dumps(data, ensure_ascii=False), max_tokens=1200).strip()


# ---------- Email ----------

def _d(iso):
    return date.fromisoformat(iso).strftime("%d %b") if iso else ""


def _task_line(t, due=True):
    extra = [t.get("who") or "unassigned"]
    if due and t.get("due_date"):
        extra.append(f"due {_d(t['due_date'])}")
    if t.get("jira_key"):
        extra.append(t["jira_key"])
    return f"• {t['description']} — {', '.join(extra)}"


def email_text(r, first_name):
    def section(title, rows, fn, limit=15):
        if not rows:
            return None
        more = f"\n…and {len(rows) - limit} more" if len(rows) > limit else ""
        return f"{title} ({len(rows)})\n" + "\n".join(fn(x) for x in rows[:limit]) + more

    c = r["counts"]
    span = "this month" if r["period"] == "month" else "this week"
    parts = [f"Hi {first_name},",
             f"Your {r['period']}ly report for {r['label']}"
             + (f" ({', '.join(r['projects'])})" if r["projects"] else "") + ":",
             f"{c['meetings']} meeting(s) · {c['completed']} finished · {c['created']} new · "
             f"{c['sent']} sent to Jira · {c['overdue']} overdue · {c['blocked']} blocked"]
    if r["highlights"]:
        parts.append("HIGHLIGHTS\n" + r["highlights"])
    parts += [x for x in [
        section("OVERDUE", r["overdue"], _task_line),
        section("BLOCKED", r["blocked"], lambda t: _task_line(t) + (f"\n   {t['note']}" if t.get("note") else "")),
        section("DUE IN THE NEXT 7 DAYS", r["due_soon"], _task_line),
        section(f"FINISHED {span.upper()}", r["completed"],
                lambda t: _task_line(t, due=False) + (" (closed in Jira)" if t.get("done_by_jira") else "")),
        section("NEW WORK", r["created"], _task_line),
        section("DECISIONS", r["decisions"], lambda d: f"• {d['description']} ({d['meeting_title']})"),
        section("STILL OPEN QUESTIONS", r["open_questions"], lambda q: f"• {q['text']}"),
        section("MEETINGS", r["meetings"], lambda m: f"• {_d(m['meeting_date'])} {m['title']}"),
        section("OPEN WORK PER PERSON", r["workload"],
                lambda w: f"• {w['who']}: {w['open']} open" + (f" (~{w['hours']:g} h)" if w.get("hours") else "")
                + (f", {w['high']} high priority" if w.get("high") else "")
                + (f", {w['overdue']} overdue" if w["overdue"] else "")),
    ] if x]
    if len(parts) == 3:
        parts.append(f"Nothing happened in your projects {span}.")
    return "\n\n".join(parts)


def send(pm, project_ids, day=None, period="week"):
    """Email the report of the week (or month) containing `day` to one PM. Returns the outbox status."""
    r = build(project_ids, day, period=period)
    name = "Monthly" if r["period"] == "month" else "Weekly"
    subject = (f"{name} report {r['label']}: "
               f"{r['counts']['completed']} finished, {r['counts']['overdue']} overdue")
    return mailer.send(pm["email"], subject, email_text(r, pm["full_name"].split()[0]),
                       kind="monthly" if r["period"] == "month" else "weekly",
                       link=f"{mailer.app_url()}/#/weekly?period={r['period']}&week={r['week_start']}",
                       link_text=f"Open the {name.lower()} report")


def pms():
    """PMs with an email and the ids of their projects."""
    with db.connect() as c:
        rows = db.rows(c.execute("""SELECT e.id, e.full_name, e.email, p.id AS project_id FROM projects p
                                    JOIN employees e ON e.id = p.pm_id WHERE e.active = 1 AND e.email IS NOT NULL"""))
    out = {}
    for r in rows:
        out.setdefault(r["id"], {"id": r["id"], "full_name": r["full_name"], "email": r["email"], "project_ids": []})
        out[r["id"]]["project_ids"].append(r["project_id"])
    return list(out.values())


def send_all(today=None, force=False, period="week"):
    """Report to every PM, once per PM per period unless force. Returns how many were sent.
    Weekly: the current week. Monthly: the month that just ended (sent at the start of the next)."""
    today = today or date.today()
    day = today if period == "week" else month_of(today)[0] - timedelta(days=1)
    key = week_of(day)[0].isoformat() if period == "week" else "M" + month_of(day)[0].isoformat()[:7]
    n = 0
    for pm in pms():
        with db.connect() as c:
            if not force and c.execute("SELECT 1 FROM weekly_log WHERE pm_id = ? AND week_start = ?",
                                       (pm["id"], key)).fetchone():
                continue
            c.execute("INSERT OR REPLACE INTO weekly_log (pm_id, week_start, sent_at) VALUES (?,?,?)",
                      (pm["id"], key, db.now()))
        try:
            send(pm, pm["project_ids"], day, period)
            n += 1
        except Exception:
            traceback.print_exc()
    return n


def schedule():
    """(weekday 0-6, hour) from .env: WEEKLY_REPORT_DAY (number or name, default Friday), WEEKLY_REPORT_HOUR."""
    raw = (os.environ.get("WEEKLY_REPORT_DAY") or "4").strip().lower()
    names = [d.lower() for d in DAYS]
    day = int(raw) if raw.isdigit() else next((i for i, n in enumerate(names) if n.startswith(raw[:3])), 4)
    hour = int(os.environ.get("WEEKLY_REPORT_HOUR") or "15")
    return min(max(day, 0), 6), min(max(hour, 0), 23)


def start_scheduler():
    if os.environ.get("WEEKLY_REPORT", "1") == "0":
        return

    def loop():
        while True:
            try:
                day, hour = schedule()
                now = datetime.now()
                # On the day from the hour, or later in the week if the computer was off then
                if now.weekday() > day or (now.weekday() == day and now.hour >= hour):
                    send_all()
                # Monthly: the previous month, from MONTHLY_REPORT_HOUR (default 9) on the 1st, or on
                # the 2nd/3rd if the computer was off (not later, so a restart mid-month sends nothing)
                if os.environ.get("MONTHLY_REPORT", "1") != "0" and (
                        1 < now.day <= 3 or (now.day == 1 and now.hour >= int(os.environ.get("MONTHLY_REPORT_HOUR") or "9"))):
                    send_all(period="month")
            except Exception:
                traceback.print_exc()
            time.sleep(15 * 60)

    threading.Thread(target=loop, daemon=True, name="weekly-report").start()
