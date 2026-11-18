"""
Daily standups: their own place, separate from meetings.

A standup is recorded like a meeting (kind = 'standup'), but it never makes tasks or tickets.
The AI writes a report instead: for each person what they finished, what they work on today,
what blocks them, and how their work is going (on track / at risk / blocked). The person's
plan from the previous standup is given to the AI, so it can tell what keeps being carried
over from day to day.
"""
import json
import os
import re
import sys
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from . import db, org

sys.path.insert(0, str(db.ROOT / "scripts"))
import llm  # noqa: E402  (scripts/llm.py)

SCHEMA = """
CREATE TABLE IF NOT EXISTS standup_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    person TEXT NOT NULL,
    employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    done TEXT,            -- JSON list: finished since the last standup
    today TEXT,           -- JSON list: what they work on now
    blockers TEXT,        -- JSON list
    carried_over TEXT,    -- JSON list: planned at the previous standup and still not finished
    status TEXT NOT NULL, -- on_track | at_risk | blocked
    status_reason TEXT,
    quote TEXT,
    unclear INTEGER NOT NULL DEFAULT 0, -- they spoke, but the recording was too unclear to tell what
    tickets TEXT          -- JSON list of Jira keys their update is about
);
CREATE INDEX IF NOT EXISTS standup_updates_meeting ON standup_updates(meeting_id);
"""

STATUSES = ("on_track", "at_risk", "blocked")


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(standup_updates)")}
        if "unclear" not in cols:  # databases from before the column existed
            c.execute("ALTER TABLE standup_updates ADD COLUMN unclear INTEGER NOT NULL DEFAULT 0")
        if "tickets" not in cols:
            c.execute("ALTER TABLE standup_updates ADD COLUMN tickets TEXT")


# ---------- What the AI writes ----------

class Speaker(BaseModel):
    label: str = Field(description="Speaker label exactly as in the transcript, e.g. 'Folësi 2'")
    name: str = Field(description="Who it is: full name from the team list when clear, otherwise the label again")


class PersonUpdate(BaseModel):
    person: str = Field(description="The person's full name from the team list when clear, otherwise the speaker label")
    done: list[str] = Field(description="What they FINISHED since the last standup, short English lines")
    today: list[str] = Field(description="What they work on today / next, short English lines")
    blockers: list[str] = Field(description="What blocks or slows them (waiting for someone, a bug, access...), "
                                            "short English lines; empty if nothing")
    carried_over: list[str] = Field(description="Items from THEIR plan at the previous standup (see <previous>) "
                                                "that are still not finished; empty if none or no previous plan")
    status: Literal["on_track", "at_risk", "blocked"] = Field(
        description="on_track = work moves normally; at_risk = slipping (same work carried over for days, "
                    "problems, unsure about the deadline); blocked = cannot continue without help")
    status_reason: str = Field(description="Why that status, a few English words")
    quote: str = Field(description="Short verbatim quote from what they said (original language)")
    tickets: list[str] = Field(description="Keys from <work_items> (e.g. KAN-12) that this update is clearly about; "
                                           "empty if none")
    unclear: bool = Field(description="True when this person's part of the transcript is garbled (fully or in part): "
                                      "then done/today/blockers hold only what can be understood, possibly nothing")


class StandupNotes(BaseModel):
    speakers: list[Speaker] = Field(description="FIRST work out who each speaker label is; empty if no labels")
    summary: str = Field(description="2-4 sentences in English: what the team said they work on today. "
                                     "Only what was said, no judgement (no 'on track', 'at risk', 'blocked')")
    people: list[PersonUpdate] = Field(description="One entry per person who gave an update, in speaking order")


