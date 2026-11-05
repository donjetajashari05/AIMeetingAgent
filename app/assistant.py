"""
"Pyet arkivin": find relevant passages from past meetings and let Claude
answer with numbered sources.

Retrieval is full-text search for now; step 5 replaces it with embeddings
(pgvector) without changing this interface.
"""
import anthropic

from . import db

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You answer questions about a team's past meetings using ONLY the
numbered excerpts provided. Answer in the language of the question, in 1-4 sentences.
Cite the excerpts you used with their numbers in square brackets, e.g. [1] or [2][3].
If the excerpts do not contain the answer, say so plainly instead of guessing."""


def _format_source(i, s):
    where = f"{s['meeting_title']} ({s['meeting_date']})"
    kind = {"summary": "përmbledhje", "decision": "vendim", "task": "detyrë"}.get(s["kind"])
    prefix = f"{s['speaker']}: " if s.get("speaker") else (f"[{kind}] " if kind else "")
    return f"[{i}] {where}: {prefix}{s['text']}"


def ask(question, has_key, meeting_ids=None):
    """meeting_ids: the meetings this user may see (None = all)."""
    sources = db.search(question, meeting_ids=meeting_ids)
    for i, s in enumerate(sources, 1):
        s["n"] = i

    if not sources:
        return {"answer": "Nuk gjeta asgjë për këtë në mbledhjet e ruajtura.", "sources": [], "ai": False}
    if not has_key:
        return {"answer": "Këto janë pjesët më të afërta që u gjetën. Shto ANTHROPIC_API_KEY "
                          "që Claude të japë përgjigje të plotë.", "sources": sources, "ai": False}

    excerpts = "\n".join(_format_source(i, s) for i, s in enumerate(sources, 1))
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"<excerpts>\n{excerpts}\n</excerpts>\n\nQuestion: {question}"}],
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return {"answer": "Modeli nuk e përpunoi këtë pyetje.", "sources": sources, "ai": False}
    answer = "".join(b.text for b in response.content if b.type == "text").strip()
    return {"answer": answer, "sources": sources, "ai": True}
