"""
"Ask the archive": questions about past meetings, answered with numbered sources.

  1. Understand the question (AI): search words in English AND Albanian (notes are in English,
     transcripts in Albanian), the person, project and period it is about, and what it needs
     (tasks, decisions, meeting summaries, what was said).
  2. Gather sources the user is allowed to see: tasks and tickets (with owner, due date,
     status and Jira key), decisions, meeting summaries and transcript passages.
  3. Answer (AI) only from those sources, citing them as [1], [2]…; follow-up questions
     see the last few questions and answers.

Without an AI key it falls back to plain full-text search.
"""
import json
import sys
from datetime import date, timedelta

from pydantic import BaseModel, Field

from . import db

if str(db.ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(db.ROOT / "scripts"))

import llm  # noqa: E402  (scripts/llm.py)

MAX_TASKS, MAX_DECISIONS, MAX_MEETINGS, MAX_PASSAGES = 40, 25, 8, 10


# ---------- 1. Understand the question ----------

class Plan(BaseModel):
    keywords_en: list[str] = Field(description="2-8 English search words/short phrases for the topic "
                                               "(meeting notes are written in English)")
    keywords_sq: list[str] = Field(description="2-8 Albanian search words for the topic as people would say "
                                               "them in a meeting (Kosovo/Gheg forms welcome)")
    person: str | None = Field(description="Full name from the team list if the question is about one person, else null")
    project: str | None = Field(description="Project name from the project list if the question is about one, else null")
    date_from: str | None = Field(description="YYYY-MM-DD start of the period asked about, else null")
    date_to: str | None = Field(description="YYYY-MM-DD end of the period asked about, else null")
    task_status: str = Field(description="'open', 'done' or 'any': which tasks matter")
    needs: list[str] = Field(description="Any of: 'tasks', 'decisions', 'summaries', 'transcript'")


PLAN_PROMPT = """You turn a question about a software team's past meetings into a search plan.
Meeting notes (summaries, decisions, tasks) are in English; transcripts are in Albanian.
Resolve relative periods ("javën e kaluar", "last month", "sot", "dje") with today's date.
Use the conversation so far to understand follow-up questions ("po ajo tjetra?", "and him?").
'needs': tasks for who-does-what / what is open / deadlines / workload; decisions for what was
decided or agreed; summaries for what happened in meetings / overviews; transcript for who said
what or exact wording. Pick everything that could help. Reply in JSON."""


def _person_line(p):
    return p["full_name"] + (f" (also called {p['aliases']})" if p.get("aliases") else "")


def _plan(question, history, today, people, projects):
    calendar = "\n".join(f"{(today - timedelta(days=i)).isoformat()} {(today - timedelta(days=i)).strftime('%A')}"
                         for i in range(0, 8))
    convo = "".join(f"Q: {h['q']}\nA: {h['a'][:600]}\n" for h in history)
    user = (f"Today: {today.isoformat()} ({today.strftime('%A')})\nLast days:\n{calendar}\n\n"
            f"Team: {', '.join(_person_line(p) for p in people) or '-'}\n"
            f"Projects: {', '.join(projects) or '-'}\n\n"
            + (f"Conversation so far:\n{convo}\n" if convo else "")
            + f"Question: {question}")
    return llm.structured(PLAN_PROMPT, user, Plan, max_tokens=3000, effort="low")


# ---------- 2. Gather sources ----------

def _in(ids):
    return ",".join("?" * len(ids)) or "NULL"


def _scope(alias, meeting_ids, user_id=None):
    """SQL + args limiting rows to meetings the user may see (or tasks assigned to them)."""
    if meeting_ids is None:
        return "1 = 1", ()
    ids = tuple(meeting_ids)
    if user_id is not None:
        return f"({alias}.meeting_id IN ({_in(ids)}) OR {alias}.assignee_id = ?)", (*ids, user_id)
    return f"{alias}.meeting_id IN ({_in(ids)})", ids


def _period(plan, col):
    sql, args = [], []
    if plan.date_from:
        sql.append(f"{col} >= ?"); args.append(plan.date_from)
    if plan.date_to:
        sql.append(f"{col} <= ?"); args.append(plan.date_to)
    return (" AND " + " AND ".join(sql)) if sql else "", tuple(args)


def _valid_date(s):
    try:
        return date.fromisoformat(s).isoformat() if s else None
    except ValueError:
        return None


