"""
Recognizing people by their voice.

People don't say their name in meetings, so speakers are recognized by voice:
  1. Diarization (pyannote) splits a recording by voice and gives each speaker ("Folësi 2")
     a voice fingerprint: a list of numbers (an embedding), not a recording.
  2. The PM tells the system once who a speaker is ("Folësi 2 = <name>"). If that person agreed to
     voice recognition, their fingerprint from that meeting is added to their voice profile.
  3. In the next meetings every speaker's fingerprint is compared with the profiles. A close
     enough match puts the person's name on the speaker automatically.
Step 2 can also happen before any meeting: an admin records (or uploads) 20-60 seconds of the
person speaking alone on the Team page (enroll), so names appear from the very first meeting.
The sample recording itself is deleted right after the fingerprint is taken.

A voice fingerprint is biometric personal data, so:
  - nothing is stored for a person without consent (employees.voice_consent_at, set by an
    admin after the person agreed); withdrawing consent deletes the profile;
  - fingerprints stay in the local database, are never sent to the browser, to the AI or to
    files, and are removed from the transcript before it is saved;
  - every naming, consent change and profile deletion goes to the audit log.

VOICE_MATCH_THRESHOLD (.env, default 0.6): how similar (cosine, 0..1) a voice must be to a
profile to be named automatically. Raise it if people get mixed up, lower it if known people
stay "Folësi N".
"""
import os
import sys

import numpy as np

from . import db

if str(db.ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(db.ROOT / "scripts"))

