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
from functools import lru_cache
from pathlib import Path

import ctranslate2
from dotenv import load_dotenv
from faster_whisper import WhisperModel

load_dotenv()

SPEAKER_LABEL = "Folësi"


def get_device():
    """Use the GPU if available, otherwise fall back to the CPU."""
    if ctranslate2.get_cuda_device_count() > 0:
        return "cuda", "float16"
    return "cpu", "int8"


@lru_cache(maxsize=2)
def load_model(name):
    """Load a Whisper model once and reuse it (loading takes several seconds)."""
    device, compute_type = get_device()
    return WhisperModel(name, device=device, compute_type=compute_type)


def transcribe_audio(audio, model_name="large-v3", language=None, diarize=False,
                     num_speakers=None, min_speakers=None, max_speakers=None,
                     on_segment=None, on_info=None, on_diarize=None, diarize_required=True):
    """
    Transcribe an audio file and return a dict with segments (and utterances
    with speaker labels when diarize=True).

    on_info(info) is called once the language/duration are known;
    on_segment(segment_dict, info) is called for every transcribed segment;
    on_diarize() is called when diarization starts.
    With diarize_required=False a diarization failure is stored in
    data["diarization_error"] instead of raising.
    """
    model = load_model(model_name)
    start = time.time()
    segments, info = model.transcribe(
        str(audio),
        language=language,
        vad_filter=True,   # skips silence, which speeds things up and reduces errors
        beam_size=5,
        word_timestamps=diarize,  # word-level times are needed to match speakers
    )
    if on_info:
        on_info(info)

    results, words = [], []
    for seg in segments:  # transcription actually happens while iterating
        item = {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}
        results.append(item)
        for w in seg.words or []:
            words.append({"start": w.start, "end": w.end, "word": w.word})
        if on_segment:
            on_segment(item, info)
    asr_elapsed = time.time() - start

    utterances, diarization_error = None, None
    if diarize:
        if on_diarize:
            on_diarize()
        try:
            from diarize import assign_speakers, diarize as run_diarization

            turns = run_diarization(str(audio), num_speakers=num_speakers,
                                    min_speakers=min_speakers, max_speakers=max_speakers)
            utterances = assign_speakers(words, turns, label=SPEAKER_LABEL)
        except (Exception, SystemExit) as e:
            if diarize_required:
                raise
            # Keep the transcript; just report why speakers are missing
            diarization_error = str(e).splitlines()[0][:300]

    data = {
        "audio": str(audio),
        "model": model_name,
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration_sec": round(info.duration, 1),
        "processing_sec": round(time.time() - start, 1),
        "asr_sec": round(asr_elapsed, 1),
        "segments": results,
    }
    if utterances is not None:
        data["utterances"] = utterances
    if diarization_error:
        data["diarization_error"] = diarization_error
    return data


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # so ë/ç print correctly in the Windows console
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

    def show_info(info):
        print(f"Language: {info.language} ({info.language_probability:.0%}) | "
              f"Duration: {info.duration / 60:.1f} min\n")

    def show_segment(s, _info):
        print(f"[{s['start']:7.1f}s -> {s['end']:7.1f}s] {s['text']}")

    if args.diarize:
        print("(Diarization runs after transcription.)")
    data = transcribe_audio(args.audio, args.model, args.language, args.diarize,
                            args.speakers, args.min_speakers, args.max_speakers,
                            on_segment=show_segment, on_info=show_info)

    if "utterances" in data:
        print(f"\nFound {len({u['speaker'] for u in data['utterances']})} speakers\n")
        for u in data["utterances"]:
            print(f"[{u['start']:7.1f}s] {u['speaker']}: {u['text']}")

    elapsed = data["processing_sec"]
    print(f"\nFinished in {elapsed:.0f} seconds "
          f"({data['duration_sec'] / max(elapsed, 1):.1f}x faster than real time)")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(args.audio).stem

    with open(out_dir / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Plain text without speaker labels, used by evaluate_wer.py
    with open(out_dir / f"{name}.txt", "w", encoding="utf-8") as f:
        f.write(" ".join(s["text"] for s in data["segments"]))

    saved = [f"{name}.json", f"{name}.txt"]
    if "utterances" in data:
        with open(out_dir / f"{name}_speakers.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(f"{u['speaker']}: {u['text']}" for u in data["utterances"]))
        saved.append(f"{name}_speakers.txt")

    print(f"Results saved to {out_dir}: {', '.join(saved)}")


if __name__ == "__main__":
    main()
