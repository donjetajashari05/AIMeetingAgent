"""
Deadline reminders (run once a day with the daily reminder, from REMINDER_HOUR):

  - The day BEFORE a ticket's due date, the person it belongs to gets an email
    ("… is due tomorrow"). Only tickets the PM has reviewed (confirmed or sent to Jira);
    AI drafts from a meeting never email anyone.
  - When a ticket sent to Jira passes its due date and is not "Done" in Jira, the PM of the
    project gets one email listing those tickets (the Jira status is read first).

Each reminder goes out once per ticket and due date (table deadline_log), so moving a due
date sends a new reminder for the new date. DEADLINE_REMINDERS=0 turns both off.
"""
import os
import traceback
from datetime import date, timedelta

from . import db, jira, mailer

SCHEMA = """
CREATE TABLE IF NOT EXISTS deadline_log (
    task_id INTEGER NOT NULL,
    kind TEXT NOT NULL,          -- due_tomorrow | overdue
    due_date TEXT NOT NULL,
    sent_on TEXT NOT NULL,
    PRIMARY KEY (task_id, kind, due_date)
);
"""


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def enabled():
    return os.environ.get("DEADLINE_REMINDERS", "1") != "0"


def _logged(c, task_id, kind, due):
    return c.execute("SELECT 1 FROM deadline_log WHERE task_id = ? AND kind = ? AND due_date = ?",
                     (task_id, kind, due)).fetchone() is not None


def _log(c, task_id, kind, due, today):
    c.execute("INSERT OR IGNORE INTO deadline_log (task_id, kind, due_date, sent_on) VALUES (?,?,?,?)",
              (task_id, kind, due, today))


TICKETS = """
    SELECT t.id, t.description, t.details, t.due_date, t.jira_key, t.jira_url, t.jira_status,
           COALESCE(e.full_name, t.assignee) AS who, COALESCE(t.assignee_email, e.email) AS email,
           pr.name AS project, COALESCE(pm.id, sb.id) AS pm_id, COALESCE(pm.full_name, sb.full_name) AS pm_name,
           COALESCE(pm.email, sb.email) AS pm_email
    FROM tasks t JOIN meetings m ON m.id = t.meeting_id
    LEFT JOIN employees e ON e.id = t.assignee_id
    LEFT JOIN projects pr ON pr.id = m.project_id
    LEFT JOIN employees pm ON pm.id = pr.pm_id AND pm.active = 1
    LEFT JOIN employees sb ON sb.id = t.sent_by
    WHERE t.is_ticket = 1 AND t.status = 'open' AND t.merged_into IS NULL
"""


def due_tomorrow(today):
    tomorrow = (today + timedelta(days=1)).isoformat()
    with db.connect() as c:
        rows = db.rows(c.execute(TICKETS + " AND (t.approved_at IS NOT NULL OR t.jira_key IS NOT NULL) "
                                 "AND t.due_date = ? ORDER BY t.id", (tomorrow,)))
        return [r for r in rows if r["email"] and not _logged(c, r["id"], "due_tomorrow", r["due_date"])]


def overdue(today):
    with db.connect() as c:
        rows = db.rows(c.execute(TICKETS + " AND t.jira_key IS NOT NULL AND t.due_date < ? ORDER BY t.due_date",
                                 (today.isoformat(),)))
        return [r for r in rows if r["pm_email"] and not _logged(c, r["id"], "overdue", r["due_date"])]


def _fmt(d):
    return date.fromisoformat(d).strftime("%A %d %b")


def remind_people(today):
    sent = 0
    for t in due_tomorrow(today):
        lines = [f"Hi {(t['who'] or '').split(' ')[0] or 'there'},",
                 f"Your ticket “{t['description']}” is due tomorrow, {_fmt(t['due_date'])}."
                 + (f"\nProject: {t['project']}" if t["project"] else "")]
        if t["details"]:
            lines.append(t["details"])
        if t["jira_key"]:
            lines.append(f"Jira: {t['jira_key']}" + (f" ({t['jira_status']})" if t["jira_status"] else "")
                         + ". When it is finished, move it to Done in Jira.")
        lines.append("If the date has to change, tell your project manager.")
        mailer.send(t["email"], f"Due tomorrow: {t['description']}", "\n\n".join(lines), kind="deadline",
                    link=t["jira_url"] or None, link_text="Open in Jira" if t["jira_url"] else None)
        with db.connect() as c:
            _log(c, t["id"], "due_tomorrow", t["due_date"], today.isoformat())
        sent += 1
    return sent


def alert_pms(today):
    late = overdue(today)
    by_pm = {}
    for t in late:
        by_pm.setdefault(t["pm_email"], []).append(t)
    for email, tickets in by_pm.items():
        first = (tickets[0]["pm_name"] or "").split(" ")[0] or "there"
        rows = []
        for t in tickets:
            days = (today - date.fromisoformat(t["due_date"])).days
            rows.append(f"• {t['description']} — {t['who'] or 'unassigned'}, due {date.fromisoformat(t['due_date']).strftime('%d %b')} "
                        f"({days} day{'s' if days != 1 else ''} late), {t['jira_key']}: {t['jira_status'] or 'not Done'}")
        n = len(tickets)
        body = (f"Hi {first},\n\n{n} ticket{'s have' if n > 1 else ' has'} passed the due date and "
                f"{'are' if n > 1 else 'is'} not Done in Jira:\n\n" + "\n".join(rows)
                + "\n\nCheck with the people, move the due date, or close the ticket in Jira if it is finished.")
        mailer.send(email, f"Overdue: {n} ticket{'s' if n > 1 else ''} not Done in Jira", body, kind="overdue",
                    link=f"{mailer.app_url()}/#/tasks?tab=sent", link_text="Open the tickets")
        with db.connect() as c:
            for t in tickets:
                _log(c, t["id"], "overdue", t["due_date"], today.isoformat())
    return len(by_pm)


def run(today=None):
    """Both reminders; safe to call many times a day. Returns (people emailed, PMs emailed)."""
    if not enabled():
        return 0, 0
    today = today or date.today()
    try:
        jira.sync_status(force=True)  # a ticket closed in Jira is not overdue
    except Exception:
        traceback.print_exc()
    return remind_people(today), alert_pms(today)
