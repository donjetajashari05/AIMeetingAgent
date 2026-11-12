"""
Background processing: transcription -> (speakers) -> extraction with Claude.
Jobs run one at a time in a worker thread so the CPU isn't overloaded.
"""
import json
import os
import queue
import sys
import threading
import time
import traceback
from datetime import date
from pathlib import Path

from . import db, dedupe, learning, memory, notify, org, owners, standup, voices

sys.path.insert(0, str(db.ROOT / "scripts"))

import correct as corrector  # noqa: E402  (scripts/correct.py)
import extract as extractor  # noqa: E402  (scripts/extract.py)
import llm  # noqa: E402  (scripts/llm.py)
import vocab  # noqa: E402  (scripts/vocab.py)

_jobs: "queue.Queue[tuple[str, int]]" = queue.Queue()


def has_claude_key():
    """True when any language model is configured (Claude or Groq; see scripts/llm.py)."""
    return llm.available()


def has_hf_token():
    return bool(os.environ.get("HF_TOKEN"))


def enqueue(meeting_id, job="process"):
    _jobs.put((job, meeting_id))


def start_worker():
    threading.Thread(target=_worker, daemon=True, name="pipeline").start()


def _worker():
    while True:
        job, meeting_id = _jobs.get()
        try:
            if job == "process":
                _process(meeting_id)
            elif job == "extract":
                _extract(meeting_id)
        except Exception as e:  # never let the worker die
            traceback.print_exc()
            db.update_meeting(meeting_id, status="error", error=str(e)[:500])


def _process(meeting_id):
    m = db.get_meeting(meeting_id)
    if not m:
        return

    if m["source"] == "json":
        with open(m["file_path"], encoding="utf-8") as f:
            data = json.load(f)
        if not (data.get("segments") or data.get("utterances")):
            raise ValueError("The JSON file has no 'segments' or 'utterances'.")
        db.save_transcript(meeting_id, data)
    else:
        data = _transcribe(meeting_id, m)

    _extract(meeting_id, data)


def _context(m):
    """Vocabulary (data/fjalori.txt), the meeting's people and the project names."""
    people = [p["full_name"] for p in org.participants(m["team_id"], m["project_id"])][:25]
    with db.connect() as c:
        projects = [r[0] for r in c.execute("SELECT name FROM projects ORDER BY id")][:15]
    glossary = vocab.load_glossary()
    # Words learned from the PM's transcript fixes come first, so they fit in Whisper's hotwords
    known = {g["term"].lower() for g in glossary}
    learned = [{"term": w, "meaning": None} for w in learning.hotword_terms() if w.lower() not in known]
    return learned + glossary, people, projects


def vocabulary_prompt(m):
    """Hint for Whisper: the team's names, project names and the words in data/fjalori.txt."""
    return vocab.whisper_prompt(*_context(m))


def _transcribe(meeting_id, m):
    import transcribe as transcriber  # scripts/transcribe.py (loads faster-whisper)

    db.update_meeting(meeting_id, status="transcribing", progress=0, note="Loading the model…", error=None)
    state = {"last": 0.0}

    def on_info(info):
        db.update_meeting(meeting_id, duration_sec=round(info.duration, 1), language=info.language,
                          note=f"Language: {info.language}")

    def on_segment(seg, info):
        if time.time() - state["last"] > 1:  # don't write to the DB on every segment
            state["last"] = time.time()
            db.update_meeting(meeting_id, progress=min(seg["end"] / max(info.duration, 1), 0.99))

    def on_diarize():
        db.update_meeting(meeting_id, status="diarizing", progress=1, note="Identifying speakers…")

    glossary, people, projects = _context(m)
    data = transcriber.transcribe_audio(
        m["file_path"], model_name=m["model"], language=m["language_hint"] or None,
        diarize=has_hf_token(), diarize_required=False, num_speakers=m.get("speakers_hint") or None,
        on_info=on_info, on_segment=on_segment, on_diarize=on_diarize,
        initial_prompt=vocab.whisper_prompt(glossary, people, projects),
        hotwords=vocab.hotwords(glossary, people, projects),
    )
    # Mistakes the team already fixed by hand on earlier meetings are fixed again here
    data["learned_fixes"] = learning.apply_to_data(data)
    # Known voices get their names; the voice fingerprints never stay in the transcript
    try:
        known = voices.identify(meeting_id, data)
        if known:
            db.update_meeting(meeting_id, note=f"Recognized {known} {'person' if known == 1 else 'people'} by voice")
    except Exception:
        traceback.print_exc()  # recognition is a help: it must never stop the meeting
    finally:
        data.pop("speaker_embeddings", None)
    db.save_transcript(meeting_id, data)
    return data


