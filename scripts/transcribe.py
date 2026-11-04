"""
Transcribe a meeting recording with faster-whisper.

Examples:
    python scripts/transcribe.py data/audio/meeting1.mp3
    python scripts/transcribe.py data/audio/meeting1.mp3 --language en
    python scripts/transcribe.py data/audio/meeting1.mp3 --model medium
"""
import argparse
import json
import time
from pathlib import Path

import ctranslate2
from faster_whisper import WhisperModel


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
    )
    print(f"Language: {info.language} ({info.language_probability:.0%}) | "
          f"Duration: {info.duration / 60:.1f} min\n")

    results = []
    for seg in segments:  # transcription actually happens while iterating
        text = seg.text.strip()
        results.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
        print(f"[{seg.start:7.1f}s -> {seg.end:7.1f}s] {text}")

    elapsed = time.time() - start
    print(f"\nFinished in {elapsed:.0f} seconds "
          f"({info.duration / max(elapsed, 1):.1f}x faster than real time)")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(args.audio).stem

    with open(out_dir / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump({
            "audio": args.audio,
            "model": args.model,
            "language": info.language,
            "duration_sec": round(info.duration, 1),
            "processing_sec": round(elapsed, 1),
            "segments": results,
        }, f, ensure_ascii=False, indent=2)

    with open(out_dir / f"{name}.txt", "w", encoding="utf-8") as f:
        f.write(" ".join(s["text"] for s in results))

    print(f"Results saved to: {out_dir / name}.json and .txt")


if __name__ == "__main__":
    main()