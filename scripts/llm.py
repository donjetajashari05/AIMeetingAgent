"""
One place for the language model, with two providers:

  anthropic  - Claude (ANTHROPIC_API_KEY). Used when the key is set.
  groq       - Groq's free tier (GROQ_API_KEY), for testing. OpenAI-compatible API,
               called over plain HTTP, so no extra package is needed.

LLM_PROVIDER=anthropic|groq forces one; otherwise Claude is preferred when both keys exist.
Note: with Groq the transcript is sent to Groq's servers.
"""
import json
import os
import urllib.error
import urllib.request

from pydantic import BaseModel, ValidationError

CLAUDE_MODEL = "claude-opus-5-5"
GROQ_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
NO_KEY = "No AI key: add GROQ_API_KEY (free) or ANTHROPIC_API_KEY to .env."


class LLMError(Exception):
    pass


def provider():
    """'anthropic', 'groq', or None when no key is configured."""
    forced = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    has = {"anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")), "groq": bool(os.environ.get("GROQ_API_KEY"))}
    if forced in has:
        return forced if has[forced] else None
    return "anthropic" if has["anthropic"] else "groq" if has["groq"] else None


def available():
    return provider() is not None


def groq_model():
    return os.environ.get("GROQ_MODEL") or GROQ_DEFAULT_MODEL


def label():
    return {"anthropic": "Claude", "groq": f"Groq · {groq_model()}"}.get(provider(), "—")


# ---------- Groq (OpenAI-compatible HTTP API) ----------

def _groq(path, body=None):
    req = urllib.request.Request(
        GROQ_URL + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                 "Content-Type": "application/json", "User-Agent": "meeting-agent"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = str(e.reason)
        if e.code == 401:
            raise LLMError("GROQ_API_KEY is not valid.")
        if e.code == 429:
            raise LLMError("Groq: free request limit reached. Wait a little and try again.")
        if e.code in (400, 404) and "model" in msg.lower():
            models = ", ".join(groq_models()) or "see console.groq.com/docs/models"
            raise LLMError(f"Groq: the model “{groq_model()}” doesn’t work ({msg}). Set GROQ_MODEL in .env. Models: {models}")
        raise LLMError(f"Groq {e.code}: {msg}"[:400])
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach Groq: {e.reason}")


def groq_models():
    try:
        return sorted(m["id"] for m in _groq("/models").get("data", []))
    except Exception:
        return []


def _groq_chat(system, user, max_tokens, json_mode):
    body = {
        "model": groq_model(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_completion_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    choice = _groq("/chat/completions", body)["choices"][0]
    if choice.get("finish_reason") == "length":
        raise LLMError("The answer was cut off (too long). Try a shorter transcript.")
    return choice["message"].get("content") or ""


# ---------- Claude ----------

def _claude(call):
    """Run an Anthropic SDK call, turning its errors into LLMError."""
    import anthropic

    try:
        return call(anthropic.Anthropic())
    except anthropic.AuthenticationError:
        raise LLMError("ANTHROPIC_API_KEY is not valid.")
    except anthropic.RateLimitError:
        raise LLMError("Claude: too many requests. Wait a little and try again.")
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach Claude. Check the internet connection.")
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error: {e.message}")


# ---------- Public API ----------

def structured(system, user, schema: type[BaseModel], max_tokens=16000, effort="medium"):
    """Ask for JSON matching `schema` and return a validated instance."""
    p = provider()
    if p == "anthropic":
        response = _claude(lambda client: client.beta.messages.parse(
            model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema, output_config={"effort": effort},
            # If the model declines, the API retries on a fallback model in the same call
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        ))
        if response.stop_reason == "refusal":
            raise LLMError("The model declined to process this transcript.")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMError("The answer was cut off (too long). Try a shorter transcript.")
        return response.parsed_output

    if p == "groq":
        # JSON mode + the schema in the prompt, validated with Pydantic; one retry on bad output
        shape = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        sys_prompt = (f"{system}\n\nReply with ONE JSON object only (no markdown, no comments) that matches "
                      f"this JSON Schema exactly:\n{shape}")
        problem = None
        for _ in range(2):
            prompt = user if not problem else f"{user}\n\nYour previous reply was invalid ({problem}). Return valid JSON only."
            raw = _groq_chat(sys_prompt, prompt, max_tokens, json_mode=True)
            try:
                return schema.model_validate_json(raw)
            except ValidationError as e:
                problem = str(e).splitlines()[0][:300]
        raise LLMError(f"Groq returned invalid JSON: {problem}")

    raise LLMError(NO_KEY)


def text(system, user, max_tokens=2000):
    """Plain-text answer."""
    p = provider()
    if p == "anthropic":
        response = _claude(lambda client: client.beta.messages.create(
            model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}], output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        ))
        if response.stop_reason == "refusal":
            raise LLMError("The model declined to answer this question.")
        return "".join(b.text for b in response.content if b.type == "text").strip()
    if p == "groq":
        return _groq_chat(system, user, max_tokens, json_mode=False).strip()
    raise LLMError(NO_KEY)
