"""
Tickets made from tasks (page "Tasks"): the PM picks one or more tasks and the AI
writes ONE Jira-ready ticket from them (title + description, priority + estimate).
Without an AI key the ticket is still made, from the tasks' own text.

Automatic tickets: after a meeting, every task that has a person becomes a draft ticket by itself
(see auto_for_meeting below), so the PM only checks and sends.
"""
import os
import sys
import threading
import traceback
from typing import Literal

from pydantic import BaseModel, Field

from . import db

sys.path.insert(0, str(db.ROOT / "scripts"))

import llm  # noqa: E402  (scripts/llm.py)


PRIORITY_RULES = """Priority: high = urgent or blocking (production bug, client waiting, blocks other people or a
release, deadline within ~2 working days, called urgent); low = nice to have / cleanup / no hurry;
medium = normal planned work. Do not make everything high.
Estimate: effort for one person in working hours (1 day = 8 h). Use what was said when there is a
number; otherwise a realistic rough guess (quick fix/call 0.5-2, normal feature or bug 2-8, bigger 8-24).
Use 0.5, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 40."""


# The description has the same sections as the team's Jira form. Stored as text with these
# headings (one per line), so the edit form, the Tickets page and Jira all show the same thing.
SECTIONS = ("Summary", "Context", "Acceptance criteria", "Other information")
NA = "N/A"


def compose(summary=None, context=None, criteria=(), other=None):
    """The four sections as one text (the `details` of a ticket). Empty sections say N/A, like in Jira."""
    crit = [" ".join(str(c).split()).lstrip("-• ").strip() for c in criteria or ()]
    crit = [c for c in crit if c and c.upper() != NA]
    parts = [("Summary", (summary or "").strip() or NA), ("Context", (context or "").strip() or NA),
             ("Acceptance criteria", "\n".join(f"- {c}" for c in crit) or NA),
             ("Other information", (other or "").strip() or NA)]
    return "\n\n".join(f"{h}\n{body}" for h, body in parts)[:4000]


def parse(details):
    """{section: text} from a ticket description. Older tickets (free text with "Scope:" /
    "Done when:") are mapped: the text goes to Summary, the "Done when" bullets to Acceptance criteria."""
    out = {h: "" for h in SECTIONS}
    text = (details or "").strip()
    if not text:
        return out
    lines = text.splitlines()
    if any(line.strip() in SECTIONS for line in lines):
        current = None
        for line in lines:
            if line.strip() in SECTIONS:
                current = line.strip()
            elif current:
                out[current] += line + "\n"
        out = {h: ("" if v.strip() == NA else v.strip()) for h, v in out.items()}
        return out
    head, _, done = text.partition("Done when:")
    out["Summary"] = head.strip()
    out["Acceptance criteria"] = done.strip()
    return out


def criteria_list(text):
    return [c.lstrip("-• ").strip() for c in (text or "").splitlines() if c.strip().lstrip("-• ").strip()]


class TicketDraft(BaseModel):
    title: str = Field(description="Jira ticket title in English: a short imperative phrase, at most ~12 words")
    summary: str = Field(description="1-2 English sentences: what has to be done")
    context: str = Field(description="Why it is needed / background, only from the input; empty if nothing was said")
    acceptance_criteria: list[str] = Field(description="1-5 concrete, checkable conditions for 'done', in English")
    other_information: str = Field(description="Anything else useful from the input (links, systems, people to ask, "
                                               "dependencies); empty if nothing")
    priority: Literal["high", "medium", "low"] = Field(description="Priority of the whole ticket (see the instructions)")
    priority_reason: str = Field(description="Why that priority, a few English words")
    estimate_hours: float | None = Field(description="Rough effort for the whole ticket in working hours; "
                                                     "null if impossible to tell")


class Estimate(BaseModel):
    priority: Literal["high", "medium", "low"]
    priority_reason: str = Field(description="Why that priority, a few English words")
    estimate_hours: float | None = Field(description="Rough effort in working hours; null if impossible to tell")


SYSTEM_PROMPT = """You turn tasks from work meetings at a software company into ONE Jira ticket.
The tasks were extracted from Albanian meetings; some fields may still be in Albanian.

Write in clear, professional ENGLISH, in the sections of the team's Jira form:
- title: one short imperative phrase that covers ALL the tasks together
  (e.g. "Finish and test the payroll export API"). No ticket numbers, no names, no dates.
- summary: 1-2 sentences, what has to be done.
- context: why it is needed or the background, using only what the tasks and quotes say. Empty if
  nothing was said (do not write filler).
- acceptance_criteria: 1-5 short, concrete, checkable conditions that follow from the tasks.
  If there are several tasks, every piece of work must be covered by a criterion.
- other_information: dependencies, systems, people to ask, links mentioned. Empty if nothing.

Never invent requirements, technologies, people or deadlines that are not in the input.
Do not mention who is assigned, the due date, the priority or the estimate in the text: those are
separate ticket fields. Fill priority and estimate_hours for the whole ticket: the tasks may already
have a suggestion (keep it unless together they clearly mean more work or more urgency).

""" + PRIORITY_RULES


