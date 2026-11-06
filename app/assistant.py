"""
"Ask the archive": find relevant passages from past meetings and let the language
model (Claude or Groq, see scripts/llm.py) answer with numbered sources.

Retrieval is full-text search for now; step 5 replaces it with embeddings
(pgvector) without changing this interface.
"""
import sys

from . import db

if str(db.ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(db.ROOT / "scripts"))

import llm  # noqa: E402  (scripts/llm.py)

SYSTEM_PROMPT = """You answer questions about a team's past meetings using ONLY the
numbered excerpts provided. Answer in the language of the question, in 1-4 sentences.
Cite the excerpts you used with their numbers in square brackets, e.g. [1] or [2][3].
If the excerpts do not contain the answer, say so plainly instead of guessing."""


def _format_source(i, s):
    where = f"{s['meeting_title']} ({s['meeting_date']})"
    kind = {"summary": "summary", "decision": "decision", "task": "task"}.get(s["kind"])
    prefix = f"{s['speaker']}: " if s.get("speaker") else (f"[{kind}] " if kind else "")
    return f"[{i}] {where}: {prefix}{s['text']}"


def ask(question, has_key, meeting_ids=None):
    """meeting_ids: the meetings this user may see (None = all). Raises llm.LLMError."""
    sources = db.search(question, meeting_ids=meeting_ids)
    for i, s in enumerate(sources, 1):
        s["n"] = i

    if not sources:
        return {"answer": "I found nothing about this in the saved meetings.", "sources": [], "ai": False}
    if not has_key:
        return {"answer": "These are the closest passages found. Add GROQ_API_KEY or ANTHROPIC_API_KEY "
                          "for a full AI answer.", "sources": sources, "ai": False}

    excerpts = "\n".join(_format_source(i, s) for i, s in enumerate(sources, 1))
    answer = llm.text(SYSTEM_PROMPT, f"<excerpts>\n{excerpts}\n</excerpts>\n\nQuestion: {question}", max_tokens=2000)
    return {"answer": answer, "sources": sources, "ai": True}