def _link_to_memory(notes, items, closed=()):
    """A new task that is the same work as an earlier open item is not created again: it becomes an
    update on that item. (If the earlier item is being closed, the task is new remaining work and stays.)
    The same work as a ticket that was already finished reopens that ticket instead of a new one.
    Returns (the updates for memory.save(), [(ticket, note, quote)] for memory.save_reopened())."""
    known = {t["id"] for t in items}
    updates = [u for u in notes.pop("previous_updates", None) or [] if u.get("item_id") in known]
    by_id = {u["item_id"]: u for u in updates}
    kept, reopened = [], {}
    for t in dedupe.collapse(notes["tasks"]):  # the same task twice in this meeting
        prev, repeat = t.pop("continues", None), False
        if prev not in known:
            # The AI didn't link it: compare the text with the earlier open work as well
            match = dedupe.best_match(t["description"], items, t.get("assignee"))
            prev, repeat = (match["id"], True) if match else (None, False)
        if prev not in known:
            # Finished before as a ticket: reopen that ticket, no second one
            done = dedupe.best_match(t["description"], closed, t.get("assignee"))
            if done:
                due = f" (due {t['due_date']})" if t.get("due_date") else ""
                step = f"Came up again: {t['description']}{due}"
                if done["id"] in reopened:
                    old = reopened[done["id"]]
                    reopened[done["id"]] = (done, f"{old[1]} {step}", old[2] or t.get("quote"))
                else:
                    reopened[done["id"]] = (done, step, t.get("quote"))
                continue
        if prev in known and (by_id.get(prev) or {}).get("status") != "done":
            due = f" (due {t['due_date']})" if t.get("due_date") else ""
            step = f"{'Came up again' if repeat else 'Next step'}: {t['description']}{due}"
            if prev in by_id:
                u = by_id[prev]
                u["note"] = f"{u.get('note') or ''} {step}".strip()
            else:
                by_id[prev] = {"item_id": prev, "status": "mentioned" if repeat else "in_progress", "note": step,
                               "quote": t.get("quote")}
                updates.append(by_id[prev])
            continue
        kept.append(t)
    notes["tasks"] = kept
    return updates, list(reopened.values())


def _extract(meeting_id, data=None):
    m = db.get_meeting(meeting_id)
    if data is None:
        data = json.loads(m["transcript"])

    if not has_claude_key():
        db.update_meeting(meeting_id, status="needs_key", progress=1,
                          note="The transcript is ready. Add GROQ_API_KEY (free) or ANTHROPIC_API_KEY for decisions and tasks.")
        return

    people = org.participants(m["team_id"], m["project_id"])
    glossary = vocab.load_glossary()

    # 1) Fix misheard words (once per meeting; "Analyze again" doesn't redo it)
    if data.get("corrected") is None and os.environ.get("TRANSCRIPT_CORRECTION", "1") != "0":
        db.update_meeting(meeting_id, status="extracting", progress=1, error=None,
                          note=f"{llm.label()} is fixing misheard words…")
        try:
            corrector.apply_corrections(
                data, glossary, [p["full_name"] for p in people], learned=learning.examples(),
                on_progress=lambda d, t: db.update_meeting(meeting_id, note=f"{llm.label()} is correcting the transcript ({d}/{t} lines)…"))
            db.save_transcript(meeting_id, data)  # also re-indexes search with the corrected text
        except Exception:
            traceback.print_exc()  # a failed correction must not block the analysis

    # A daily standup is a report per person: no tasks, tickets or meeting memory
    if m.get("kind") == "standup":
        db.update_meeting(meeting_id, status="extracting", progress=1, error=None,
                          note=f"{llm.label()} is writing the standup report…")
        notes = standup.analyze(meeting_id, extractor.transcript_text(data), people, glossary)
        blocked = sum(1 for p in notes.people if p.status == "blocked")
        db.update_meeting(meeting_id, status="done", progress=1,
                          note=f"{blocked} blocked" if blocked else None)
        return

    # 2) Summary, decisions, tasks
    db.update_meeting(meeting_id, status="extracting", progress=1, note=f"{llm.label()} is extracting decisions and tasks…",
                      error=None)
    memory.undo(meeting_id)  # "Analyze again": earlier tasks this meeting closed are open again
    mem = memory.context(m)
    try:
        notes = extractor.extract(extractor.transcript_text(data), date.fromisoformat(m["meeting_date"]),
                                  participants=people, glossary=glossary, previous=mem["text"] or None,
                                  on_progress=lambda text: db.update_meeting(meeting_id, note=text))
    except SystemExit as e:  # extract.py signals refusals / truncation this way
        raise RuntimeError(str(e))
    notes = notes.model_dump()
    for t in notes["tasks"]:
        t.pop("commitment", None)  # only there to make the AI justify each task
    updates, reopened = _link_to_memory(notes, mem["items"], memory.closed_tickets(m))
    db.save_notes(meeting_id, notes)
    closed = memory.save(meeting_id, updates, mem["items"])
    memory.save_reopened(meeting_id, reopened)
    org.link_assignees(meeting_id, people)
    # Work nobody took on goes to "Needs an owner"; work taken on here closes earlier such issues
    owners.save(meeting_id, notes.get("unowned"))
    owners.resolve_from_meeting(meeting_id)
    no_owner = owners.count({meeting_id})

    note = None
    if data.get("diarization_error"):
        note = "No speakers: " + data["diarization_error"]
    elif no_owner:
        note = f"{no_owner} item{'s' if no_owner > 1 else ''} without an owner: give {'them' if no_owner > 1 else 'it'} to someone."
    elif closed:
        note = f"{closed} earlier task{'s' if closed > 1 else ''} marked done from this meeting."
    db.update_meeting(meeting_id, status="done", progress=1, note=note)
    try:
        notify.meeting_processed(meeting_id)  # email the project's PM
    except Exception:
        traceback.print_exc()