def _task_block(t):
    parts = [f"- Task: {t['description']}"]
    if t.get("details"):
        parts.append(f"  Notes: {t['details']}")
    if t.get("quote"):
        parts.append(f"  Said in the meeting: \"{t['quote']}\"")
    if t.get("priority") or t.get("estimate_hours"):
        parts.append(f"  Suggested: priority {t.get('priority') or '?'}"
                     + (f" ({t['priority_reason']})" if t.get("priority_reason") else "")
                     + (f", estimate {t['estimate_hours']:g} h" if t.get("estimate_hours") else ""))
    if t.get("due_date"):
        parts.append(f"  Due: {t['due_date']}")
    parts.append(f"  Meeting: {t['meeting_title']} ({t['meeting_date']})")
    return "\n".join(parts)


def _plain(sources):
    """Ticket text without the AI, in the Jira sections: the tasks' own words."""
    notes = " ".join(t["details"] for t in sources if t.get("details"))
    summary = sources[0]["description"] if len(sources) == 1 else "; ".join(t["description"] for t in sources)
    return sources[0]["description"], compose(summary, notes, [t["description"] for t in sources])


def _details(out):
    """The AI's sections as the ticket text."""
    return compose(out.summary, out.context, out.acceptance_criteria, out.other_information)


def combined(sources):
    """Priority and estimate of several tasks without the AI: the highest priority, the estimates added up."""
    best = min((t for t in sources if t.get("priority") in db.PRIORITY_RANK),
               key=lambda t: db.PRIORITY_RANK[t["priority"]], default=None)
    hours = [t["estimate_hours"] for t in sources if t.get("estimate_hours")]
    return {"priority": best["priority"] if best else None,
            "priority_reason": best.get("priority_reason") if best else None,
            "estimate_hours": sum(hours) if hours else None}


def draft(sources, use_ai=True):
    """Return (title, details, ai_used, {priority, priority_reason, estimate_hours})."""
    if use_ai and llm.available():
        try:
            out = llm.structured(SYSTEM_PROMPT, "<tasks>\n" + "\n".join(_task_block(t) for t in sources) + "\n</tasks>",
                                 TicketDraft, max_tokens=4000, effort="low")
            title = " ".join(out.title.split())[:300]
            if title:
                est = {"priority": out.priority, "priority_reason": out.priority_reason.strip()[:200] or None,
                       "estimate_hours": out.estimate_hours}
                return title, _details(out), True, est
        except llm.LLMError:
            pass  # fall back to the tasks' own text
    title, details = _plain(sources)
    return title, details, False, combined(sources)


def suggest(title, details=None):
    """AI priority and estimate for one ticket written by hand. None without an AI key or on an error."""
    if not llm.available():
        return None
    try:
        out = llm.structured(
            "You set the priority and a rough effort estimate for a Jira ticket at a software company.\n"
            + PRIORITY_RULES,
            f"<ticket>\nTitle: {title}\n" + (f"Description: {details}\n" if details else "") + "</ticket>",
            Estimate, max_tokens=1500, effort="low")
    except llm.LLMError:
        return None
    return {"priority": out.priority, "priority_reason": out.priority_reason.strip()[:200] or None,
            "estimate_hours": out.estimate_hours}


def make_ticket(task_ids, use_ai=True):
    """Create one draft ticket from the given tasks. Returns (ticket_id, ai_used)."""
    sources = [db.get_task(i) for i in task_ids]
    title, details, ai_used, est = draft(sources, use_ai)
    return _save(sources, title, details, est), ai_used


def _save(sources, title, details, est):
    """Store one draft ticket made from these tasks. Returns its id."""
    # Same person on every task -> the ticket goes to them; otherwise the PM chooses on the Tickets page
    people = {t["assignee_id"] for t in sources}
    assignee_id = people.pop() if len(people) == 1 else None
    names = list(dict.fromkeys(t["assignee_name"] for t in sources if t["assignee_name"]))
    emails = {t["assignee_email"] for t in sources}
    dues = sorted(t["due_date"] for t in sources if t["due_date"])
    ticket_id = db.create_ticket(sources, title, details, assignee_id=assignee_id,
                                 assignee=", ".join(names) or None, due_date=dues[0] if dues else None, **est)
    projects = {t["jira_project"] for t in sources}
    if len(projects) == 1 and (project := projects.pop()):
        db.edit_task(ticket_id, jira_project=project)
    email = emails.pop() if len(emails) == 1 else None
    if email and not assignee_id:
        db.edit_task(ticket_id, assignee_email=email)
    return ticket_id


# ---------- Automatic tickets ----------
# After a meeting the PM no longer ticks tasks one by one: every task that has a person becomes a
# draft ticket in "Tickets to send" (related tasks of the same person become ONE ticket). Tasks
# without a person wait in Tasks; when the PM gives one a person, its ticket is made too.
# Nothing goes to Jira by itself: the PM still sends them (one click: "Send all ready").

def auto_enabled():
    return os.environ.get("AUTO_TICKETS", "1").strip().lower() not in ("0", "false", "no")


