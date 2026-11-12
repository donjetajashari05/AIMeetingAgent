"""
Fix speech-recognition mistakes in an Albanian meeting transcript with Claude,
using the conversation's context and the team vocabulary (data/fjalori.txt).

The speakers' dialect and informal words are kept exactly as spoken
("qysh", "me bo", "kqyre"...): only words Whisper clearly misheard are changed.
The original text of every changed line is kept in "raw".

Examples:
    python scripts/correct.py output/mbledhja1.json
    python scripts/evaluate_wer.py data/reference/mbledhja1.txt output/mbledhja1.txt
    python scripts/evaluate_wer.py data/reference/mbledhja1.txt output/mbledhja1_corrected.txt
"""
import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

import llm
from vocab import glossary_text, load_glossary

load_dotenv()
CHUNK = 60  # lines per request; neighbouring lines give the model context


class Fix(BaseModel):
    i: int = Field(description="Line number as given in the transcript")
    text: str = Field(description="The whole corrected line")


class Fixes(BaseModel):
    fixes: list[Fix] = Field(description="Only the lines that changed; empty if nothing needs fixing")


SYSTEM_PROMPT = """You correct speech-recognition (ASR) errors in transcripts of work meetings at a
software company in Kosovo. People speak Albanian, mostly the Kosovo (Gheg) dialect, informally,
mixed with English work terms (deploy, sprint, task, review...).

Fix ONLY words the recognizer clearly got wrong:
- a word that makes no sense in context but a similar-sounding word does
- broken or merged words, wrong spelling of names, projects and terms in the vocabulary
- English terms broken into nonsense (e.g. "di ploj" -> "deploy", "sprin rivju" -> "sprint review");
  an English word with an Albanian ending ("deploy-it", "taskat", "me e mergu") is correct as said

Do NOT:
- convert dialect or colloquial speech into standard Albanian. "qysh", "qka", "me bo", "s'kom",
  "kqyre", "tash", "najsen" and similar are correct as spoken and must stay.
- rephrase, shorten, summarize, translate, or "improve" grammar or word order
- add words that were not said, or remove filler words
- change a line when you are not confident: leaving it is better than guessing

One exception to "never remove": speech recognizers sometimes repeat a phrase or sentence
word-for-word right after itself, or produce text in silence ("Faleminderit që shikuat").
Remove such machine repetitions/hallucinations; keep repetitions that a person clearly said
("po po", "jo jo, prit").

Use the vocabulary for spelling of names and terms. <learned_corrections> lists mistakes this
recognizer made before and how the team corrected them ("wrong -> right"): when the same mistake
appears again and fits the context, fix it the same way. Return only the lines you changed, each as
the complete corrected line: only the spoken text, without the "[n]" number or the speaker name."""


def _lines_text(lines, offset):
    out = []
    for n, l in enumerate(lines):
        who = f"{l['speaker']}: " if l.get("speaker") else ""
        out.append(f"[{offset + n}] {who}{l['text']}")
    return "\n".join(out)


def _strip_prefix(text, line):
    """Remove a "[12] Folësi 2: " the AI copied from the input format."""
    text = re.sub(r"^\s*\[\d+\]\s*", "", text.strip())
    who = line.get("speaker")
    if who and text.startswith(f"{who}:"):
        text = text[len(who) + 1:]
    return text.strip()


def correct_lines(lines, glossary=(), people=(), client=None, on_progress=None, learned=()):
    """Return {index: corrected_text} for the lines that should change.
    learned: [(wrong, right)] fixes the team made by hand on earlier transcripts."""
    vocab = glossary_text(glossary)
    names = ", ".join(people)
    context = ""
    if vocab:
        context += f"<vocabulary>\n{vocab}\n</vocabulary>\n\n"
    if learned:
        context += "<learned_corrections>\n" + "\n".join(f"{w} -> {r}" for w, r in learned) + "\n</learned_corrections>\n\n"
    if names:
        context += f"<participants>{names}</participants>\n\n"

    changes = {}
    for start in range(0, len(lines), CHUNK):
        chunk = lines[start:start + CHUNK]
        try:
            result = llm.structured(SYSTEM_PROMPT, f"{context}<transcript>\n{_lines_text(chunk, start)}\n</transcript>",
                                    Fixes, max_tokens=16000, effort="medium")
        except llm.LLMError as e:
            if "API_KEY" in str(e):
                raise  # no/invalid key: stop and report
            continue  # refusal, cut-off or bad output: keep this chunk as transcribed
        for fix in result.fixes:
            n = fix.i - start
            new = _strip_prefix(fix.text, chunk[n] if 0 <= n < len(chunk) else {})
            if 0 <= n < len(chunk) and new and new != chunk[n]["text"]:
                changes[fix.i] = new
        if on_progress:
            on_progress(min(start + CHUNK, len(lines)), len(lines))
    return changes


def apply_corrections(data, glossary=(), people=(), client=None, on_progress=None, learned=()):
    """Correct the lines that are shown (utterances if there are speakers, else segments).
    Changed lines keep the original in "raw". Returns the number of changed lines."""
    key = "utterances" if data.get("utterances") else "segments"
    lines = data.get(key) or []
    changes = correct_lines(lines, glossary, people, client, on_progress, learned)
    for i, text in changes.items():
        lines[i].setdefault("raw", lines[i]["text"])
        lines[i]["text"] = text
    data["corrected"] = len(changes)
    return len(changes)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Correct ASR mistakes with Claude")
    parser.add_argument("transcript", help="JSON produced by transcribe.py")
    parser.add_argument("--out", default="output")
    args = parser.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        data = json.load(f)
    glossary = load_glossary()
    if not llm.available():
        raise SystemExit(llm.NO_KEY)
    print(f"Vocabulary: {len(glossary)} words. Correcting with {llm.label()}...")
    n = apply_corrections(data, glossary, on_progress=lambda d, t: print(f"  {d}/{t} lines"))

    key = "utterances" if data.get("utterances") else "segments"
    for l in data[key]:
        if "raw" in l:
            print(f"- {l['raw']}\n+ {l['text']}\n")
    print(f"{n} lines changed.")

    name = Path(args.transcript).stem
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{name}_corrected.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(out / f"{name}_corrected.txt", "w", encoding="utf-8") as f:
        f.write(" ".join(l["text"] for l in data[key]))
    print(f"Saved {out / name}_corrected.json and _corrected.txt")


if __name__ == "__main__":
    main()
