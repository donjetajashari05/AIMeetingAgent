"""
The team's vocabulary (data/fjalori.txt): words that are said in meetings —
dialect, slang, English work terms, names — and optionally what they mean.

File format (lines starting with # are ignored):
    qysh, qka, me bo, kqyre              <- words, separated by commas
    kqyre = shiko                        <- one word with its meaning per line
    me ja nis = të fillojmë              <- meanings help Claude understand slang

Uses:
  - Whisper: initial_prompt (style + names) and hotwords (kept for the whole recording)
  - Claude: correcting misheard words and understanding what slang means
"""
from pathlib import Path

GLOSSARY_FILE = Path(__file__).resolve().parent.parent / "data" / "fjalori.txt"


def load_glossary(path=GLOSSARY_FILE):
    """Return [{"term": ..., "meaning": ... or None}, ...] without duplicates."""
    path = Path(path)
    if not path.is_file():
        return []
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" in line:
            term, meaning = (x.strip() for x in line.split("=", 1))
            if term:
                entries[term.lower()] = {"term": term, "meaning": meaning or None}
        else:
            for term in (w.strip() for w in line.split(",")):
                if term and term.lower() not in entries:
                    entries[term.lower()] = {"term": term, "meaning": None}
    return list(entries.values())


def whisper_prompt(glossary=(), people=(), projects=(), limit=400, with_words=False):
    """Initial prompt: the style (Kosovo Albanian + English terms) and the names.
    The vocabulary words go to hotwords() instead: putting the same list in both
    fills Whisper's ~224-token context and can make it repeat sentences.
    with_words=True adds the words here too (for when hotwords aren't used)."""
    prompt = "Mbledhje pune në shqip (dialekt kosovar), me fjalë në anglisht."
    if people:
        prompt += " Pjesëmarrës: " + ", ".join(people) + "."
    if projects:
        prompt += " Projekte: " + ", ".join(projects) + "."
    if with_words and glossary:
        prompt += " " + ", ".join(g["term"] for g in glossary) + "."
    return prompt[:limit]


def hotwords(glossary, people=(), projects=(), limit=250):
    """Vocabulary that faster-whisper adds to every 30-second window (the initial
    prompt only helps at the start). Names are in the prompt; here mostly the words."""
    words = list(dict.fromkeys([*(g["term"] for g in glossary), *people, *projects]))
    out = ""
    for w in words:
        if len(out) + len(w) + 2 > limit:
            break
        out += (", " if out else "") + w
    return out or None


def glossary_text(glossary):
    """The glossary as Claude reads it."""
    lines = []
    for g in glossary:
        lines.append(f"- {g['term']}" + (f": {g['meaning']}" if g["meaning"] else ""))
    return "\n".join(lines)