def _tasks(c, plan, meeting_ids, user_id, person, project_id):
    scope, args = _scope("t", meeting_ids, user_id)
    where = [scope, "t.merged_into IS NULL", "m.source IS NOT NULL"]
    if plan.task_status in ("open", "done"):
        where.append("t.status = ?"); args += (plan.task_status,)
    if person:
        where.append("(t.assignee_id = ? OR lower(t.assignee) = lower(?))"); args += (person["id"], person["full_name"])
    if project_id:
        where.append("m.project_id = ?"); args += (project_id,)
    per, pargs = _period(plan, "m.meeting_date")
    return db.rows(c.execute(f"""
        SELECT t.id, t.description, t.status, t.due_date, t.priority, t.estimate_hours, t.jira_key, t.jira_status, t.is_ticket, t.completed_at,
               COALESCE(e.full_name, t.assignee) AS who, m.id AS meeting_id, m.title AS meeting_title,
               m.meeting_date, m.source AS meeting_source, pr.name AS project_name
        FROM tasks t JOIN meetings m ON m.id = t.meeting_id
        LEFT JOIN employees e ON e.id = t.assignee_id LEFT JOIN projects pr ON pr.id = m.project_id
        WHERE {' AND '.join(where)}{per}
        ORDER BY t.status = 'done', COALESCE(t.due_date, '9999'), m.meeting_date DESC LIMIT ?""",
        (*args, *pargs, MAX_TASKS)))


def _decisions(c, plan, meeting_ids, project_id):
    scope, args = _scope("d", meeting_ids)
    per, pargs = _period(plan, "m.meeting_date")
    proj = " AND m.project_id = ?" if project_id else ""
    return db.rows(c.execute(f"""
        SELECT d.description, d.quote, m.id AS meeting_id, m.title AS meeting_title, m.meeting_date
        FROM decisions d JOIN meetings m ON m.id = d.meeting_id
        WHERE {scope}{proj}{per} ORDER BY m.meeting_date DESC, d.id DESC LIMIT ?""",
        (*args, *((project_id,) if project_id else ()), *pargs, MAX_DECISIONS)))


def _meetings(c, plan, meeting_ids, project_id, standups=True):
    only = "" if meeting_ids is None else f" AND m.id IN ({_in(meeting_ids)})"
    if not standups:
        only += " AND m.kind != 'standup'"  # standup reports are for PMs and administrators
    per, pargs = _period(plan, "m.meeting_date")
    proj = " AND m.project_id = ?" if project_id else ""
    return db.rows(c.execute(f"""
        SELECT m.id AS meeting_id, m.title AS meeting_title, m.meeting_date, m.summary, m.open_questions
        FROM meetings m WHERE m.summary IS NOT NULL AND m.source != 'manual'{only}{proj}{per}
        ORDER BY m.meeting_date DESC, m.id DESC LIMIT ?""",
        (*(meeting_ids or ()), *((project_id,) if project_id else ()), *pargs, MAX_MEETINGS)))


def _passages(plan, question, meeting_ids, tasks=True):
    words = " ".join([question, *plan.keywords_en, *plan.keywords_sq])
    found = db.search(words, limit=MAX_PASSAGES * 2, meeting_ids=meeting_ids, tasks=tasks)
    lo, hi = plan.date_from or "0000", plan.date_to or "9999"
    return [s for s in found if lo <= s["meeting_date"] <= hi][:MAX_PASSAGES]


def _task_text(t):
    bits = [t["who"] or "no owner", f"due {t['due_date']}" if t["due_date"] else "no due date",
            "done" if t["status"] == "done" else "open"]
    if t.get("priority"):
        bits.append(f"{t['priority']} priority")
    if t.get("estimate_hours"):
        bits.append(f"estimate {t['estimate_hours']:g} h")
    if t["jira_key"]:
        bits.append(f"Jira {t['jira_key']}" + (f" ({t['jira_status']})" if t["jira_status"] else ""))
    return f"{t['description']} — " + ", ".join(bits)


def _gather(plan, question, meeting_ids, user_id, tasks=True):
    """Numbered sources for the answer and for the page."""
    people = {e["full_name"].lower(): e for e in _employees()}
    person = people.get((plan.person or "").lower())
    with db.connect() as c:
        project_id = None
        if plan.project:
            r = c.execute("SELECT id FROM projects WHERE lower(name) = lower(?)", (plan.project,)).fetchone()
            project_id = r[0] if r else None
        needs = set(plan.needs or []) or {"tasks", "decisions", "summaries", "transcript"}
        if person:
            needs.add("tasks")
        if not tasks:
            needs.discard("tasks")
        sources = []
        if "tasks" in needs:
            for t in _tasks(c, plan, meeting_ids, user_id, person, project_id):
                manual = t["meeting_source"] == "manual"
                sources.append({"kind": "task", "task_id": t["id"], "meeting_id": t["meeting_id"],
                                "is_ticket": bool(t["is_ticket"]), "status": t["status"],
                                "meeting_title": "Written ticket" if manual else t["meeting_title"],
                                "meeting_date": t["meeting_date"], "text": _task_text(t)})
        if "decisions" in needs:
            for d in _decisions(c, plan, meeting_ids, project_id):
                sources.append({"kind": "decision", **d, "text": d["description"]})
        if "summaries" in needs or not sources:
            for m in _meetings(c, plan, meeting_ids, project_id, standups=tasks):
                oq = json.loads(m["open_questions"]) if m["open_questions"] else []
                text = m["summary"] + (f" Open questions: {'; '.join(oq)}" if oq else "")
                sources.append({"kind": "summary", "meeting_id": m["meeting_id"], "meeting_title": m["meeting_title"],
                                "meeting_date": m["meeting_date"], "text": text})
    seen = {(s["meeting_id"], s["text"]) for s in sources}
    for s in _passages(plan, question, meeting_ids, tasks):
        if (s["meeting_id"], s["text"]) not in seen:
            sources.append({**s, "kind": "said" if s["kind"] == "utterance" else s["kind"]})
    for i, s in enumerate(sources, 1):
        s["n"] = i
    return sources, person


