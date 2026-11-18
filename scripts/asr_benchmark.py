"""
How many mistakes does each Whisper model make on Albanian? (WER / CER on held-out test sets)

Test sets (downloaded from Mozilla Data Collective into data/asr_test/, never committed):
  gheg  Common Voice Spontaneous Speech 5.0, Gheg Albanian (aln), split "test": 3 speakers, ~1.9 h
  sq    Common Voice Scripted Speech 27.0, Albanian (sq): read sentences, the same held-out
        speakers as the training notebook (scripts/train_whisper_sq.ipynb)

Usage (from the project folder, with the server stopped so the CPU is free):
  .venv\\Scripts\\python.exe scripts\\asr_benchmark.py --model large-v3-turbo --set gheg --limit 60
  .venv\\Scripts\\python.exe scripts\\asr_benchmark.py --summary

--limit takes the same number of clips from every speaker (the same clips every run), so the
models are compared on identical audio. Results go to data/asr_test/results/: one CSV per
model and set with every reference and hypothesis, so a stopped run continues where it stopped.

Scores:
  WER       word error rate after lower-casing and removing punctuation
  WER_flat  the same, but ë=e and ç=c (many people type Albanian without them; shows how much
            of the error is only those letters)
  CER       character error rate
"""
import argparse
import csv
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import jiwer
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "asr_test"
RESULTS = DATA / "results"
GHEG = DATA / "gheg" / "sps-corpus-5.0-2026-09-11-aln"
SQ = DATA / "sq" / "cv-corpus-27.0-2026-09-11" / "sq"


def gheg_to_standard_letters(text):
    """The Gheg set's transcriber wrote w for ë and C for ç (see its README); brackets mark
    uncertain words. Turn them into normal Albanian letters before comparing."""
    text = text.replace("w", "ë").replace("W", "Ë").replace("C", "ç")
    text = re.sub(r"\[\?+\]", " ", text)
    return text.replace("[", "").replace("]", "")


def norm(text, flat=False):
    text = unicodedata.normalize("NFC", str(text)).lower()
    text = re.sub(r"[-–—/]", " ", text)
    text = re.sub(r"[^\w\s]", "", text)  # punctuation and apostrophes out
    if flat:
        text = text.replace("ë", "e").replace("ç", "c")
    return re.sub(r"\s+", " ", text).strip()


def load_set(name, limit):
    if name == "gheg":
        df = pd.read_csv(GHEG / "ss-corpus-aln.tsv", sep="\t")
        df = df[(df.split == "test") & df.transcription.notna()]
        df = pd.DataFrame({"clip": df.audio_file, "speaker": df.client_id,
                           "path": [str(GHEG / "audios" / f) for f in df.audio_file],
                           "ref": df.transcription.map(gheg_to_standard_letters)})
    elif name == "sq":
        # Common Voice's own split puts 138 of 147 Albanian speakers in test (2 in train), so the
        # training notebook (train_whisper_sq.ipynb) splits by its own rule; the same test here:
        # among speakers with <= 100 clips, sorted by id, every 4th.
        df = pd.read_csv(SQ / "validated.tsv", sep="\t", quoting=3)
        counts = df.client_id.value_counts()
        df = df[df.client_id.isin(sorted(counts[counts <= 100].index)[::4])]
        df = pd.DataFrame({"clip": df.path, "speaker": df.client_id,
                           "path": [str(SQ / "clips" / f) for f in df.path], "ref": df.sentence})
    else:
        raise SystemExit(f"Unknown set {name!r}: use gheg or sq")
    missing = [p for p in df.path if not os.path.exists(p)]
    if missing:
        raise SystemExit(f"{len(missing)} audio files are missing, e.g. {missing[0]}")
    if limit:  # the same clips every run: sorted, then evenly spread over the speakers
        df = df.sort_values(["speaker", "clip"])
        groups = [g for _, g in df.groupby("speaker")]
        if limit < len(groups):  # more speakers than clips wanted: one clip each from evenly spaced speakers
            groups = groups[:: len(groups) // limit][:limit]
        per = max(1, limit // len(groups))
        df = pd.concat(g.iloc[:: max(1, len(g) // per)].head(per) for g in groups)
    return df.reset_index(drop=True)


def safe(name):
    return re.sub(r"[^\w.-]+", "_", name)


def run(model_name, set_name, limit, beam):
    from faster_whisper import WhisperModel

    clips = load_set(set_name, limit)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{set_name}__{safe(model_name)}.csv"
    done = set(pd.read_csv(out)["clip"]) if out.exists() else set()
    todo = clips[~clips["clip"].isin(done)]
    print(f"{model_name} on {set_name}: {len(clips)} clips from {clips.speaker.nunique()} speakers, "
          f"{len(done)} already done, {len(todo)} to go")
    if len(todo):
        threads = int(os.environ.get("WHISPER_THREADS") or min(4, os.cpu_count() or 4))
        model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads)
        new = not out.exists()
        with open(out, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["clip", "speaker", "ref", "hyp", "seconds"])
            t0 = time.time()
            for i, r in enumerate(todo.itertuples(), 1):
                t = time.time()
                segments, _ = model.transcribe(r.path, language="sq", beam_size=beam, vad_filter=True,
                                               condition_on_previous_text=False)
                hyp = " ".join(s.text.strip() for s in segments)
                w.writerow([r.clip, r.speaker, r.ref, hyp, round(time.time() - t, 1)])
                f.flush()
                left = (time.time() - t0) / i * (len(todo) - i)
                print(f"  {i}/{len(todo)}  ~{left / 60:.0f} min left", end="\r")
        print()
    summary(only=[out])


def scores(df):
    refs, hyps = [norm(x) for x in df.ref], [norm(x) for x in df.hyp.fillna("")]
    keep = [i for i, r in enumerate(refs) if r]
    refs, hyps = [refs[i] for i in keep], [hyps[i] for i in keep]
    return {"clips": len(refs), "speakers": df.speaker.nunique(),
            "WER": jiwer.wer(refs, hyps), "WER_flat": jiwer.wer([norm(x, True) for x in refs], [norm(x, True) for x in hyps]),
            "CER": jiwer.cer(refs, hyps), "sec_per_clip": df.seconds.mean()}


def summary(only=None):
    files = only or sorted(RESULTS.glob("*.csv"))
    rows = []
    for p in files:
        set_name, model = p.stem.split("__", 1)
        df = pd.read_csv(p)
        rows.append({"set": set_name, "model": model, **scores(df)})
        if only:  # one run: also per speaker (each test speaker is a different voice)
            for spk, g in df.groupby("speaker"):
                s = scores(g)
                print(f"    speaker {spk[:8]}: {s['clips']} clips, WER {s['WER']:.1%}, CER {s['CER']:.1%}")
    if not rows:
        print("No results yet.")
        return
    t = pd.DataFrame(rows).sort_values(["set", "WER"])
    for c in ("WER", "WER_flat", "CER"):
        t[c] = t[c].map(lambda v: f"{v:.1%}")
    t["sec_per_clip"] = t["sec_per_clip"].map(lambda v: f"{v:.1f}")
    print(t.to_string(index=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--model", default="large-v3-turbo", help="faster-whisper model name or a local CTranslate2 folder")
    a.add_argument("--set", default="gheg", choices=["gheg", "sq"])
    a.add_argument("--limit", type=int, default=60, help="clips to test (0 = the whole test set)")
    a.add_argument("--beam", type=int, default=5)
    a.add_argument("--summary", action="store_true", help="only print the table of all results so far")
    args = a.parse_args()
    if args.summary:
        summary()
    else:
        run(args.model, args.set, args.limit, args.beam)