PROMPT = """You write the report of a DAILY STANDUP of a software team. People take turns and say
what they did yesterday, what they do today and what blocks them. The meeting is in Albanian
(often Kosovo/Gheg dialect), English, or a mix; the speech recognizer makes spelling mistakes.

Write everything in clear, short ENGLISH (only quotes stay as spoken). This is a report, NOT a
task list: nothing here becomes a ticket.

For each person who gives an update:
- done: what they finished ("e kam kry", "e kam përfundu", "dje e mbylla", "done").
- today: what they work on now or next ("sot kam me…", "jam tu punu në…", "vazhdoj me…").
  Work that was started but not finished is in `today`, not in `done`.
- blockers: what stops or slows them ("po pres…", "s'kam qasje", "më doli një bug", "jam bllokuar").
- carried_over: compare with THEIR plan in <previous> (the last standup). Items they planned
  then and that are still not finished. Only when the transcript makes it clear.
- status: blocked when they cannot continue without help; at_risk when the work slips (the same
  item is carried over again, new problems, doubt about a deadline); otherwise on_track.
- Use the full name from the team list when the conversation shows who the speaker is (they
  are called by name: "<emri>, ti?" and the next speaker answers; or they say their own name).
  The same label is the same person for the whole standup. Merge everything one person said
  into ONE entry, even when they speak several times.
- EVERY speaker label that gives an update gets an entry, also when you cannot tell who it is:
  then `person` is the label itself ("Folësi 3"); the PM names them later. Never drop someone
  because their name is unknown.
- When a speaker's part of the transcript is garbled, keep what CAN be understood: put only the
  pieces whose meaning is clear (a task, a feature, a bug, "high priority", "still investigating")
  into done/today/blockers, and set unclear = true with status_reason "Partly unclear in the
  recording". If nothing at all can be understood: unclear = true, empty lists, status on_track and
  status_reason "Not clear in the recording". Never guess or fill gaps: a short true line is better
  than a full invented one.
- <work_items> lists each person's current Jira issues (and SyncUp tickets). Use them to understand
  misheard words: "post cheks", "definishon mester" next to an issue "Post checks for old version"
  means that issue. When a line is clearly about an issue, start it with its key ("KAN-12: ...") and
  put the key in `tickets`.
- When the audio is unclear but the meaning is LIKELY from context (their work items, their plan
  at the previous standup, the team vocabulary), you may write it, ending the line with
  " (probably)". Only for things the person did talk about: never add a line just because an issue
  exists, and never list issues they did not mention.
- Do not invent anything. Someone who only asked questions or moderated (calls on people,
  "po, faleminderit", "kush vazhdon?") gets no entry."""


def _previous(m):
    """Each person's update at the last earlier standup of the same project/team."""
    if m.get("project_id"):
        scope, args = "pm.project_id = ?", (m["project_id"],)
    elif m.get("team_id"):
        scope, args = "pm.team_id = ?", (m["team_id"],)
    else:
        scope, args = "pm.project_id IS NULL AND pm.team_id IS NULL", ()
    with db.connect() as c:
        prev = c.execute(f"""
            SELECT pm.id, pm.meeting_date FROM meetings pm
            WHERE pm.kind = 'standup' AND pm.status = 'done' AND pm.id != ? AND {scope}
              AND (pm.meeting_date < ? OR (pm.meeting_date = ? AND pm.id < ?))
            ORDER BY pm.meeting_date DESC, pm.id DESC LIMIT 1""",
            (m["id"], *args, m["meeting_date"], m["meeting_date"], m["id"])).fetchone()
        if not prev:
            return None
        rows = db.rows(c.execute("SELECT person, today, blockers FROM standup_updates WHERE meeting_id = ?", (prev["id"],)))
    lines = [f"Standup of {prev['meeting_date']}:"]
    for r in rows:
        plan = "; ".join(json.loads(r["today"] or "[]")) or "—"
        block = "; ".join(json.loads(r["blockers"] or "[]"))
        lines.append(f"- {r['person']}: planned: {plan}" + (f" | blocked by: {block}" if block else ""))
    return "\n".join(lines) if rows else None


LEAD_ROLE = re.compile(r"\b(pm|project manager|product manager|scrum master|team lead)\b", re.I)


def leads(m, people):
    """Ids of the people who run the standup and give no update: the project's PM and anyone whose
    role is PM / manager / scrum master."""
    ids = {p["id"] for p in people if LEAD_ROLE.search(p.get("role") or "")}
    if m.get("project_id"):
        with db.connect() as c:
            row = c.execute("SELECT pm_id FROM projects WHERE id = ?", (m["project_id"],)).fetchone()
        if row and row[0]:
            ids.add(row[0])
    return ids


