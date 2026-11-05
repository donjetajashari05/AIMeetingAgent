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

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

MODEL = "claude-opus-5-5"

WEEKDAYS_SQ = ["e hënë", "e martë", "e mërkurë", "e enjte", "e premte", "e shtunë", "e diel"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------- Output schema ----------

class Decision(BaseModel):
    description: str = Field(description="What was decided, in one clear sentence")
    quote: str = Field(description="Short verbatim quote from the transcript that supports it")


class Task(BaseModel):
    description: str = Field(description="What has to be done, as a concrete action")
    assignee: str | None = Field(description="Person responsible: their name if mentioned, "
                                             "otherwise the speaker label (e.g. 'Folësi 2'); null if nobody")
    due_date: str | None = Field(description="Deadline as YYYY-MM-DD, resolved against the meeting date; "
                                             "null if no deadline was mentioned")
    due_date_text: str | None = Field(description="The deadline exactly as it was said, e.g. 'deri të premten'")
    quote: str = Field(description="Short verbatim quote from the transcript that supports it")


class MeetingNotes(BaseModel):
    summary: str = Field(description="Summary of the meeting in 3-6 sentences")
    decisions: list[Decision]
    tasks: list[Task]
    open_questions: list[str] = Field(description="Questions or topics left unresolved")


# ---------- Prompt ----------

SYSTEM_PROMPT = """You extract structured meeting notes from transcripts of work meetings.
Meetings are in Albanian, English, or a mix of both. Write every field in the main
language of the meeting.

Rules:
- Only include decisions that were actually agreed, not ideas that were just mentioned.
- A task needs a concrete action. If someone volunteers ("I'll do it", "unë e marr")
  or is asked and accepts, they are the assignee.l
- Use a person's name when it is clear from the conversation who a speaker is
  (e.g. someone says "Arta, can you..." and Folësi 2 answers "Yes"). Otherwise use
  the speaker label as written in the transcript.
- If a list of team members is given, write the assignee as the full name from that
  list when the transcript clearly refers to that person (a first name or nickname is
  enough if only one member matches it). Never assign a task to a listed member just
  because they are on the list.
- Resolve relative deadlines ("by Friday", "deri të premten", "next week", "nesër")
  to a concrete date using the meeting date and the calendar provided. "Next week"
  without a day means the Friday of next week. Keep the original wording in due_date_text.
- Do not invent anything that is not in the transcript. Empty lists are fine.
- Quotes must be copied verbatim from the transcript and kept short."""


def calendar_hint(meeting_date: date, days=21):
    """List the next few weeks so relative dates resolve reliably."""
    lines = []
    for i in range(days):
        d = meeting_date + timedelta(days=i)
        tag = " (meeting day)" if i == 0 else ""
        lines.append(f"{d.isoformat()} {WEEKDAYS_EN[d.weekday()]} / {WEEKDAYS_SQ[d.weekday()]}{tag}")
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


def extract(transcript, meeting_date: date, client=None, participants=None) -> MeetingNotes:
    client = client or anthropic.Anthropic()
    team = (f"Team members:\n<team_members>\n{participants_text(participants)}\n</team_members>\n\n"
            if participants else "")
    user_message = (
        f"Meeting date: {meeting_date.isoformat()} ({WEEKDAYS_EN[meeting_date.weekday()]})\n\n"
        f"Calendar:\n{calendar_hint(meeting_date)}\n\n"
        f"{team}"
        f"<transcript>\n{transcript}\n</transcript>"
    )
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=MeetingNotes,
        output_config={"effort": "medium"},
        # If the model declines, the API retries on a fallback model in the same call
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise SystemExit("The model declined to process this transcript.")
    if response.stop_reason == "max_tokens":
        raise SystemExit("The response was cut off (max_tokens). Try a shorter transcript.")
    return validate_dates(response.parsed_output)


def validate_dates(notes: MeetingNotes) -> MeetingNotes:
    """Drop any due_date that is not a valid YYYY-MM-DD date (keep the original text)."""
    for t in notes.tasks:
        if t.due_date:
            try:
                date.fromisoformat(t.due_date)
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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is missing. Create a key at "
                         "https://platform.claude.com/settings/keys and put "
                         "ANTHROPIC_API_KEY=sk-ant-... in the .env file.")
    meeting_date = date.fromisoformat(args.date) if args.date else date.today()

    transcript, data = load_transcript(args.transcript)
    print(f"Sending transcript to {MODEL} ({len(transcript.split())} words)...\n")
    try:
        notes = extract(transcript, meeting_date)
    except anthropic.AuthenticationError:
        raise SystemExit("Invalid or missing ANTHROPIC_API_KEY. Put it in the .env file.")
    except anthropic.RateLimitError:
        raise SystemExit("Rate limit reached. Wait a moment and try again.")
    except anthropic.APIConnectionError:
        raise SystemExit("Could not reach the Anthropic API. Check the internet connection.")

    print_notes(notes)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(args.transcript).stem
    out_path = out_dir / f"{name}_notes.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"source": args.transcript, "meeting_date": meeting_date.isoformat(),
                   "model": MODEL, **notes.model_dump()}, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
