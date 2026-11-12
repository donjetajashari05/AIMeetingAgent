"""
Extract a summary, decisions and tasks from a meeting transcript using Claude.

Takes the JSON produced by transcribe.py (uses speaker utterances if diarization
was run, otherwise plain segments) and writes <name>_notes.json.

Examples:
    python scripts/extract.py output/meeting1.json
    python scripts/extract.py output/meeting1.json --date 2026-11-03
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from typing import Literal

from dotenv import load_dotenv

from pydantic import BaseModel, Field

import llm

load_dotenv()

MODEL = llm.CLAUDE_MODEL

WEEKDAYS_SQ = ["e hënë", "e martë", "e mërkurë", "e enjte", "e premte", "e shtunë", "e diel"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------- Output schema ----------

class Decision(BaseModel):
    description: str = Field(description="What was decided, in one clear sentence, in English")
    quote: str = Field(description="Short verbatim quote from the transcript that supports it (original language)")


class Speaker(BaseModel):
    label: str = Field(description="Speaker label exactly as in the transcript, e.g. 'Folësi 2'")
    name: str = Field(description="Who it is: full name from the team list when clear, otherwise the label again")
    evidence: str = Field(description="Why, in a few English words (e.g. 'answers when called by name')")


class Topic(BaseModel):
    title: str = Field(description="Short name of the topic, in English (e.g. 'Payment API delays')")
    summary: str = Field(description="1-3 sentences in English: what was said and how it ended "
                                     "(decided, agreed task, left open, or just information)")


class Task(BaseModel):
    commitment: str = Field(description="Why this is a real task: who took it on or what the group agreed "
                                        "must be done, in a few English words (e.g. 'the person said they will do it')")
    description: str = Field(description="What still has to be done, as a concrete action in English "
                                         "(this becomes a ticket title, e.g. 'Fix the login page bug')")
    assignee: str | None = Field(description="Person responsible: their name if mentioned, "
                                             "otherwise the speaker label (e.g. 'Folësi 2'); null if nobody")
    due_date: str | None = Field(description="Deadline as YYYY-MM-DD, resolved against the meeting date; "
                                             "null if no deadline was mentioned")
    due_date_text: str | None = Field(description="The deadline exactly as it was said, e.g. 'deri të premten'")
    quote: str = Field(description="Short verbatim quote from the transcript that supports it (original language)")
    priority: Literal["high", "medium", "low"] = Field(description="How urgent/important it is (see PRIORITY rules)")
    priority_reason: str = Field(description="Why that priority, a few English words (e.g. 'blocks the release')")
    estimate_hours: float | None = Field(description="Rough effort in working hours (see ESTIMATE rules); "
                                                     "null if impossible to tell")
    continues: int | None = Field(description="The [id] of an item in <previous_work> that is the SAME piece of "
                                              "work (it was already planned in an earlier meeting); null if new")


class Unowned(BaseModel):
    description: str = Field(description="What has to be done, as a concrete action in English "
                                         "(like a ticket title, e.g. 'Fix the failing payment webhook')")
    why: str = Field(description="What was said about it and why it needs someone, one short English sentence")
    quote: str = Field(description="Short verbatim quote from the transcript that supports it (original language)")


class PreviousUpdate(BaseModel):
    item_id: int = Field(description="The [id] of the item in <previous_work>")
    status: str = Field(description="'done' if someone says it is finished, 'in_progress' if they are still on it, "
                                    "'blocked' if it is stuck, 'mentioned' if it only came up")
    note: str = Field(description="What was said about it, one short English sentence")
    quote: str = Field(description="Short verbatim quote from the transcript (original language)")


class MeetingNotes(BaseModel):
    speakers: list[Speaker] = Field(description="FIRST work out who each speaker label is. Empty if the "
                                                "transcript has no speaker labels.")
    summary: str = Field(description="Summary of the meeting in 3-6 sentences, in English")
    topics: list[Topic] = Field(description="What was discussed, topic by topic in meeting order (usually 2-8). "
                                            "All discussion goes here, including what did NOT become a task.")
    completed: list[str] = Field(description="Work people reported as ALREADY DONE (yesterday or before the "
                                             "meeting), one short English line each. These are NOT tasks.")
    decisions: list[Decision]
    tasks: list[Task] = Field(description="Only work that is NOT done yet: planned, in progress, next, or blocked")
    open_questions: list[str] = Field(description="Questions, blockers or topics left unresolved, in English")
    unowned: list[Unowned] = Field(description="Work the meeting said must be done (a problem, bug, request, "
                                               "follow-up) that NOBODY was given or took on. See NO OWNER rules.")
    previous_updates: list[PreviousUpdate] = Field(description="What this meeting said about items in "
                                                               "<previous_work>; empty if none was discussed")


# ---------- Prompt ----------

SYSTEM_PROMPT = """You extract structured meeting notes from transcripts of work meetings at a
software company. Meetings are in Albanian (often the Kosovo/Gheg dialect), English, or a mix.

