"""
Speaker diarization with pyannote.audio and alignment with the Whisper transcript.

Requires a free HuggingFace token (HF_TOKEN in the environment or in .env) and
accepting the model terms at:
    https://huggingface.co/pyannote/speaker-diarization-community-1
"""
import os

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
SAMPLE_RATE = 16000


def diarize(audio_path, num_speakers=None, min_speakers=None, max_speakers=None):
    """Return a list of speaker turns: [{"start", "end", "speaker"}, ...]."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit(
            "HF_TOKEN is missing. Create a token at https://huggingface.co/settings/tokens, "
            f"accept the terms at https://huggingface.co/{DIARIZATION_MODEL} "
            "and put HF_TOKEN=hf_... in the .env file."
        )

    # Heavy imports (torch, pyannote) only when diarization is actually used
    try:
        import torch
        from faster_whisper import decode_audio
        from pyannote.audio import Pipeline
    except ImportError as e:
        raise SystemExit(
            f"Could not load pyannote.audio: {e}\n"
            "If the message says 'Application Control policy has blocked this file', "
            "Windows is blocking a DLL on this machine. Run diarization in another "
            "environment (e.g. Google Colab) or run transcription without --diarize."
        )

    pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
    if pipeline is None:
        raise SystemExit(f"Could not load {DIARIZATION_MODEL}. "
                         "Check that you accepted the model terms on HuggingFace.")
    if torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))

    # Decode with faster-whisper (PyAV) and pass the waveform in memory, so we
    # don't depend on torchcodec/FFmpeg DLLs, which often fail on Windows.
    audio = decode_audio(audio_path, sampling_rate=SAMPLE_RATE)
    waveform = torch.from_numpy(audio).unsqueeze(0)

    output = pipeline(
        {"waveform": waveform, "sample_rate": SAMPLE_RATE},
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    )
    # The "exclusive" version has no overlapping turns, which suits transcripts better.
    annotation = getattr(output, "exclusive_speaker_diarization", output)

    return [
        {"start": turn.start, "end": turn.end, "speaker": speaker}
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def _speaker_at(start, end, turns):
    """Speaker with the largest overlap with [start, end]; closest one if no overlap."""
    best, best_overlap = None, 0.0
    for t in turns:
        overlap = min(end, t["end"]) - max(start, t["start"])
        if overlap > best_overlap:
            best, best_overlap = t["speaker"], overlap
    if best is None and turns:
        mid = (start + end) / 2
        best = min(turns, key=lambda t: min(abs(mid - t["start"]), abs(mid - t["end"])))["speaker"]
    return best


def assign_speakers(words, turns, label="Speaker"):
    """
    Assign a speaker to each word and group consecutive words of the same speaker
    into utterances. Speakers are renamed "Speaker 1", "Speaker 2", ... in order
    of first appearance.

    words: [{"start", "end", "word"}, ...] (from faster-whisper word_timestamps)
    """
    names = {}
    utterances = []
    for w in words:
        raw = _speaker_at(w["start"], w["end"], turns) or "UNKNOWN"
        if raw not in names:
            names[raw] = f"{label} {len(names) + 1}"
        speaker = names[raw]

        if utterances and utterances[-1]["speaker"] == speaker:
            u = utterances[-1]
            u["end"] = w["end"]
            u["text"] += w["word"]
        else:
            utterances.append({"speaker": speaker, "start": w["start"],
                               "end": w["end"], "text": w["word"]})

    for u in utterances:
        u["text"] = u["text"].strip()
        u["start"], u["end"] = round(u["start"], 2), round(u["end"], 2)
    return utterances
