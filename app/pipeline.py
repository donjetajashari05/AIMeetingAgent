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

from . import db, notify, org

sys.path.insert(0, str(db.ROOT / "scripts"))

import extract as extractor  # noqa: E402  (scripts/extract.py)

_jobs: "queue.Queue[tuple[str, int]]" = queue.Queue()


def has_claude_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


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
            raise ValueError("Skedari JSON nuk ka 'segments' ose 'utterances'.")
        db.save_transcript(meeting_id, data)
    else:
        data = _transcribe(meeting_id, m)

    _extract(meeting_id, data)


GLOSSARY_FILE = db.ROOT / "data" / "fjalori.txt"


def vocabulary_prompt(m):
    """Hint for Whisper: the team's names, project names and the words in data/fjalori.txt.
    Whisper reads only ~200 tokens of it, so it is kept short."""
    words = []
    if GLOSSARY_FILE.is_file():
        for line in GLOSSARY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                words += [w.strip() for w in line.split(",") if w.strip()]
    people = [p["full_name"] for p in org.participants(m["team_id"], m["project_id"])][:25]
    with db.connect() as c:
        projects = [r[0] for r in c.execute("SELECT name FROM projects ORDER BY id")][:15]
    prompt = "Mbledhje pune në shqip (dialekt kosovar), me fjalë në anglisht."
    if people:
        prompt += " Pjesëmarrës: " + ", ".join(people) + "."
    if projects:
        prompt += " Projekte: " + ", ".join(projects) + "."
    if words:
        prompt += " " + ", ".join(dict.fromkeys(words)) + "."
    return prompt[:800]


def _transcribe(meeting_id, m):
    import transcribe as transcriber  # scripts/transcribe.py (loads faster-whisper)

    db.update_meeting(meeting_id, status="transcribing", progress=0, note="Po ngarkohet modeli…", error=None)
    state = {"last": 0.0}

    def on_info(info):
        db.update_meeting(meeting_id, duration_sec=round(info.duration, 1), language=info.language,
                          note=f"Gjuha: {info.language}")

    def on_segment(seg, info):
        if time.time() - state["last"] > 1:  # don't write to the DB on every segment
            state["last"] = time.time()
            db.update_meeting(meeting_id, progress=min(seg["end"] / max(info.duration, 1), 0.99))

    def on_diarize():
        db.update_meeting(meeting_id, status="diarizing", progress=1, note="Po identifikohen folësit…")

    data = transcriber.transcribe_audio(
        m["file_path"], model_name=m["model"], language=m["language_hint"] or None,
        diarize=has_hf_token(), diarize_required=False,
        on_info=on_info, on_segment=on_segment, on_diarize=on_diarize,
        initial_prompt=vocabulary_prompt(m),
    )
    db.save_transcript(meeting_id, data)
    return data


def _extract(meeting_id, data=None):
    m = db.get_meeting(meeting_id)
    if data is None:
        data = json.loads(m["transcript"])

    if not has_claude_key():
        db.update_meeting(meeting_id, status="needs_key", progress=1,
                          note="Transkripti është gati. Shto ANTHROPIC_API_KEY për vendimet dhe detyrat.")
        return

    db.update_meeting(meeting_id, status="extracting", progress=1, note="Claude po nxjerr vendimet dhe detyrat…",
                      error=None)
    people = org.participants(m["team_id"], m["project_id"])
    try:
        notes = extractor.extract(extractor.transcript_text(data), date.fromisoformat(m["meeting_date"]),
                                  participants=people)
    except SystemExit as e:  # extract.py signals refusals / truncation this way
        raise RuntimeError(str(e))
    db.save_notes(meeting_id, notes.model_dump())
    org.link_assignees(meeting_id, people)

    note = None
    if data.get("diarization_error"):
        note = "Pa folës: " + data["diarization_error"]
    db.update_meeting(meeting_id, status="done", progress=1, note=note)
    try:
        notify.meeting_processed(meeting_id)  # email the project's PM
    except Exception:
        traceback.print_exc()