LANGUAGE: write the summary, completed items, decisions, tasks and open questions in clear,
professional ENGLISH, whatever language the meeting was in. Tasks become Jira tickets, so a
task description is a short imperative ticket title ("Fix the database connection issues in
the user authentication API"). Only quotes and due_date_text stay exactly as spoken, in the
original language.

DONE vs TO DO (very important): many meetings are stand-ups where people first say what they
did yesterday, then what they will do today. Work that is already finished is NOT a task:
- Past/finished: "e kam kry", "e kam përfundu", "kam përfundu tiketën", "dje kam punu",
  "e kom bo", "e kemi mbyll", "u kry", "done", "finished", "e kam testu" -> put it in
  `completed` (and the summary), never in `tasks`.
- Still to do: "sot kam me...", "masnej/pastaj...", "duhet me...", "kam me e bo", "do e bëj",
  "jam tu punu", "e kam fillu", "next I'll", "tomorrow" -> a task.
- Started but not finished ("e kam kry pjesën kryesore, po kam probleme me ...", "besoj sot e
  përfundoj"): the REMAINING part is a task; the finished part goes to `completed`.
- A problem that blocks someone ("më kanë dalë probleme me database connections") becomes a
  task to resolve it when someone says they will handle it, otherwise an open question.

WHAT BECOMES A TASK (be strict, meetings are long and most talk is NOT a task):
First understand the whole conversation and group it into `topics`. Then create a task ONLY
when all of these are true:
  1. it is a concrete piece of work (something a person can do and finish);
  2. someone took it on ("unë e marr", "e boj unë", "po, e rregulloj"), was asked and agreed,
     or the group clearly agreed it must be done ("duhet me e bo deri…", "le ta bëjmë");
  3. it is not already finished;
  (when the group agreed it must be done but by the end NOBODY took it on, it is not a task:
   it goes to `unowned`, see NO OWNER)
  4. it is the team's own work (not something a client or another company will do, unless a
     team member must follow up).
NOT tasks (put them in `topics`, `decisions` or `open_questions` instead):
  - ideas, brainstorming and possibilities ("ndoshta", "a thua", "mundemi me…", "kishte qenë mirë");
  - opinions, explanations of how something works, status reports, demos, small talk;
  - questions nobody agreed to answer (-> open_questions), and work that must be done but that
    nobody took on (-> `unowned`, see NO OWNER);
  - proposals that were rejected, postponed with no plan, or left undecided;
  - general intentions without an action ("duhet me qenë ma të kujdesshëm").
The END of the discussion counts: if a task is proposed and later dropped ("jo, s'ka nevojë",
"e lajmë për ma vonë"), leave it out; if the owner or deadline changes later, use the final one;
if it was finished later in the same meeting, it goes to `completed`.
Merge everything said about the same work into ONE task. When in doubt, it is not a task:
a missing task is better than a wrong one. A long meeting often has only a few real tasks.

Rules:
- Only include decisions that were actually agreed, not ideas that were just mentioned.
- A task needs a concrete action. If someone volunteers ("I'll do it", "unë e marr")
  or is asked and accepts, they are the assignee.
- People speak informally, with English work terms ("qysh me e bo deploy", "kqyre ate
  taskun", "s'kom pas kohë") and the speech recognizer makes spelling mistakes. Understand
  the meaning; a vocabulary with the team's words and their meanings may be given.
- Speakers: before anything else, fill `speakers`. A label is a person when the conversation
  shows it: someone is addressed by name and that label answers ("<emri>, a mundesh…?" ->
  Folësi 2: "Po, e marr unë" means Folësi 2 is <emri>), or a speaker says their own name. The
  same label is the same person for the whole meeting. Then write assignees with the names
  from `speakers` (the label only when the person is unknown). The person who ANSWERS and
  accepts is the assignee, not someone who only offers to help.
- If a list of team members is given, write the assignee as the full name from that
  list when the transcript clearly refers to that person (a first name or nickname is
  enough if only one member matches it). Never assign a task to a listed member just
  because they are on the list.
- Resolve relative deadlines ("by Friday", "deri të premten", "next week", "nesër")
  to a concrete date using the meeting date and the calendar provided. "Next week"
  without a day means the Friday of next week. A weekday with "next"/"e ardhshme"/"tjetër"
  ("të mërkurën e ardhshme", "next Wednesday") means that day in the FOLLOWING week; a plain
  weekday ("të premten", "by Friday") means the next one coming. Keep the original wording in due_date_text.
- PRIORITY of each task:
  high = urgent or blocking: a production bug, a client is waiting or complaining, it blocks other
         people or a release, a deadline within ~2 working days, or it was called urgent
         ("urgjent", "sa ma shpejt", "asap", "prioritet", "s'pret", "po na bllokon");
  low = nice to have, cleanup, "kur të kesh kohë", "s'ka ngut", no deadline and nothing depends on it;
  medium = everything else (normal planned work). Do not make everything high.
- ESTIMATE (estimate_hours): the effort for one person, in working hours (1 day = 8 h). If someone
  said how long it takes ("dy ditë", "një orë", "gjysë dite"), use that. Otherwise give a rough
  realistic guess from the kind of work: a quick fix, call or email 0.5-2; a normal feature or bug
  2-8; bigger work 8-24; more than 40 only when it was described as very big. Use 0.5, 1, 2, 3, 4,
  6, 8, 12, 16, 24, 32, 40. Null only when the task is too vague to guess.
- NO OWNER (`unowned`, very important, the PM must never miss these): work that the meeting
  clearly said has to be done, or a real problem that needs action ("duhet me u rregullu",
  "dikush duhet me e kqyr", "kjo duhet me u bo", "s'po funksionon …", "klienti po pret …"),
  but by the END of the discussion nobody was given it and nobody took it on: nobody said
  "unë e marr", the question "kush e merr?" got no answer, or it was "dikush" / "na duhet
  dikush" / "e shohim ma vonë kush". Put each such piece of work in `unowned` (one per piece
  of work, concrete action in English), NOT in `tasks`. It may also be in `open_questions`.
  Not `unowned`: ideas nobody said must be done, things that were rejected, work someone
  took on (-> tasks), and work already finished.
- Do not invent anything that is not in the transcript. Empty lists are fine.
- Quotes must be copied verbatim from the transcript and kept short.

MEMORY OF EARLIER MEETINGS: <previous_work> (when given) lists work that is still open from
earlier meetings, each with an [id], plus earlier decisions and summaries. Use it to
understand references like "ai bug-u i javës së kaluar", "tiketa e login-it", "qajo që e
morëm përsipër":
- Someone says an earlier item is finished -> add it to `previous_updates` with status
  'done' (it may also go to `completed`). Do NOT create a new task for it.
- An earlier item is discussed as still in progress, blocked or moved -> `previous_updates`
  with that status. If NEW remaining work is agreed for it, you may also create a task and
  set `continues` to the item's id.
- A task that is the same work as an earlier item gets `continues` = that id.
- Only use ids that appear in <previous_work>. Only mark 'done' when the transcript clearly
  says so; when unsure, use 'mentioned'. Never copy earlier items into `tasks` just because
  they are listed."""


def calendar_hint(meeting_date: date, days=21):
    """List the next few weeks so relative dates resolve reliably."""
    lines = []
    for i in range(days):
        d = meeting_date + timedelta(days=i)
        weeks = (d - timedelta(days=d.weekday()) - (meeting_date - timedelta(days=meeting_date.weekday()))).days // 7
        week = {0: "this week", 1: "next week / java e ardhshme"}.get(weeks, f"in {weeks} weeks")
        tag = ", meeting day" if i == 0 else ""
        lines.append(f"{d.isoformat()} {WEEKDAYS_EN[d.weekday()]} / {WEEKDAYS_SQ[d.weekday()]} ({week}{tag})")
    return "\n".join(lines)


def transcript_text(data):
    """Turn transcribe.py output into the text the model reads."""
    if data.get("utterances"):
        lines = [f"[{u['start']:.0f}s] {u['speaker']}: {u['text']}" for u in data["utterances"]]
    else:
        lines = [f"[{s['start']:.0f}s] {s['text']}" for s in data["segments"]]
    return "\n".join(lines)


def load_transcript(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return transcript_text(data), data


def participants_text(people):
    """people: [{"full_name", "role", "aliases"}, ...]"""
    lines = []
    for p in people:
        extra = ", ".join(x for x in [p.get("role"), f"also called {p['aliases']}" if p.get("aliases") else None] if x)
        lines.append(f"- {p['full_name']}" + (f" ({extra})" if extra else ""))
    return "\n".join(lines)


# Long meetings are analyzed in parts, then the parts are merged into one result.
# Groq's free tier limits tokens per minute, so its parts are smaller. EXTRACT_PART_WORDS overrides.
PART_WORDS = {"groq": 2500, "anthropic": 30000}
OVERLAP_LINES = 3

MERGE_PROMPT = """You get the partial notes of ONE long meeting. The transcript was split into
consecutive parts and each part was analyzed on its own, in order (part 1 is the start).
Write the final notes for the whole meeting, following the same rules as before:
- One summary (3-6 sentences) of the whole meeting.
- People: speaker labels mean the same person in every part. Combine the `speakers` of all
  parts: if any part shows who a label is, use that name everywhere (assignees, topics,
  summary); never describe one person as two.
- Topics: one topic per subject. Merge topics about the same subject (also across parts) and
  fold small remarks into the bigger topic they belong to; keep meeting order; usually 2-8.
- Tasks: one task per piece of work. Merge duplicates (parts overlap slightly, and the same
  work is often discussed several times). The LATER parts win: a task dropped or rejected later
  is removed; a changed owner or deadline is updated; work finished later goes to `completed`.
  Keep only real tasks (someone took it on or the group agreed it must be done); move the rest
  to topics or open questions.
- Decisions, completed and open questions: merge duplicates; drop an open question that a later
  part answered.
- unowned: merge duplicates; drop one that someone took on in a later part (it becomes a task).
- previous_updates: at most one per item_id, with the latest status.
- Copy quotes, due_date_text, due dates, assignees and `continues` from the partial notes; do
  not invent new ones. For merged tasks keep the highest priority and the larger estimate
  (add them up only when the merged tasks are clearly separate pieces of work). Write in English except quotes."""


def _words(text):
    return len(text.split())


def split_transcript(transcript, max_words):
    """Split on line boundaries into parts of about max_words, with a few lines of overlap."""
    lines = transcript.split("\n")
    parts, cur, n = [], [], 0
    for line in lines:
        w = _words(line)
        if cur and n + w > max_words:
            parts.append(cur)
            cur = cur[-OVERLAP_LINES:]
            n = sum(_words(x) for x in cur)
        cur.append(line)
        n += w
    if cur and (not parts or cur != parts[-1][-len(cur):]):
        parts.append(cur)
    return ["\n".join(p) for p in parts]


def part_words():
    env = os.environ.get("EXTRACT_PART_WORDS")
    return int(env) if env and env.isdigit() else PART_WORDS.get(llm.provider(), 2500)


def extract(transcript, meeting_date: date, client=None, participants=None, glossary=None,
            previous=None, on_progress=None) -> MeetingNotes:
    """glossary: [{"term", "meaning"}] from data/fjalori.txt (slang, dialect, work terms).
    previous: text about earlier meetings (open work with [id]s, decisions), see app/memory.py.
    on_progress(text): called while a long meeting is analyzed part by part.
    Uses Claude or Groq (see llm.py); raises llm.LLMError with a readable message."""
    team = (f"Team members:\n<team_members>\n{participants_text(participants)}\n</team_members>\n\n"
            if participants else "")
    vocab = ""
    if glossary:
        from vocab import glossary_text
        vocab = f"Team vocabulary:\n<vocabulary>\n{glossary_text(glossary)}\n</vocabulary>\n\n"
    head = (f"Meeting date: {meeting_date.isoformat()} ({WEEKDAYS_EN[meeting_date.weekday()]})\n\n"
            f"Calendar:\n{calendar_hint(meeting_date)}\n\n"
            f"{team}{vocab}"
            + (f"Memory of earlier meetings:\n<previous_work>\n{previous}\n</previous_work>\n\n" if previous else ""))

    def one(text, part=None):
        note = (f"This is PART {part[0]} of {part[1]} of a long meeting (the parts follow each other and will "
                f"be merged later). Extract only what is in this part; a task may be confirmed, changed or "
                f"dropped in another part, so keep its quote.\n\n") if part else ""
        return llm.structured(SYSTEM_PROMPT, head + note + f"<transcript>\n{text}\n</transcript>",
                              MeetingNotes, max_tokens=16000, effort="medium")

    size = part_words()
    while True:
        parts = split_transcript(transcript, size) if _words(transcript) > size else [transcript]
        try:
            if len(parts) == 1:
                return validate_dates(one(parts[0]), meeting_date)
            partial = []
            for i, text in enumerate(parts, 1):
                if on_progress:
                    on_progress(f"Long meeting: analyzing part {i} of {len(parts)}…")
                partial.append(one(text, (i, len(parts))).model_dump())
            break
        except llm.TooLarge:
            if size <= 600:
                raise
            size //= 2  # the provider said the request is too big: use smaller parts

    if on_progress:
        on_progress(f"Long meeting: merging the {len(parts)} parts…")
    for p in partial:
        for t in p["tasks"]:
            t.pop("commitment", None)
    merged = llm.structured(
        SYSTEM_PROMPT + "\n\n" + MERGE_PROMPT,
        head + "<partial_notes>\n" + json.dumps(
            [{"part": i, **p} for i, p in enumerate(partial, 1)], ensure_ascii=False) + "\n</partial_notes>",
        MeetingNotes, max_tokens=16000, effort="medium")
    return validate_dates(merged, meeting_date)


NEXT_WORDS = ("ardhshme", "ardhshëm", "tjetër", "tjeter", "next")


def _next_week_fix(due, said, meeting_date):
    """'të mërkurën e ardhshme' / 'next Wednesday' means the following week: if the AI picked a
    day in the meeting's own week, move it 7 days on."""
    low = (said or "").lower()
    if not meeting_date or not any(w in low for w in NEXT_WORDS) or "javë" in low or "week" in low and "next week" not in low:
        return due
    if not any(d.lower() in low for d in WEEKDAYS_EN) and not any(d.split()[-1][:4] in low for d in WEEKDAYS_SQ):
        return due
    start = meeting_date - timedelta(days=meeting_date.weekday())
    if start <= due < start + timedelta(days=7):
        return due + timedelta(days=7)
    return due


def validate_dates(notes: MeetingNotes, meeting_date: date | None = None) -> MeetingNotes:
    """Drop any due_date that is not a valid YYYY-MM-DD date (keep the original text).
    Also writes the person's name where the AI left a speaker label it had identified."""
    names = {sp.label.strip().lower(): sp.name for sp in notes.speakers
             if sp.name and sp.name.strip().lower() != sp.label.strip().lower()}
    for t in notes.tasks:
        if t.assignee and t.assignee.strip().lower() in names:
            t.assignee = names[t.assignee.strip().lower()]
    for t in notes.tasks:
        if t.due_date:
            try:
                t.due_date = _next_week_fix(date.fromisoformat(t.due_date), t.due_date_text, meeting_date).isoformat()
            except ValueError:
                t.due_date = None
    return notes


def print_notes(notes: MeetingNotes):
    print("PËRMBLEDHJA\n" + notes.summary + "\n")
    print("VENDIMET")
    for d in notes.decisions or []:
        print(f"  - {d.description}")
    if not notes.decisions:
        print("  (asnjë)")
    print("\nDETYRAT")
    for t in notes.tasks or []:
        due = t.due_date or "pa afat"
        if t.due_date_text:
            due += f' ("{t.due_date_text}")'
        print(f"  - {t.description}\n      Përgjegjës: {t.assignee or '?'} | Afati: {due}")
    if not notes.tasks:
        print("  (asnjë)")
    if notes.unowned:
        print("\nPA PËRGJEGJËS")
        for u in notes.unowned:
            print(f"  - {u.description}")
    if notes.open_questions:
        print("\nÇËSHTJE TË HAPURA")
        for q in notes.open_questions:
            print(f"  - {q}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Extract summary, decisions and tasks")
    parser.add_argument("transcript", help="JSON file produced by transcribe.py")
    parser.add_argument("--date", default=None,
                        help="Meeting date YYYY-MM-DD (default: today), used to resolve deadlines")
    parser.add_argument("--out", default="output")
    args = parser.parse_args()

    if not Path(args.transcript).is_file():
        raise SystemExit(f"File not found: {args.transcript}")
    if not llm.available():
        raise SystemExit(llm.NO_KEY)
    meeting_date = date.fromisoformat(args.date) if args.date else date.today()

    transcript, data = load_transcript(args.transcript)
    print(f"Sending transcript to {llm.label()} ({len(transcript.split())} words)...\n")
    try:
        notes = extract(transcript, meeting_date)
    except llm.LLMError as e:
        raise SystemExit(str(e))

    print_notes(notes)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(args.transcript).stem
    out_path = out_dir / f"{name}_notes.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"source": args.transcript, "meeting_date": meeting_date.isoformat(),
                   "model": llm.label(), **notes.model_dump()}, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