MIN_SAMPLE_SEC = 10  # less speech than this gives an unreliable fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_voices (
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    label TEXT NOT NULL,              -- the original "Folësi N"
    embedding BLOB NOT NULL,          -- float32 voice fingerprint of this speaker in this meeting
    PRIMARY KEY (meeting_id, label)
);
CREATE TABLE IF NOT EXISTS meeting_speakers (
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    similarity REAL,                  -- for source = 'voice': how close the voice was (0..1)
    source TEXT NOT NULL,             -- voice (recognized) | manual (the PM chose)
    PRIMARY KEY (meeting_id, label)
);
CREATE TABLE IF NOT EXISTS voice_profiles (
    employee_id INTEGER PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,          -- mean of the person's confirmed fingerprints (normalized)
    samples INTEGER NOT NULL,         -- how many meetings it was learned from
    updated_at TEXT NOT NULL
);
"""

MARGIN = 0.08  # the best match must beat the second best by this much


class VoiceError(ValueError):
    pass


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(employees)")}
        if "voice_consent_at" not in cols:
            c.execute("ALTER TABLE employees ADD COLUMN voice_consent_at TEXT")  # NULL = no consent
        if "voice_consent_by" not in cols:
            c.execute("ALTER TABLE employees ADD COLUMN voice_consent_by INTEGER")


def threshold():
    try:
        return min(0.95, max(0.3, float(os.environ.get("VOICE_MATCH_THRESHOLD") or 0.6)))
    except ValueError:
        return 0.6


def _vec(blob):
    return np.frombuffer(blob, dtype=np.float32)


def _norm(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


# ---------- After transcription ----------

def take_embeddings(meeting_id, data):
    """Move the fingerprints out of the transcript into their own table. Returns {label: vector}."""
    raw = data.pop("speaker_embeddings", None) or {}
    out = {label: _norm(v) for label, v in raw.items() if v}
    with db.connect() as c:
        c.execute("DELETE FROM meeting_voices WHERE meeting_id = ?", (meeting_id,))
        c.executemany("INSERT INTO meeting_voices (meeting_id, label, embedding) VALUES (?,?,?)",
                      [(meeting_id, label, v.astype(np.float32).tobytes()) for label, v in out.items()])
    return out


def _profiles(c):
    """Profiles of active people who agreed to voice recognition."""
    return [(r["employee_id"], r["full_name"], _vec(r["embedding"])) for r in c.execute("""
        SELECT p.employee_id, p.embedding, e.full_name FROM voice_profiles p JOIN employees e ON e.id = p.employee_id
        WHERE e.active = 1 AND e.voice_consent_at IS NOT NULL""")]


def match(embeddings, profiles, limit=None):
    """{label: (employee_id, name, similarity)}: one person per speaker and one speaker per person,
    best pairs first; only above the threshold and clearly better than the second best."""
    limit = threshold() if limit is None else limit
    pairs = []
    for label, v in embeddings.items():
        sims = sorted(((float(np.dot(v, p)), emp, name) for emp, name, p in profiles), reverse=True)
        if not sims or sims[0][0] < limit:
            continue
        if len(sims) > 1 and sims[0][0] - sims[1][0] < MARGIN:
            continue  # two people sound alike here: don't guess
        pairs.append((sims[0][0], label, sims[0][1], sims[0][2]))
    out, used = {}, set()
    for sim, label, emp, name in sorted(pairs, reverse=True):
        if emp in used or label in out:
            continue
        used.add(emp)
        out[label] = (emp, name, round(sim, 3))
    return out


def identify(meeting_id, data):
    """Put the names of recognized people on the speakers of a new transcript. Returns how many."""
    embeddings = take_embeddings(meeting_id, data)
    if not embeddings or not data.get("utterances"):
        return 0
    with db.connect() as c:
        found = match(embeddings, _profiles(c))
        c.execute("DELETE FROM meeting_speakers WHERE meeting_id = ? AND source = 'voice'", (meeting_id,))
        manual = {r[0] for r in c.execute("SELECT label FROM meeting_speakers WHERE meeting_id = ?", (meeting_id,))}
        for label, (emp, name, sim) in found.items():
            if label in manual:
                continue  # the PM's choice wins
            c.execute("INSERT INTO meeting_speakers (meeting_id, label, employee_id, name, similarity, source) "
                      "VALUES (?,?,?,?,?, 'voice')", (meeting_id, label, emp, name, sim))
        names = {r["label"]: r["name"] for r in c.execute(
            "SELECT label, name FROM meeting_speakers WHERE meeting_id = ?", (meeting_id,))}
    for u in data["utterances"]:
        label = u.setdefault("label", u.get("speaker"))
        if label in names:
            u["speaker"] = names[label]
    return len(found)


# ---------- The PM names a speaker ----------

def speakers(meeting_id):
    """The speakers of a meeting with who they are (never the fingerprints)."""
    m = db.get_meeting(meeting_id)
    import json
    data = json.loads(m["transcript"]) if m and m["transcript"] else {}
    labels = []
    for u in data.get("utterances") or []:
        label = u.get("label") or u.get("speaker")
        if label and label not in labels:
            labels.append(label)
    with db.connect() as c:
        known = {r["label"]: dict(r) for r in c.execute("SELECT * FROM meeting_speakers WHERE meeting_id = ?", (meeting_id,))}
        voiced = {r[0] for r in c.execute("SELECT label FROM meeting_voices WHERE meeting_id = ?", (meeting_id,))}
    out = []
    for label in labels:
        k = known.get(label) or {}
        out.append({"label": label, "name": k.get("name") or label, "employee_id": k.get("employee_id"),
                    "source": k.get("source"), "similarity": k.get("similarity"), "has_voice": label in voiced})
    return out


def name_speaker(meeting_id, label, employee_id):
    """The PM says who a speaker is (employee_id None = unknown again). Renames the speaker in the
    transcript, the search index, the AI's draft tasks and the standup report, and, if the person
    agreed, learns their voice from this meeting. Returns {"name", "learned"}."""
    import json
    m = db.get_meeting(meeting_id)
    data = json.loads(m["transcript"]) if m and m["transcript"] else None
    if not data or not data.get("utterances"):
        raise VoiceError("This meeting has no speakers.")
    if not any((u.get("label") or u.get("speaker")) == label for u in data["utterances"]):
        raise VoiceError("Speaker not found in this meeting.")
    with db.connect() as c:
        emp = None
        if employee_id:
            emp = c.execute("SELECT id, full_name, voice_consent_at FROM employees WHERE id = ?", (employee_id,)).fetchone()
            if not emp:
                raise VoiceError("Employee not found.")
        # One person is one speaker in a meeting
        if emp:
            c.execute("DELETE FROM meeting_speakers WHERE meeting_id = ? AND employee_id = ? AND label != ?",
                      (meeting_id, emp["id"], label))
        c.execute("DELETE FROM meeting_speakers WHERE meeting_id = ? AND label = ?", (meeting_id, label))
        if emp:
            c.execute("INSERT INTO meeting_speakers (meeting_id, label, employee_id, name, similarity, source) "
                      "VALUES (?,?,?,?,NULL,'manual')", (meeting_id, label, emp["id"], emp["full_name"]))
        names = {r["label"]: r["name"] for r in c.execute(
            "SELECT label, name FROM meeting_speakers WHERE meeting_id = ?", (meeting_id,))}
    renamed = {}
    for u in data["utterances"]:
        lab = u.setdefault("label", u.get("speaker"))
        new = names.get(lab, lab)
        if u.get("speaker") != new:
            renamed[u.get("speaker")] = new
            u["speaker"] = new
    db.save_transcript(meeting_id, data)  # also re-indexes search with the names
    new_name = emp["full_name"] if emp else label
    with db.connect() as c:
        for old in set(renamed) | {label}:
            # Only the AI's drafts: tasks the PM confirmed or sent keep their assignee
            c.execute("UPDATE tasks SET assignee = ?, assignee_id = ? WHERE meeting_id = ? AND assignee = ? "
                      "AND approved_at IS NULL AND jira_key IS NULL AND is_ticket = 0",
                      (new_name, emp["id"] if emp else None, meeting_id, old))
            c.execute("UPDATE standup_updates SET person = ?, employee_id = ? WHERE meeting_id = ? AND person = ?",
                      (new_name, emp["id"] if emp else None, meeting_id, old))
    learned = bool(emp and emp["voice_consent_at"] and learn(emp["id"], meeting_id, label))
    return {"name": new_name, "learned": learned, "consent": bool(emp and emp["voice_consent_at"])}


def learn(employee_id, meeting_id, label):
    """Add this meeting's fingerprint of `label` to the person's profile (running mean)."""
    with db.connect() as c:
        r = c.execute("SELECT embedding FROM meeting_voices WHERE meeting_id = ? AND label = ?", (meeting_id, label)).fetchone()
        if not r:
            return False
        _add_to_profile(c, employee_id, _vec(r["embedding"]))
    return True


def _add_to_profile(c, employee_id, vec):
    new = _norm(vec)
    p = c.execute("SELECT embedding, samples FROM voice_profiles WHERE employee_id = ?", (employee_id,)).fetchone()
    if p:
        n = p["samples"]
        mean = _norm((_vec(p["embedding"]) * n + new) / (n + 1))
        c.execute("UPDATE voice_profiles SET embedding = ?, samples = ?, updated_at = ? WHERE employee_id = ?",
                  (mean.astype(np.float32).tobytes(), n + 1, db.now(), employee_id))
    else:
        c.execute("INSERT INTO voice_profiles (employee_id, embedding, samples, updated_at) VALUES (?,?,1,?)",
                  (employee_id, new.astype(np.float32).tobytes(), db.now()))


# ---------- Voice sample on the Team page (names from the first meeting) ----------

def sample_fingerprint(audio_path):
    """Fingerprint of the one person speaking in a short sample. Refuses samples that are too
    short or have a second voice in them (the profile would mix two people)."""
    from diarize import diarize  # loads pyannote (needs HF_TOKEN)

    try:
        turns, vectors = diarize(str(audio_path), with_embeddings=True)
    except SystemExit as e:  # diarize() reports setup problems this way
        raise VoiceError(str(e).splitlines()[0])
    talk = {}
    for t in turns:
        talk[t["speaker"]] = talk.get(t["speaker"], 0.0) + t["end"] - t["start"]
    if not talk:
        raise VoiceError("No speech was found in the sample.")
    ranked = sorted(talk.items(), key=lambda kv: kv[1], reverse=True)
    main, seconds = ranked[0]
    if seconds < MIN_SAMPLE_SEC:
        raise VoiceError(f"Only {seconds:.0f} seconds of speech. Record at least {MIN_SAMPLE_SEC} seconds (30 is better).")
    if len(ranked) > 1 and ranked[1][1] > max(3.0, 0.2 * seconds):
        raise VoiceError("More than one voice is in the sample. Record the person alone, in a quiet room.")
    if main not in vectors:
        raise VoiceError("The voice could not be measured. Try a clearer recording.")
    return np.asarray(vectors[main], dtype=np.float32), seconds


def enroll(employee_id, vec):
    """Add a sample's fingerprint to the person's profile. Only with their consent."""
    with db.connect() as c:
        emp = c.execute("SELECT voice_consent_at FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if not emp:
            raise VoiceError("Employee not found.")
        if not emp["voice_consent_at"]:
            raise VoiceError("Tick “Agreed to be recognized by voice” and save first.")
        _add_to_profile(c, employee_id, vec)
        return c.execute("SELECT samples FROM voice_profiles WHERE employee_id = ?", (employee_id,)).fetchone()["samples"]


# ---------- Consent and profiles ----------

def set_consent(employee_id, consent, by_user_id):
    """Record that the person agreed (or withdrew). Withdrawing deletes their voice profile."""
    with db.connect() as c:
        row = c.execute("SELECT voice_consent_at FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if not row:
            raise VoiceError("Employee not found.")
        if consent and not row["voice_consent_at"]:
            c.execute("UPDATE employees SET voice_consent_at = ?, voice_consent_by = ? WHERE id = ?",
                      (db.now(), by_user_id, employee_id))
            return "given"
        if not consent and row["voice_consent_at"]:
            c.execute("UPDATE employees SET voice_consent_at = NULL, voice_consent_by = NULL WHERE id = ?", (employee_id,))
            c.execute("DELETE FROM voice_profiles WHERE employee_id = ?", (employee_id,))
            return "withdrawn"
    return None


def delete_profile(employee_id):
    with db.connect() as c:
        return c.execute("DELETE FROM voice_profiles WHERE employee_id = ?", (employee_id,)).rowcount > 0


def summary():
    """{employee_id: {"consent_at", "samples"}} for the Team page (no fingerprints)."""
    with db.connect() as c:
        rows = c.execute("""SELECT e.id, e.voice_consent_at, p.samples FROM employees e
                            LEFT JOIN voice_profiles p ON p.employee_id = e.id""").fetchall()
    return {r["id"]: {"voice_consent_at": r["voice_consent_at"], "voice_samples": r["samples"] or 0} for r in rows}
