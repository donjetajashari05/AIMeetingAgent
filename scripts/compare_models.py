"""
Compare two Whisper models on the same recording and list only the places where they disagree,
so a person can listen to those few seconds and say which model was right.

Example:
    python scripts/compare_models.py data/uploads/abc.webm
    python scripts/compare_models.py data/uploads/abc.webm --meeting 11   (A's words from the app, no second run)

Writes output/compare_<name>.txt. Nothing leaves this computer.
"""
import argparse
import difflib
import re
import sys
import time
from pathlib import Path

from transcribe import load_model
from vocab import hotwords, load_glossary, whisper_prompt


def words_with_times(model_name, audio, language, prompt, hot):
    model = load_model(model_name)
    start = time.time()
    segments, _ = model.transcribe(str(audio), language=language, vad_filter=True, beam_size=5,
                                   vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 400},
                                   initial_prompt=prompt, hotwords=hot, word_timestamps=True)
    out = []
    for seg in segments:
        for w in seg.words or []:
            out.append((w.word.strip(), w.start))
    return out, time.time() - start


def meeting_words(meeting_id):
    """Words of a transcript the app already made (time = start of their segment)."""
    import json
    import sqlite3
    db = Path(__file__).resolve().parent.parent / "data" / "app.db"
    row = sqlite3.connect(db).execute("SELECT transcript FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    data = json.loads(row[0])
    return [(w, seg["start"]) for seg in data["segments"] for w in seg["text"].split()]


def norm(w):
    return re.sub(r"[^\w]", "", w.lower())


def clock(sec):
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Where do two models disagree?")
    p.add_argument("audio")
    p.add_argument("--a", default="large-v3", help="First model (default: large-v3)")
    p.add_argument("--b", default="large-v3-turbo", help="Second model (a name or a converted model folder)")
    p.add_argument("--language", default="sq")
    p.add_argument("--meeting", type=int, help="Take model A's words from this meeting in the app (no second run)")
    args = p.parse_args()

    glossary = load_glossary()
    prompt, hot = whisper_prompt(glossary), hotwords(glossary)
    if args.meeting:
        a, ta = meeting_words(args.meeting), 0.0
        args.a = f"{args.a} (from meeting #{args.meeting})"
    else:
        a, ta = words_with_times(args.a, args.audio, args.language, prompt, hot)
    print(f"A = {args.a}: {len(a)} words in {ta:.0f} s")
    b, tb = words_with_times(args.b, args.audio, args.language, prompt, hot)
    print(f"B = {args.b}: {len(b)} words in {tb:.0f} s")

    sm = difflib.SequenceMatcher(a=[norm(w) for w, _ in a], b=[norm(w) for w, _ in b], autojunk=False)
    lines, n = [], 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        n += 1
        at = a[i1][1] if i1 < len(a) else (b[j1][1] if j1 < len(b) else 0)
        before = " ".join(w for w, _ in a[max(0, i1 - 4):i1])
        after = " ".join(w for w, _ in a[i2:i2 + 4])
        lines.append(f"{n:>3}. [{clock(at)}]  …{before}\n"
                     f"     A: {' '.join(w for w, _ in a[i1:i2]) or '(nothing)'}\n"
                     f"     B: {' '.join(w for w, _ in b[j1:j2]) or '(nothing)'}\n"
                     f"     …{after}\n"
                     f"     Right:  A / B / both wrong\n")
    same = sum(i2 - i1 for op, i1, i2, _, _ in sm.get_opcodes() if op == "equal")
    head = (f"A = {args.a} ({ta:.0f} s)\nB = {args.b} ({tb:.0f} s)\n"
            f"The models agree on {same} of {len(a)} words. Listen to these {n} places and mark which one is right.\n\n")
    out = Path("output") / f"compare_{Path(args.audio).stem}.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(head + "\n".join(lines), encoding="utf-8")
    print(head + "\n".join(lines))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
