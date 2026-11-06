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

from . import db, learning, notify, org

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
        diarize=has_hf_token(), diarize_required=False,
        on_info=on_info, on_segment=on_segment, on_diarize=on_diarize,
        initial_prompt=vocab.whisper_prompt(glossary, people, projects),
        hotwords=vocab.hotwords(glossary, people, projects),
    )
    # Mistakes the team already fixed by hand on earlier meetings are fixed again here
    data["learned_fixes"] = learning.apply_to_data(data)
    db.save_transcript(meeting_id, data)
    return data


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

    # 2) Summary, decisions, tasks
    db.update_meeting(meeting_id, status="extracting", progress=1, note=f"{llm.label()} is extracting decisions and tasks…",
                      error=None)
    try:
        notes = extractor.extract(extractor.transcript_text(data), date.fromisoformat(m["meeting_date"]),
                                  participants=people, glossary=glossary)
    except SystemExit as e:  # extract.py signals refusals / truncation this way
        raise RuntimeError(str(e))
    db.save_notes(meeting_id, notes.model_dump())
    org.link_assignees(meeting_id, people)

    note = None
    if data.get("diarization_error"):
        note = "No speakers: " + data["diarization_error"]
    db.update_meeting(meeting_id, status="done", progress=1, note=note)
    try:
        notify.meeting_processed(meeting_id)  # email the project's PM
    except Exception:
        traceback.print_exc()
