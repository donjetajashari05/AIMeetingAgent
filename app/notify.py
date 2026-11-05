"""
Emails to project managers:
  - after a meeting of their project is processed: summary, decisions, tasks
  - a daily reminder (once a day, from REMINDER_HOUR, default 8): tasks due
    tomorrow or overdue in their projects
"""
import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from . import db, mailer

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminder_log (
    pm_id INTEGER NOT NULL,
    sent_on TEXT NOT NULL,
    PRIMARY KEY (pm_id, sent_on)
);
"""


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def _pm_of_meeting(c, meeting):
    if not meeting.get("project_id"):
        return None, None
    row = c.execute("""
        SELECT p.name AS project, e.id, e.full_name, e.email FROM projects p
        JOIN employees e ON e.id = p.pm_id WHERE p.id = ? AND e.active = 1 AND e.email IS NOT NULL
    """, (meeting["project_id"],)).fetchone()
    return (dict(row), row["project"]) if row else (None, None)


def meeting_processed(meeting_id):
    """Send the meeting summary to the project's PM (once per processing)."""
    m = db.meeting_detail(meeting_id)
    if not m:
        return
    with db.connect() as c:
        pm, project = _pm_of_meeting(c, m)
    if not pm:
        return
    lines = [f"Përshëndetje {pm['full_name'].split()[0]},",
             f"Mbledhja “{m['title']}” ({m['meeting_date']}) e projektit {project} u përpunua.",
             "PËRMBLEDHJA\n" + (m["summary"] or "—")]
    if m["decisions"]:
        lines.append("VENDIMET\n" + "\n".join(f"• {d['description']}" for d in m["decisions"]))
    if m["tasks"]:
        lines.append("DETYRAT\n" + "\n".join(
            f"• {t['description']} — {t['assignee_name'] or 'pa përgjegjës'}"
            + (f", afati {t['due_date']}" if t["due_date"] else "") for t in m["tasks"]))
        lines.append("Detyrat janë draft: kontrolloji, ndryshoji dhe konfirmoji te faqja e mbledhjes. Asgjë nuk shkon në Jira pa konfirmimin tënd.")
    if m["open_questions"]:
        lines.append("ÇËSHTJE TË HAPURA\n" + "\n".join(f"• {q}" for q in m["open_questions"]))
    mailer.send(pm["email"], f"Mbledhja u përpunua: {m['title']}", "\n\n".join(lines), kind="meeting",
                link=f"{mailer.app_url()}/#/meeting/{meeting_id}", link_text="Hap mbledhjen")
    db.update_meeting(meeting_id, notified_at=db.now())


def send_daily_reminders(today=None):
    """One digest per PM per day. Returns how many emails were sent."""
    today = today or date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    digests = []  # collected first; emails go out after the DB transaction is closed
    with db.connect() as c:
        pms = db.rows(c.execute("""
            SELECT DISTINCT e.id, e.full_name, e.email FROM projects p JOIN employees e ON e.id = p.pm_id
            WHERE e.active = 1 AND e.email IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM reminder_log r WHERE r.pm_id = e.id AND r.sent_on = ?)
        """, (today.isoformat(),)))
        for pm in pms:
            tasks = db.rows(c.execute("""
                SELECT t.description, t.due_date, COALESCE(e.full_name, t.assignee) AS who,
                       m.title AS meeting, p.name AS project, t.jira_key
                FROM tasks t JOIN meetings m ON m.id = t.meeting_id JOIN projects p ON p.id = m.project_id
                LEFT JOIN employees e ON e.id = t.assignee_id
                WHERE p.pm_id = ? AND t.status = 'open' AND t.due_date IS NOT NULL AND t.due_date <= ?
                ORDER BY t.due_date
            """, (pm["id"], tomorrow)))
            c.execute("INSERT OR IGNORE INTO reminder_log (pm_id, sent_on) VALUES (?, ?)", (pm["id"], today.isoformat()))
            if tasks:
                digests.append((pm, tasks))

    fmt = lambda t: (f"• {t['description']} — {t['who'] or 'pa përgjegjës'} ({t['project']}, afati {t['due_date']})"
                     + (f" [{t['jira_key']}]" if t["jira_key"] else ""))
    for pm, tasks in digests:
        late = [t for t in tasks if t["due_date"] < today.isoformat()]
        soon = [t for t in tasks if t["due_date"] >= today.isoformat()]
        parts = [f"Përshëndetje {pm['full_name'].split()[0]},"]
        if late:
            parts.append(f"ME VONESË ({len(late)})\n" + "\n".join(map(fmt, late)))
        if soon:
            parts.append(f"SKADOJNË SOT OSE NESËR ({len(soon)})\n" + "\n".join(map(fmt, soon)))
        subject = (f"Përkujtues: {len(late)} detyra me vonesë" if late else f"Përkujtues: {len(soon)} detyra skadojnë së shpejti")
        mailer.send(pm["email"], subject, "\n\n".join(parts), kind="reminder",
                    link=f"{mailer.app_url()}/#/tasks", link_text="Shiko detyrat")
    return len(digests)


def start_scheduler():
    hour = int(os.environ.get("REMINDER_HOUR", "8"))

    def loop():
        while True:
            try:
                if datetime.now().hour >= hour:
                    send_daily_reminders()
            except Exception:
                traceback.print_exc()
            time.sleep(15 * 60)

    threading.Thread(target=loop, daemon=True, name="reminders").start()
