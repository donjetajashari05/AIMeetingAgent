"""
Duplicate check for tasks and tickets.

The AI already links new tasks to earlier ones (`continues`), but it can miss one, so every new
task is also compared in code with the work that is still open. Two descriptions count as the
same work when their important words mostly match ("Fix the login page bug" ~ "Fix login bug
on the login page"), or when the text is almost identical.
"""
import re
from difflib import SequenceMatcher

STOP = set("""a an the to of for in on at by with and or from into onto up out about as is are be
this that these those it its our their his her my your new all any some via per
""".split())

THRESHOLD = 0.6          # share of important words that must match
DIFFERENT_PEOPLE = 0.8   # stricter when the tasks clearly belong to different people


def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


# Words that mean the same in ticket titles ("" = ignore the word)
SAME = {"resolve": "fix", "solve": "fix", "repair": "fix", "authentication": "auth", "signin": "login",
        "implement": "build", "develop": "build", "create": "build", "make": "build",
        "setup": "set", "configure": "set", "verify": "check", "issue": "bug", "problem": "bug",
        "error": "bug", "db": "database", "screen": "page", "user": "", "application": "app"}


def _norm(w):
    for x in (w, w[:-1] if w.endswith("s") else w, _stem(w)):
        if x in SAME:
            return SAME[x]
    return _stem(w)


def words(text):
    out = {_norm(w) for w in re.findall(r"[a-z0-9ëç]+", (text or "").lower()) if w not in STOP and len(w) > 1}
    out.discard("")
    return out


def _speaker_label(name):
    return not name or bool(re.match(r"(?i)^(folësi|folesi|speaker)\b", name.strip()))


def similarity(a, b):
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    jaccard = len(wa & wb) / len(wa | wb)
    text = SequenceMatcher(None, " ".join(sorted(wa)), " ".join(sorted(wb))).ratio()
    return max(jaccard, text if text >= 0.9 else 0.0)


def same_work(a, b, who_a=None, who_b=None):
    """True when description a and b look like the same piece of work."""
    need = THRESHOLD
    if not _speaker_label(who_a) and not _speaker_label(who_b) and who_a.strip().lower() != who_b.strip().lower():
        need = DIFFERENT_PEOPLE
    return similarity(a, b) >= need


def best_match(description, candidates, who=None, key="description", who_key="who"):
    """The candidate (dict) that is the same work as `description`, or None."""
    best, best_score = None, 0.0
    for c in candidates:
        if same_work(description, c.get(key), who, c.get(who_key)):
            s = similarity(description, c.get(key))
            if s > best_score:
                best, best_score = c, s
    return best


def collapse(tasks):
    """Remove repeats inside one list of AI tasks (keeps the first; takes a missing due date
    from the repeat)."""
    kept = []
    for t in tasks:
        dup = best_match(t["description"], kept, t.get("assignee"), who_key="assignee")
        if dup:
            if not dup.get("due_date") and t.get("due_date"):
                dup["due_date"], dup["due_date_text"] = t["due_date"], t.get("due_date_text")
            if not dup.get("assignee") and t.get("assignee"):
                dup["assignee"] = t["assignee"]
            rank = {"high": 0, "medium": 1, "low": 2}
            if rank.get(t.get("priority"), 3) < rank.get(dup.get("priority"), 3):
                dup["priority"], dup["priority_reason"] = t["priority"], t.get("priority_reason")
            if (t.get("estimate_hours") or 0) > (dup.get("estimate_hours") or 0):
                dup["estimate_hours"] = t["estimate_hours"]
            continue
        kept.append(t)
    return kept