def _work_items(people, lead_ids):
    """Each team member's current work, for the AI to make sense of half-heard words: their Jira
    issues (key, title and status only) and their open SyncUp tickets.
    STANDUP_WORK_CONTEXT=0 in .env turns it off."""
    if os.environ.get("STANDUP_WORK_CONTEXT", "1") == "0":
        return ""
    from . import jira  # local import: jira imports db
    lines = []
    for p in people:
        if p["id"] in lead_ids:
            continue
        items = [f"{i['key']} {i['summary']} ({i['status']})" for i in jira.work_items(p.get("email"))]
        keys = {i.split()[0] for i in items}
        with db.connect() as c:
            local = db.rows(c.execute(
                "SELECT description, jira_key FROM tasks WHERE assignee_id = ? AND status = 'open' "
                "AND (is_ticket = 1 OR merged_into IS NULL) ORDER BY id DESC LIMIT 10", (p["id"],)))
        items += [(f"{t['jira_key']} " if t["jira_key"] else "") + t["description"][:200]
                  for t in local if not t["jira_key"] or t["jira_key"] not in keys]
        if items:
            lines.append(f"- {p['full_name']}: " + "; ".join(items[:15]))
    return "\n".join(lines)


def analyze(meeting_id, transcript, people, glossary=None):
    """Write the standup report (raises llm.LLMError). people: the team list for names."""
    m = db.get_meeting(meeting_id)
    lead_ids = leads(m, people)
    team = "\n".join(f"- {p['full_name']}" + (f" (also called {p['aliases']})" if p.get("aliases") else "")
                     + (" (runs the standup: PM, gives no update)" if p["id"] in lead_ids else "")
                     for p in people)
    vocab = ""
    if glossary:
        from vocab import glossary_text
        vocab = f"Team vocabulary:\n<vocabulary>\n{glossary_text(glossary)}\n</vocabulary>\n\n"
    prev = _previous(m)
    work = _work_items(people, lead_ids)
    user = (f"Standup date: {m['meeting_date']}\n\n"
            + (f"Team members:\n<team_members>\n{team}\n</team_members>\n\n" if team else "")
            + (f"What each person is working on:\n<work_items>\n{work}\n</work_items>\n\n" if work else "")
            + vocab
            + (f"The previous standup:\n<previous>\n{prev}\n</previous>\n\n" if prev else "")
            + f"<transcript>\n{transcript}\n</transcript>")
    notes = llm.structured(PROMPT, user, StandupNotes, max_tokens=12000, effort="medium")
    save(meeting_id, notes.model_dump(), people, skip=lead_ids)
    return notes


