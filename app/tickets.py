"""
Tickets made from tasks (page "Tasks"): the PM picks one or more tasks and the AI
writes ONE Jira-ready ticket from them (title + description, priority + estimate).
Without an AI key the ticket is still made, from the tasks' own text.
"""
import sys
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


class TicketDraft(BaseModel):
    title: str = Field(description="Jira ticket title in English: a short imperative sentence, at most ~12 words")
    details: str = Field(description="Ticket description in English, plain text (see the instructions)")
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

Write in clear, professional ENGLISH:
- title: one short imperative sentence that covers ALL the tasks together
  (e.g. "Finish and test the payroll export API"). No ticket numbers, no names, no dates.
- details: plain text, no markdown headings. First 1-3 sentences of context: what is needed and why,
  using only what the tasks and quotes say. If there are several tasks, then a line "Scope:" followed by
  one "- " bullet per task (rewritten clearly, keep every piece of work). Finish with a line
  "Done when:" and 1-3 "- " bullets with concrete acceptance criteria that follow from the tasks.

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
    """Ticket text without the AI: the task itself, or the tasks as a scope list."""
    if len(sources) == 1:
        t = sources[0]
        return t["description"], t.get("details") or None
    title = sources[0]["description"]
    scope = "\n".join(f"- {t['description']}" for t in sources)
    return title, f"Scope:\n{scope}"


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
                return title, out.details.strip()[:4000] or None, True, est
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
    return ticket_id, ai_used
