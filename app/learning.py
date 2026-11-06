"""
Continuous learning from the PM's transcript fixes.

When a transcript line is edited (double-click on the meeting page, or on a quote), the words that changed
are saved as "learned corrections" (wrong -> right). They are then used on every new meeting:
  1. Whisper hears them better: the right words go into its hotwords
  2. after transcription the same mistakes are replaced automatically
  3. the AI correction step gets them as examples of this team's usual mistakes
A correction can be switched off or deleted on the "Vocabulary" page.
"""
import difflib
import re

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wrong TEXT NOT NULL,           -- lowercase, as the recognizer wrote it
    right TEXT NOT NULL,           -- as the PM wrote it
    times INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (wrong, right)
);
"""

MAX_WORDS = 4      # a fix longer than this is a rewrite, not a misheard word
EDGE = re.compile(r"^[^\w]+|[^\w]+$")


def init():
    with db.connect() as c:
        c.executescript(SCHEMA)


def _clean(word):
    return EDGE.sub("", word)


def word_fixes(old, new):
    """[(wrong, right)] phrases that changed between two versions of a line.
    Punctuation-only changes, rewrites of long passages and plain deletions are not learned."""
    a = [w for w in (_clean(x) for x in old.split()) if w]
    b = [w for w in (_clean(x) for x in new.split()) if w]
    fixes = []
    sm = difflib.SequenceMatcher(a=[w.lower() for w in a], b=[w.lower() for w in b], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace" and i2 - i1 <= MAX_WORDS and j2 - j1 <= MAX_WORDS:
            fixes.append((" ".join(a[i1:i2]), " ".join(b[j1:j2])))
        elif tag == "equal":
            # Same word with a capital letter in the middle of a sentence: a name ("donjeta" -> "Donjeta")
            for k in range(i2 - i1):
                wa, wb = a[i1 + k], b[j1 + k]
                if i1 + k > 0 and wa != wb and wb[:1].isupper() and wa[:1].islower():
                    fixes.append((wa, wb))
    return [(w, r) for w, r in fixes if w and r and len(w) <= 80 and len(r) <= 80]


def learn(old, new):
    """Save the fixes from one edited line. Returns them as [{"wrong", "right"}]."""
    fixes = word_fixes(old, new)
    with db.connect() as c:
        for wrong, right in fixes:
            c.execute("""INSERT INTO corrections (wrong, right, created_at, updated_at) VALUES (?,?,?,?)
                         ON CONFLICT (wrong, right) DO UPDATE SET times = times + 1, updated_at = excluded.updated_at,
                         active = 1""", (wrong.lower(), right, db.now(), db.now()))
    return [{"wrong": w, "right": r} for w, r in fixes]


def all_corrections():
    with db.connect() as c:
        return db.rows(c.execute("SELECT * FROM corrections ORDER BY updated_at DESC, id DESC"))


def set_active(correction_id, active):
    with db.connect() as c:
        c.execute("UPDATE corrections SET active = ? WHERE id = ?", (1 if active else 0, correction_id))


def delete(correction_id):
    with db.connect() as c:
        c.execute("DELETE FROM corrections WHERE id = ?", (correction_id,))


def rules():
    """Active corrections to apply automatically: {wrong: right}.
    When one wrong word was fixed in different ways, the most frequent fix wins.
    Very short words (1-2 letters) are applied only once they were fixed at least twice."""
    out = {}
    with db.connect() as c:
        for r in c.execute("SELECT wrong, right, times FROM corrections WHERE active = 1 ORDER BY times, updated_at"):
            if len(r["wrong"]) >= 3 or r["times"] >= 2:
                out[r["wrong"]] = r["right"]
    return out


def _pattern(rule_map):
    if not rule_map:
        return None
    words = sorted(rule_map, key=len, reverse=True)  # longer phrases first
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE)


def apply_text(text, rule_map, pattern=None):
    pattern = pattern or _pattern(rule_map)
    if not pattern:
        return text

    def sub(m):
        right = rule_map[m.group(0).lower()]
        if m.group(0)[:1].isupper() and right[:1].islower():
            right = right[:1].upper() + right[1:]  # keep a capital at the start of a sentence
        return right
    return pattern.sub(sub, text)


def apply_to_data(data):
    """Apply the learned corrections to every transcript line. Changed lines keep the
    recognizer's text in "raw". Returns the number of changed lines."""
    rule_map = rules()
    pattern = _pattern(rule_map)
    if not pattern:
        return 0
    changed = 0
    for key in ("utterances", "segments"):
        for line in data.get(key) or []:
            new = apply_text(line["text"], rule_map, pattern)
            if new != line["text"]:
                line.setdefault("raw", line["text"])
                line["text"] = new
                if key == ("utterances" if data.get("utterances") else "segments"):
                    changed += 1
    return changed


def count_matches(lines, fixes):
    """How many lines still contain one of these wrong words."""
    pattern = _pattern({f["wrong"].lower(): f["right"] for f in fixes})
    return sum(1 for l in lines if pattern and pattern.search(l["text"])) if pattern else 0


def hotword_terms(limit=40):
    """The right words, most-fixed first: they go to Whisper's hotwords."""
    with db.connect() as c:
        return [r[0] for r in c.execute(
            "SELECT right FROM corrections WHERE active = 1 GROUP BY right ORDER BY SUM(times) DESC, MAX(updated_at) DESC "
            "LIMIT ?", (limit,))]


def examples(limit=60):
    """[(wrong, right)] for the AI correction prompt."""
    with db.connect() as c:
        return [(r[0], r[1]) for r in c.execute(
            "SELECT wrong, right FROM corrections WHERE active = 1 ORDER BY times DESC, updated_at DESC LIMIT ?", (limit,))]
