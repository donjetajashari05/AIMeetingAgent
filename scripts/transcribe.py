"""
Transcribe a meeting recording with faster-whisper, optionally with speaker
diarization (pyannote.audio).

Examples:
    python scripts/transcribe.py data/audio/meeting1.mp3
    python scripts/transcribe.py data/audio/meeting1.mp3 --language en
    python scripts/transcribe.py data/audio/meeting1.mp3 --model medium
    python scripts/transcribe.py data/audio/meeting1.mp3 --diarize
    python scripts/transcribe.py data/audio/meeting1.mp3 --diarize --speakers 3
"""
import argparse
import json
import sys
import time
from pathlib import Path

import ctranslate2
from dotenv import load_dotenv
from faster_whisper import WhisperModel

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")  # so ë/ç print correctly in the Windows console

SPEAKER_LABEL = "Folësi"


def get_device():
    """Use the GPU if available, otherwise fall back to the CPU."""
    if ctranslate2.get_cuda_device_count() > 0:
        return "cuda", "float16"
    return "cpu", "int8"


def main():
    parser = argparse.ArgumentParser(description="Meeting transcription")
    parser.add_argument("audio", help="Path to the audio file (mp3, wav, m4a...)")
    parser.add_argument("--model", default="large-v3", help="tiny, base, small, medium, large-v3")
    parser.add_argument("--language", default=None, help="Language code (e.g. en), or leave empty for auto-detection")
    parser.add_argument("--out", default="output", help="Directory where results are saved")
    parser.add_argument("--diarize", action="store_true", help="Detect who is speaking (pyannote.audio)")
    parser.add_argument("--speakers", type=int, default=None, help="Exact number of speakers, if known")
    parser.add_argument("--min-speakers", type=int, default=None)
    parser.add_argument("--max-speakers", type=int, default=None)
    args = parser.parse_args()

    if not Path(args.audio).is_file():
        raise SystemExit(f"Audio file not found: {args.audio}\n"
                         f"Put a recording in data/audio/ and pass its path, "
                         f"e.g. python scripts/transcribe.py data/audio/meeting1.mp3")

    device, compute_type = get_device()
    print(f"Device: {device} ({compute_type}) | Model: {args.model}")
    model = WhisperModel(args.model, device=device, compute_type=compute_type)

    start = time.time()
    segments, info = model.transcribe(
        args.audio,
        language=args.language,
        vad_filter=True,   # skips silence, which speeds things up and reduces errors
        beam_size=5,
        word_timestamps=args.diarize,  # word-level times are needed to match speakers
    )
    print(f"Language: {info.language} ({info.language_probability:.0%}) | "
          f"Duration: {info.duration / 60:.1f} min\n")

    results, words = [], []
    for seg in segments:  # transcription actually happens while iterating
        text = seg.text.strip()
        results.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
        print(f"[{seg.start:7.1f}s -> {seg.end:7.1f}s] {text}")
        for w in seg.words or []:
            words.append({"start": w.start, "end": w.end, "word": w.word})
    asr_elapsed = time.time() - start

    utterances = None
    if args.diarize:
        from diarize import assign_speakers, diarize

        print("\nDiarization (who is speaking)...")
        d_start = time.time()
        turns = diarize(args.audio, num_speakers=args.speakers,
                        min_speakers=args.min_speakers, max_speakers=args.max_speakers)
        utterances = assign_speakers(words, turns, label=SPEAKER_LABEL)
        print(f"Found {len({u['speaker'] for u in utterances})} speakers "
              f"in {time.time() - d_start:.0f} seconds\n")
        for u in utterances:
            print(f"[{u['start']:7.1f}s] {u['speaker']}: {u['text']}")

    elapsed = time.time() - start
    print(f"\nFinished in {elapsed:.0f} seconds "
          f"({info.duration / max(elapsed, 1):.1f}x faster than real time)")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(args.audio).stem

    data = {
        "audio": args.audio,
        "model": args.model,
        "language": info.language,
        "duration_sec": round(info.duration, 1),
        "processing_sec": round(elapsed, 1),
        "asr_sec": round(asr_elapsed, 1),
        "segments": results,
    }
    if utterances is not None:
        data["utterances"] = utterances

    with open(out_dir / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Plain text without speaker labels, used by evaluate_wer.py
    with open(out_dir / f"{name}.txt", "w", encoding="utf-8") as f:
        f.write(" ".join(s["text"] for s in results))

    saved = [f"{name}.json", f"{name}.txt"]
    if utterances is not None:
        with open(out_dir / f"{name}_speakers.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(f"{u['speaker']}: {u['text']}" for u in utterances))
        saved.append(f"{name}_speakers.txt")

    print(f"Results saved to {out_dir}: {', '.join(saved)}")


if __name__ == "__main__":
    main()
