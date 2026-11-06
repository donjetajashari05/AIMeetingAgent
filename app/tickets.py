"""
Tickets made from tasks (page "Tasks"): the PM picks one or more tasks and the AI
writes ONE Jira-ready ticket from them (title + description). Without an AI key the
ticket is still made, from the tasks' own text.
"""
import sys

from pydantic import BaseModel, Field

from . import db

sys.path.insert(0, str(db.ROOT / "scripts"))

import llm  # noqa: E402  (scripts/llm.py)


class TicketDraft(BaseModel):
    title: str = Field(description="Jira ticket title in English: a short imperative sentence, at most ~12 words")
    details: str = Field(description="Ticket description in English, plain text (see the instructions)")


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
Do not mention who is assigned or the due date: those are separate ticket fields."""


def _task_block(t):
    parts = [f"- Task: {t['description']}"]
    if t.get("details"):
        parts.append(f"  Notes: {t['details']}")
    if t.get("quote"):
        parts.append(f"  Said in the meeting: \"{t['quote']}\"")
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


def draft(sources, use_ai=True):
    """Return (title, details, ai_used)."""
    if use_ai and llm.available():
        try:
            out = llm.structured(SYSTEM_PROMPT, "<tasks>\n" + "\n".join(_task_block(t) for t in sources) + "\n</tasks>",
                                 TicketDraft, max_tokens=4000, effort="low")
            title = " ".join(out.title.split())[:300]
            if title:
                return title, out.details.strip()[:4000] or None, True
        except llm.LLMError:
            pass  # fall back to the tasks' own text
    title, details = _plain(sources)
    return title, details, False


def make_ticket(task_ids, use_ai=True):
    """Create one draft ticket from the given tasks. Returns (ticket_id, ai_used)."""
    sources = [db.get_task(i) for i in task_ids]
    title, details, ai_used = draft(sources, use_ai)
    # Same person on every task -> the ticket goes to them; otherwise the PM chooses on the Tickets page
    people = {t["assignee_id"] for t in sources}
    assignee_id = people.pop() if len(people) == 1 else None
    names = list(dict.fromkeys(t["assignee_name"] for t in sources if t["assignee_name"]))
    emails = {t["assignee_email"] for t in sources}
    dues = sorted(t["due_date"] for t in sources if t["due_date"])
    ticket_id = db.create_ticket(sources, title, details, assignee_id=assignee_id,
                                 assignee=", ".join(names) or None, due_date=dues[0] if dues else None)
    projects = {t["jira_project"] for t in sources}
    if len(projects) == 1 and (project := projects.pop()):
        db.edit_task(ticket_id, jira_project=project)
    email = emails.pop() if len(emails) == 1 else None
    if email and not assignee_id:
        db.edit_task(ticket_id, assignee_email=email)
    return ticket_id, ai_used