def save(meeting_id, notes, people, skip=()):
    """skip: employee ids that never get an entry (the PMs who run the standup)."""
    names = {s["label"].strip().lower(): s["name"] for s in notes.get("speakers") or []
             if s.get("name") and s["name"].strip().lower() != s["label"].strip().lower()}
    with db.connect() as c:
        c.execute("DELETE FROM standup_updates WHERE meeting_id = ?", (meeting_id,))
        c.execute("DELETE FROM chunks WHERE meeting_id = ? AND kind != 'utterance'", (meeting_id,))
        c.execute("UPDATE meetings SET summary = ?, open_questions = '[]' WHERE id = ?", (notes["summary"], meeting_id))
        index = [(notes["summary"], meeting_id, "summary", None, None)]
        for p in notes.get("people") or []:
            person = names.get(p["person"].strip().lower(), p["person"]).strip()[:120]
            employee_id = org.match_employee(person, people)
            if employee_id in skip:
                continue  # the PM runs the standup: no update of their own
            status = p["status"] if p["status"] in STATUSES else "on_track"
            c.execute("INSERT INTO standup_updates (meeting_id, person, employee_id, done, today, blockers, carried_over, "
                      "status, status_reason, quote, unclear, tickets) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (meeting_id, person, employee_id,
                       *(json.dumps(p.get(k) or [], ensure_ascii=False) for k in ("done", "today", "blockers", "carried_over")),
                       status, p.get("status_reason"), p.get("quote"), 1 if p.get("unclear") else 0,
                       json.dumps([k for k in p.get("tickets") or [] if re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", k)])))
            text = "; ".join(x for x in [
                f"done: {', '.join(p.get('done') or [])}" if p.get("done") else "",
                f"today: {', '.join(p.get('today') or [])}" if p.get("today") else "",
                f"blocked by: {', '.join(p.get('blockers') or [])}" if p.get("blockers") else ""] if x)
            index.append((f"Standup, {person}: {text}", meeting_id, "summary", None, person))
        c.executemany("INSERT INTO chunks (text, meeting_id, kind, start, speaker) VALUES (?,?,?,?,?)", index)


# ---------- Reading ----------

def _decode(r):
    for k in ("done", "today", "blockers", "carried_over", "tickets"):
        r[k] = json.loads(r.get(k) or "[]")
    return r


def updates(meeting_id):
    with db.connect() as c:
        return [_decode(r) for r in db.rows(c.execute("""
            SELECT u.*, COALESCE(e.full_name, u.person) AS name FROM standup_updates u
            LEFT JOIN employees e ON e.id = u.employee_id WHERE u.meeting_id = ? ORDER BY u.id""", (meeting_id,)))]


def list_standups(meeting_ids=None, limit=60):
    """Newest first, with how many people are blocked or at risk."""
    only = "" if meeting_ids is None else f" AND m.id IN ({db._in(meeting_ids)})"
    with db.connect() as c:
        return db.rows(c.execute(f"""
            SELECT m.id, m.title, m.meeting_date, m.status, m.progress, m.note, m.error, m.duration_sec,
                   m.summary, m.team_id, m.project_id, tm.name AS team_name, pr.name AS project_name,
                   (SELECT COUNT(*) FROM standup_updates u WHERE u.meeting_id = m.id) AS people,
                   (SELECT COUNT(*) FROM standup_updates u WHERE u.meeting_id = m.id AND u.status = 'blocked') AS blocked,
                   (SELECT COUNT(*) FROM standup_updates u WHERE u.meeting_id = m.id AND u.status = 'at_risk') AS at_risk
            FROM meetings m LEFT JOIN teams tm ON tm.id = m.team_id LEFT JOIN projects pr ON pr.id = m.project_id
            WHERE m.kind = 'standup'{only}
            ORDER BY m.meeting_date DESC, m.id DESC LIMIT ?""", (*(meeting_ids or ()), limit)))


def by_person(meeting_ids=None, days=14, project_id=None):
    """How work goes for each person over the last `days` days of standups:
    [{"name", "employee_id", "entries": [{date, ...}] newest first}], alphabetical.
    The PMs who run the standups are left out (also their entries from before that rule)."""
    since = date.fromordinal(date.today().toordinal() - days).isoformat()
    only = "" if meeting_ids is None else f" AND m.id IN ({db._in(meeting_ids)})"
    proj = " AND m.project_id = ?" if project_id else ""
    with db.connect() as c:
        rows = [_decode(r) for r in db.rows(c.execute(f"""
            SELECT u.*, COALESCE(e.full_name, u.person) AS name, e.role AS role, m.meeting_date, m.id AS meeting_id, m.title
            FROM standup_updates u JOIN meetings m ON m.id = u.meeting_id LEFT JOIN employees e ON e.id = u.employee_id
            LEFT JOIN projects pr ON pr.id = m.project_id
            WHERE m.kind = 'standup' AND m.meeting_date >= ? AND (pr.pm_id IS NULL OR u.employee_id IS NOT pr.pm_id)
              AND (u.unclear = 0 OR u.done != '[]' OR u.today != '[]' OR u.blockers != '[]'){only}{proj}
            ORDER BY m.meeting_date DESC, m.id DESC""", (since, *(meeting_ids or ()), *((project_id,) if project_id else ()))))]
    people = {}
    for r in rows:
        if LEAD_ROLE.search(r.get("role") or ""):
            continue
        key = r["employee_id"] or r["name"].lower()
        p = people.setdefault(key, {"name": r["name"], "employee_id": r["employee_id"], "entries": []})
        p["entries"].append(r)
    out = list(people.values())
    for p in out:
        latest = p["entries"][0]
        p["status"] = latest["status"]
        p["days_blocked"] = sum(1 for e in p["entries"] if e["status"] == "blocked")
        p["days_at_risk"] = sum(1 for e in p["entries"] if e["status"] == "at_risk")
        # How many standups in a row the same work was carried over (newest first)
        streak = 0
        for e in p["entries"]:
            if not e["carried_over"]:
                break
            streak += 1
        p["carried_streak"] = streak
    out.sort(key=lambda p: p["name"].lower())  # alphabetical: the page lists what people said, it does not rank them
    return out