class AutoTicket(TicketDraft):
    task_numbers: list[int] = Field(description="The numbers of the tasks this ticket covers (from the input)")


class AutoTickets(BaseModel):
    tickets: list[AutoTicket]


AUTO_PROMPT = """You turn the tasks from ONE work meeting at a software company into Jira tickets.
The tasks were extracted from an Albanian meeting; some fields may still be in Albanian.

Group the tasks: tasks that are clearly the same piece of work for the SAME person go into one ticket
(e.g. "build the export" + "test the export"). Everything else gets its own ticket. Never put tasks of
different people in one ticket. Every task number goes into exactly one ticket.

For each ticket, write in clear, professional ENGLISH, in the sections of the team's Jira form:
- title: one short imperative phrase that covers its tasks (e.g. "Finish and test the payroll export API").
  No ticket numbers, no names, no dates.
- summary: 1-2 sentences, what has to be done.
- context: why it is needed or the background, using only what the tasks and quotes say. Empty if
  nothing was said (do not write filler).
- acceptance_criteria: 1-5 short, concrete, checkable conditions that follow from the tasks.
  If there are several tasks, every piece of work must be covered by a criterion.
- other_information: dependencies, systems, people to ask, links mentioned. Empty if nothing.

Never invent requirements, technologies, people or deadlines that are not in the input.
Do not mention who is assigned, the due date, the priority or the estimate in the text: those are
separate ticket fields. Each task may already have a suggested priority and estimate: keep them unless
together they clearly mean more work or more urgency.

""" + PRIORITY_RULES

_auto_lock = threading.Lock()  # one automatic run at a time: a task never ends up in two tickets


def _owner(t):
    return t.get("assignee_id") or (t.get("assignee_email") or "").lower() or None


def _loose(where, args):
    """Open tasks with a person that are not a ticket, not part of one and not in Jira."""
    with db.connect() as c:
        tasks = db.rows(c.execute(db.TASK_SELECT + f"""
            WHERE {where} AND t.status = 'open' AND t.is_ticket = 0 AND t.merged_into IS NULL
              AND t.merged_from IS NULL AND t.jira_key IS NULL AND m.source != 'manual'
              AND COALESCE(m.kind, 'meeting') != 'standup' ORDER BY t.id""", args))
    return [t for t in tasks if _owner(t)]


def _still_free(sources):
    ids = [t["id"] for t in sources]
    with db.connect() as c:  # the PM may have made a ticket by hand meanwhile
        free = {r[0] for r in c.execute(f"SELECT id FROM tasks WHERE id IN ({db._in(ids)}) AND merged_into IS NULL", ids)}
    return [t for t in sources if t["id"] in free]


def _groups(tasks):
    """[(sources, title, details, est)]: one AI call for all the tasks. Without the AI (or when it
    fails) each task becomes its own ticket from its own text."""
    groups, used = [], set()
    if llm.available():
        try:
            block = "\n".join(f"#{i} (person: {t['assignee_name'] or t['assignee_email']})\n{_task_block(t)}"
                              for i, t in enumerate(tasks, 1))
            out = llm.structured(AUTO_PROMPT, f"<tasks>\n{block}\n</tasks>", AutoTickets, max_tokens=16000, effort="low")
            for tk in out.tickets:
                nums = [n for n in dict.fromkeys(tk.task_numbers) if 1 <= n <= len(tasks) and n not in used]
                srcs = [tasks[n - 1] for n in nums]
                title = " ".join(tk.title.split())[:300]
                if not srcs or not title or len({_owner(t) for t in srcs}) > 1:
                    continue  # empty, or different people in one ticket: those tasks go one by one below
                used.update(nums)
                est = {"priority": tk.priority, "priority_reason": tk.priority_reason.strip()[:200] or None,
                       "estimate_hours": tk.estimate_hours}
                groups.append((srcs, title, _details(tk), est))
        except llm.LLMError:
            traceback.print_exc()
    for i, t in enumerate(tasks, 1):
        if i not in used:  # left out by the AI: like "Make ticket" on that one task
            title, details, _, est = draft([t], use_ai=bool(used))
            groups.append(([t], title, details, est))
    return groups


def auto_for_meeting(meeting_id):
    """Draft tickets from a meeting's tasks that have a person. Returns how many were made."""
    if not auto_enabled():
        return 0
    with _auto_lock:
        tasks = _loose("t.meeting_id = ?", (meeting_id,))
        made = 0
        for sources, title, details, est in _groups(tasks) if tasks else []:
            if sources := _still_free(sources):
                _save(sources, title, details, est)
                made += 1
        return made


def auto_for_task(task_id):
    """A task just got a person: make its ticket in the background (the page doesn't wait for the AI)."""
    if not auto_enabled():
        return

    def run():
        try:
            with _auto_lock:
                tasks = _loose("t.id = ?", (task_id,))
                if tasks:
                    title, details, _, est = draft(tasks)
                    if tasks := _still_free(tasks):
                        _save(tasks, title, details, est)
        except Exception:
            traceback.print_exc()

    threading.Thread(target=run, daemon=True).start()