def _employees():
    with db.connect() as c:
        return db.rows(c.execute("SELECT id, full_name, aliases FROM employees WHERE active = 1"))


def _projects():
    with db.connect() as c:
        return [r[0] for r in c.execute("SELECT name FROM projects ORDER BY name")]


# ---------- 3. Answer ----------

ANSWER_PROMPT = """You are the meeting assistant of a software team. Answer questions about past
meetings using ONLY the numbered sources given (tasks with owner/due date/status/Jira, decisions,
meeting summaries, and lines people said in Albanian).
- Answer in the language of the question (Albanian questions get Albanian answers).
- Be direct and specific: names, dates (e.g. 18 Nov), status, Jira keys. For several items use a
  short list with "- ". Keep it short: usually 1-6 sentences or a list. Plain text only: no
  **bold**, no headings, no tables. Albanian month names: janar, shkurt, mars, prill, maj,
  qershor, korrik, gusht, shtator, tetor, nëntor, dhjetor.
- Cite every fact with its source number in square brackets, e.g. [2] or [1][4].
- Today is given: say when something is overdue (open task with a due date before today).
- If the sources don't answer the question, say so plainly and say what you did find. Never guess."""


def _source_line(s):
    where = f"{s['meeting_title']} ({s['meeting_date']})"
    if s["kind"] == "task":
        label = "ticket" if s.get("is_ticket") else "task"
    elif s["kind"] == "said":
        label = f"said by {s.get('speaker') or 'someone'}"
    else:
        label = s["kind"]
    return f"[{s['n']}] {label} · {where}: {s['text']}"


def ask(question, has_key, meeting_ids=None, user_id=None, history=None, tasks=True):
    """meeting_ids: the meetings this user may see (None = all); user_id: their own tasks are always
    visible. history: [{"q", "a"}] of this conversation. tasks=False: no tasks or tickets in the answer
    (they are only for PMs and administrators). Raises llm.LLMError."""
    history = [h for h in (history or []) if h.get("q") and h.get("a")][-4:]
    if not has_key:
        sources = db.search(question, meeting_ids=meeting_ids, tasks=tasks)
        for i, s in enumerate(sources, 1):
            s["n"] = i
        if not sources:
            return {"answer": "I found nothing about this in the saved meetings.", "sources": [], "ai": False}
        return {"answer": "These are the closest passages found. Add GROQ_API_KEY or ANTHROPIC_API_KEY "
                          "for a full AI answer.", "sources": sources, "ai": False}

    today = date.today()
    plan = _plan(question, history, today, _employees(), _projects())
    plan.date_from, plan.date_to = _valid_date(plan.date_from), _valid_date(plan.date_to)
    if plan.task_status not in ("open", "done", "any"):
        plan.task_status = "any"
    sources, person = _gather(plan, question, meeting_ids, user_id, tasks)
    if not sources:
        return {"answer": "I found nothing about this in the meetings you can see.", "sources": [], "ai": True,
                "plan": plan.model_dump()}

    convo = "".join(f"Q: {h['q']}\nA: {h['a'][:800]}\n\n" for h in history)
    focus = []
    if person:
        focus.append(f"person: {person['full_name']}")
    if plan.date_from or plan.date_to:
        focus.append(f"period: {plan.date_from or '…'} to {plan.date_to or '…'}")
    user = (f"Today: {today.isoformat()} ({today.strftime('%A')})\n"
            + (f"Search focus: {', '.join(focus)}\n" if focus else "")
            + f"\n<sources>\n" + "\n".join(_source_line(s) for s in sources) + "\n</sources>\n\n"
            + (f"Conversation so far:\n{convo}" if convo else "")
            + f"Question: {question}")
    answer = llm.text(ANSWER_PROMPT, user, max_tokens=2500)
    return {"answer": answer, "sources": sources, "ai": True}
