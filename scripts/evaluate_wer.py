"""
Compute the WER (Word Error Rate) by comparing the Whisper transcript
with a manually written reference transcript.

Example:
    python scripts/evaluate_wer.py data/reference/meeting1.txt output/meeting1.txt
"""
import argparse
import re

import jiwer


def normalize(text: str) -> str:
    """Lowercase, remove punctuation, collapse extra whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def main():
    parser = argparse.ArgumentParser(description="WER calculation")
    parser.add_argument("reference", help="Correct (manual) transcript")
    parser.add_argument("hypothesis", help="Transcript produced by Whisper")
    args = parser.parse_args()

    with open(args.reference, encoding="utf-8") as f:
        ref = normalize(f.read())
    with open(args.hypothesis, encoding="utf-8") as f:
        hyp = normalize(f.read())

    out = jiwer.process_words(ref, hyp)
    total = out.hits + out.substitutions + out.deletions

    print(f"WER:           {out.wer:.2%}")
    print(f"Total words:   {total}")
    print(f"Substitutions: {out.substitutions}")
    print(f"Deletions:     {out.deletions}")
    print(f"Insertions:    {out.insertions}")


if __name__ == "__main__":
    main()