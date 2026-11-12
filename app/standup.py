"""
Daily standups: their own place, separate from meetings.

A standup is recorded like a meeting (kind = 'standup'), but it never makes tasks or tickets.
The AI writes a report instead: for each person what they finished, what they work on today,
what blocks them, and how their work is going (on track / at risk / blocked). The person's
plan from the previous standup is given to the AI, so it can tell what keeps being carried
over from day to day.
"""
import json
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
    quote TEXT
);
CREATE INDEX IF NOT EXISTS standup_updates_meeting ON standup_updates(meeting_id);
"""

STATUSES = ("on_track", "at_risk", "blocked")


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


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


class StandupNotes(BaseModel):
    speakers: list[Speaker] = Field(description="FIRST work out who each speaker label is; empty if no labels")
    summary: str = Field(description="2-4 sentences in English: how the team's work is going today, "
                                     "who is blocked or at risk")
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
- Do not invent anything. Someone who only asked questions or moderated gets no entry."""


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


def analyze(meeting_id, transcript, people, glossary=None):
    """Write the standup report (raises llm.LLMError). people: the team list for names."""
    m = db.get_meeting(meeting_id)
    team = "\n".join(f"- {p['full_name']}" + (f" (also called {p['aliases']})" if p.get("aliases") else "")
                     for p in people)
    vocab = ""
    if glossary:
        from vocab import glossary_text
        vocab = f"Team vocabulary:\n<vocabulary>\n{glossary_text(glossary)}\n</vocabulary>\n\n"
    prev = _previous(m)
    user = (f"Standup date: {m['meeting_date']}\n\n"
            + (f"Team members:\n<team_members>\n{team}\n</team_members>\n\n" if team else "")
            + vocab
            + (f"The previous standup:\n<previous>\n{prev}\n</previous>\n\n" if prev else "")
            + f"<transcript>\n{transcript}\n</transcript>")
    notes = llm.structured(PROMPT, user, StandupNotes, max_tokens=12000, effort="medium")
    save(meeting_id, notes.model_dump(), people)
    return notes


def save(meeting_id, notes, people):
    names = {s["label"].strip().lower(): s["name"] for s in notes.get("speakers") or []
             if s.get("name") and s["name"].strip().lower() != s["label"].strip().lower()}
    with db.connect() as c:
        c.execute("DELETE FROM standup_updates WHERE meeting_id = ?", (meeting_id,))
        c.execute("DELETE FROM chunks WHERE meeting_id = ? AND kind != 'utterance'", (meeting_id,))
        c.execute("UPDATE meetings SET summary = ?, open_questions = '[]' WHERE id = ?", (notes["summary"], meeting_id))
        index = [(notes["summary"], meeting_id, "summary", None, None)]
        for p in notes.get("people") or []:
            person = names.get(p["person"].strip().lower(), p["person"]).strip()[:120]
            status = p["status"] if p["status"] in STATUSES else "on_track"
            c.execute("INSERT INTO standup_updates (meeting_id, person, employee_id, done, today, blockers, carried_over, "
                      "status, status_reason, quote) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (meeting_id, person, org.match_employee(person, people),
                       *(json.dumps(p.get(k) or [], ensure_ascii=False) for k in ("done", "today", "blockers", "carried_over")),
                       status, p.get("status_reason"), p.get("quote")))
            text = "; ".join(x for x in [
                f"done: {', '.join(p.get('done') or [])}" if p.get("done") else "",
                f"today: {', '.join(p.get('today') or [])}" if p.get("today") else "",
                f"blocked by: {', '.join(p.get('blockers') or [])}" if p.get("blockers") else ""] if x)
            index.append((f"Standup, {person}: {text}", meeting_id, "summary", None, person))
        c.executemany("INSERT INTO chunks (text, meeting_id, kind, start, speaker) VALUES (?,?,?,?,?)", index)


# ---------- Reading ----------

def _decode(r):
    for k in ("done", "today", "blockers", "carried_over"):
        r[k] = json.loads(r[k] or "[]")
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
    [{"name", "employee_id", "entries": [{date, status, ...}] newest first}], people with problems first."""
    since = date.fromordinal(date.today().toordinal() - days).isoformat()
    only = "" if meeting_ids is None else f" AND m.id IN ({db._in(meeting_ids)})"
    proj = " AND m.project_id = ?" if project_id else ""
    with db.connect() as c:
        rows = [_decode(r) for r in db.rows(c.execute(f"""
            SELECT u.*, COALESCE(e.full_name, u.person) AS name, m.meeting_date, m.id AS meeting_id, m.title
            FROM standup_updates u JOIN meetings m ON m.id = u.meeting_id LEFT JOIN employees e ON e.id = u.employee_id
            WHERE m.kind = 'standup' AND m.meeting_date >= ?{only}{proj}
            ORDER BY m.meeting_date DESC, m.id DESC""", (since, *(meeting_ids or ()), *((project_id,) if project_id else ()))))]
    people = {}
    for r in rows:
        key = r["employee_id"] or r["name"].lower()
        p = people.setdefault(key, {"name": r["name"], "employee_id": r["employee_id"], "entries": []})
        p["entries"].append(r)
    rank = {"blocked": 0, "at_risk": 1, "on_track": 2}
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
    out.sort(key=lambda p: (rank.get(p["status"], 3), -p["carried_streak"], p["name"].lower()))
    return out
