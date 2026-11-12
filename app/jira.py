"""
Jira Cloud: create the tasks of a meeting as Jira issues.

.env:
    JIRA_BASE_URL=https://kompania.atlassian.net
    JIRA_EMAIL=emri@kompania.com          (the account that owns the API token)
    JIRA_API_TOKEN=<https://id.atlassian.com/manage-profile/security/api-tokens>
    JIRA_ISSUE_TYPE=Task                  (optional)
    JIRA_PRIORITY_HIGH=High               (optional: the Jira priority names for high / medium / low)
    JIRA_PRIORITY_MEDIUM=Medium
    JIRA_PRIORITY_LOW=Low
    JIRA_SEND_PRIORITY=1                  (0 = don't set priority / estimate fields, only write them in the text)

Each project in the app has a Jira key (e.g. PAY). The assignee is found in Jira
by the employee's work email.
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db


class JiraError(Exception):
    pass


def mock():
    """JIRA_MOCK=1: test mode. No Jira server is called; issues are written to data/jira_mock.jsonl."""
    return (os.environ.get("JIRA_MOCK") or "").strip().lower() in ("1", "true", "yes")


MOCK_FILE = db.ROOT / "data" / "jira_mock.jsonl"


def configured():
    return mock() or all(os.environ.get(k) for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))


def _mock_create(fields, meeting_link):
    """Fake 'create issue': next number for the project key, saved locally."""
    prefix = fields["project"]["key"] + "-"
    with db.connect() as c:
        keys = [r[0] for r in c.execute("SELECT jira_key FROM tasks WHERE jira_key LIKE ?", (prefix + "%",))]
    n = max([int(k[len(prefix):]) for k in keys if k[len(prefix):].isdigit()] + [0]) + 1
    key = f"{prefix}{n}"
    MOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MOCK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "created_at": db.now(), "fields": fields}, ensure_ascii=False) + "\n")
    return key, meeting_link


def _base():
    return os.environ["JIRA_BASE_URL"].rstrip("/")


def _request(method, path, body=None):
    token = base64.b64encode(f"{os.environ['JIRA_EMAIL']}:{os.environ['JIRA_API_TOKEN']}".encode()).decode()
    req = urllib.request.Request(
        _base() + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Basic {token}", "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())
            msgs = detail.get("errorMessages", []) + [f"{k}: {v}" for k, v in (detail.get("errors") or {}).items()]
            msg = "; ".join(msgs) or str(detail)
        except Exception:
            msg = e.reason
        raise JiraError(f"Jira {e.code}: {msg}"[:400])
    except urllib.error.URLError as e:
        raise JiraError(f"S’u arrit Jira: {e.reason}")


def find_account_id(email, cache):
    if not email:
        return None
    if email in cache:
        return cache[email]
    users = _request("GET", "/rest/api/3/user/search?" + urllib.parse.urlencode({"query": email})) or []
    exact = [u for u in users if (u.get("emailAddress") or "").lower() == email.lower()]
    pick = exact[0] if exact else (users[0] if len(users) == 1 else None)
    cache[email] = pick["accountId"] if pick else None
    return cache[email]


def _adf(paragraphs):
    """Jira's document format (ADF) for a simple description."""
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs if p]}


def estimate_text(hours):
    """4 -> '4h', 1.5 -> '1h 30m', 12 -> '1d 4h' (Jira's format, 1 day = 8 h)."""
    if not hours:
        return None
    minutes = int(round(hours * 60))
    d, rest = divmod(minutes, 8 * 60)
    h, m = divmod(rest, 60)
    return " ".join(x for x in (f"{d}d" if d else "", f"{h}h" if h else "", f"{m}m" if m else "") if x) or None


def _plan_line(t):
    """'Priority: High (blocks the release) · Estimate: 4h' for the description (also when the fields fail)."""
    parts = []
    if t.get("priority"):
        parts.append(f"Priority: {t['priority'].capitalize()}"
                     + (f" ({t['priority_reason']})" if t.get("priority_reason") else ""))
    if t.get("estimate_hours"):
        parts.append(f"Estimate: {estimate_text(t['estimate_hours'])}")
    return " · ".join(parts) or None


def _plan_fields(t):
    """Jira fields for the priority and the original estimate."""
    if os.environ.get("JIRA_SEND_PRIORITY", "1") == "0":
        return {}
    out = {}
    if t.get("priority"):
        name = os.environ.get(f"JIRA_PRIORITY_{t['priority'].upper()}") or t["priority"].capitalize()
        out["priority"] = {"name": name}
    if t.get("estimate_hours"):
        out["timetracking"] = {"originalEstimate": estimate_text(t["estimate_hours"])}
    return out


def _create_issue(fields):
    """POST the issue. Some Jira projects don't have priority or time tracking on their create screen:
    then the issue is made without those fields (the description still says them)."""
    try:
        return _request("POST", "/rest/api/3/issue", {"fields": fields})
    except JiraError as e:
        extra = [k for k in ("priority", "timetracking") if k in fields]
        if not extra or "400" not in str(e) or not any(k in str(e).lower() for k in ("priority", "timetracking", "time tracking")):
            raise
        return _request("POST", "/rest/api/3/issue", {"fields": {k: v for k, v in fields.items() if k not in extra}})


def _default_key():
    return (os.environ.get("JIRA_DEFAULT_PROJECT") or "").strip().upper() or ("TEST" if mock() else "")


def push_tasks(task_ids, sender_id=None):
    """Send the chosen tickets (page "Tickets") to Jira.

    Only tickets that are not sent yet and have a recipient email go out.
    Each ticket is assigned in Jira to the person with that email, and the person
    gets an email about it. Returns {"created": [keys], "errors": {task_id: message}}.
    """
    from . import mailer  # local import: mailer imports db too

    if not configured():
        raise JiraError("Jira is not configured. Add JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN to .env.")
    with db.connect() as c:
        tasks = db.rows(c.execute(db.TASK_SELECT + f" WHERE t.id IN ({db._in(task_ids)})", tuple(task_ids)))

    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")
    app_url = (os.environ.get("APP_URL") or "http://127.0.0.1:8000").rstrip("/")
    cache, created, errors = {}, [], {}
    for t in tasks:
        if t["jira_key"]:
            errors[t["id"]] = f"Already sent ({t['jira_key']})."
            continue
        if not t["is_ticket"]:
            errors[t["id"]] = "Make a ticket from this task first (page Tasks)."
            continue
        if not t["recipient_email"]:
            errors[t["id"]] = "Add the email of the person who gets the ticket."
            continue
        project_key = t["ticket_project"] or _default_key()
        if not project_key:
            errors[t["id"]] = "Add the Jira project (e.g. PAY)."
            continue
        manual = t.get("meeting_source") == "manual"  # written by hand on the Tickets page, no meeting
        link = f"{app_url}/#/tasks?tab=sent" if manual else f"{app_url}/#/meeting/{t['meeting_id']}"
        fields = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            "summary": t["description"][:250],
            "description": _adf([
                t["details"],
                _plan_line(t),
                None if manual else f"From the meeting “{t['meeting_title']}” ({t['meeting_date']}).",
                f"Quote: “{t['quote']}”" if t["quote"] else None,
                f"Deadline as said: {t['due_date_text']}" if t["due_date_text"] else None,
                "Written by the PM in AnchorzUp Meeting Agent." if manual else f"Created by AnchorzUp Meeting Agent: {link}",
            ]),
            "labels": ["meeting-agent"],
        }
        if t["due_date"]:
            fields["duedate"] = t["due_date"]
        fields.update(_plan_fields(t))
        try:
            if mock():
                fields["assignee_email"] = t["recipient_email"]  # recorded in the mock file only
                key, url = _mock_create(fields, link)
            else:
                account = find_account_id(t["recipient_email"], cache)
                if not account:
                    raise JiraError(f"No Jira user was found with the email {t['recipient_email']}.")
                fields["assignee"] = {"accountId": account}
                key = _create_issue(fields)["key"]
                url = f"{_base()}/browse/{key}"
        except JiraError as e:
            errors[t["id"]] = str(e)
            with db.connect() as c:
                c.execute("UPDATE tasks SET jira_error = ? WHERE id = ?", (str(e), t["id"]))
            continue
        with db.connect() as c:
            c.execute("UPDATE tasks SET jira_key = ?, jira_url = ?, jira_error = NULL, sent_at = ?, sent_by = ? WHERE id = ?",
                      (key, url, db.now(), sender_id, t["id"]))
        created.append(key)
        # Tell the person (kept in "Emails"; only sent out when SMTP is configured)
        mailer.send(
            t["recipient_email"], f"New ticket for you: {key} {t['description'][:80]}",
            "\n\n".join(x for x in [
                f"The ticket {key} has been assigned to you.",
                t["description"],
                t["details"],
                f"Due: {t['due_date']}" if t["due_date"] else None,
                _plan_line(t),
                None if manual else f"From the meeting “{t['meeting_title']}” ({t['meeting_date']}).",
            ] if x),
            kind="ticket", link=url, link_text="Open the ticket")
    return {"created": created, "errors": errors}


def push_meeting(meeting_id, task_ids=None):
    """Create Jira issues for the meeting's tasks the PM has confirmed and that don't have one yet.
    task_ids limits it to those tasks. Returns {"created": [...keys], "failed": n, "skipped": n}."""
    if not configured():
        raise JiraError("Jira is not configured. Add JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN to .env.")
    m = db.meeting_detail(meeting_id)
    with db.connect() as c:
        proj = c.execute("SELECT name, jira_key FROM projects WHERE id = ?", (m["project_id"],)).fetchone() if m["project_id"] else None
        emails = {r["id"]: r["email"] for r in c.execute("SELECT id, email FROM employees")}
    # A project's own key wins; otherwise JIRA_DEFAULT_PROJECT (e.g. KAN) for every meeting
    default_key = (os.environ.get("JIRA_DEFAULT_PROJECT") or "").strip().upper() or ("TEST" if mock() else "")
    if not (proj and proj["jira_key"]) and default_key:
        proj = {"jira_key": default_key}
    if not proj or not proj["jira_key"]:
        raise JiraError("Link the meeting to a project that has a Jira key (e.g. PAY) on the Team page.")

    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")
    cache, created, failed, skipped = {}, [], 0, 0
    link = f"{(os.environ.get('APP_URL') or 'http://127.0.0.1:8000').rstrip('/')}/#/meeting/{meeting_id}"
    for t in m["tasks"]:
        if task_ids is not None and t["id"] not in task_ids:
            continue
        if t["jira_key"] or t["status"] == "done" or not t["approved_at"] or not t["is_ticket"]:  # only confirmed tickets
            skipped += 1
            continue
        fields = {
            "project": {"key": proj["jira_key"]},
            "issuetype": {"name": issue_type},
            "summary": t["description"][:250],
            "description": _adf([
                f"From the meeting “{m['title']}” ({m['meeting_date']}).",
                _plan_line(t),
                f"Quote: “{t['quote']}”" if t["quote"] else None,
                f"Deadline as said: {t['due_date_text']}" if t["due_date_text"] else None,
                f"Created by AnchorzUp Meeting Agent: {link}",
            ]),
            "labels": ["meeting-agent"],
        }
        if t["due_date"]:
            fields["duedate"] = t["due_date"]
        fields.update(_plan_fields(t))
        try:
            if mock():
                key, url = _mock_create(fields, link)
                with db.connect() as c:
                    c.execute("UPDATE tasks SET jira_key = ?, jira_url = ?, jira_error = NULL WHERE id = ?", (key, url, t["id"]))
                created.append(key)
                continue
            account = find_account_id(emails.get(t["assignee_id"]), cache) if t["assignee_id"] else None
            if account:
                fields["assignee"] = {"accountId": account}
            res = _create_issue(fields)
            key = res["key"]
            with db.connect() as c:
                c.execute("UPDATE tasks SET jira_key = ?, jira_url = ?, jira_error = NULL WHERE id = ?",
                          (key, f"{_base()}/browse/{key}", t["id"]))
            created.append(key)
        except JiraError as e:
            failed += 1
            with db.connect() as c:
                c.execute("UPDATE tasks SET jira_error = ? WHERE id = ?", (str(e), t["id"]))
    return {"created": created, "failed": failed, "skipped": skipped}


# ---------- reopen ----------

def reopen_issue(key, comment=None):
    """Move a finished issue back out of Done (the same work came up again) and leave a comment.
    Uses the workflow's own transition: one called "Reopen…" first, otherwise the first one that
    leads to a To Do / In Progress status. Returns the new status name; raises JiraError."""
    if mock():
        MOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(MOCK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "reopened_at": db.now(), "comment": comment}, ensure_ascii=False) + "\n")
        return "Reopened"
    path = f"/rest/api/3/issue/{urllib.parse.quote(key)}"
    status = (_request("GET", f"{path}?fields=status") or {}).get("fields", {}).get("status") or {}
    if status.get("statusCategory", {}).get("key") != "done":
        return status.get("name")  # already open in Jira: nothing to move
    transitions = (_request("GET", f"{path}/transitions") or {}).get("transitions", [])
    back = [t for t in transitions if (t.get("to") or {}).get("statusCategory", {}).get("key") in ("new", "indeterminate")]
    pick = (next((t for t in back if "reopen" in (t.get("name") or "").lower()), None)
            or next((t for t in back if t["to"]["statusCategory"]["key"] == "new"), None)
            or (back[0] if back else None))
    if not pick:
        raise JiraError(f"{key} can’t be moved out of Done (no such transition in its workflow).")
    _request("POST", f"{path}/transitions", {"transition": {"id": pick["id"]}})
    if comment:
        try:
            _request("POST", f"{path}/comment", {"body": _adf([comment])})
        except JiraError:
            pass  # the reopen counts even if the comment fails
    return pick["to"].get("name") or pick.get("name")


# ---------- Jira -> app: status ----------

_sync_lock = threading.Lock()
_last_sync = 0.0
SYNC_EVERY = 60  # seconds


def _statuses(keys):
    """{key: (status name, category)} for these issues; category is new | indeterminate | done."""
    out = {}
    for i in range(0, len(keys), 50):
        batch = keys[i:i + 50]
        try:
            jql = urllib.parse.quote(f"key in ({','.join(batch)})")
            r = _request("GET", f"/rest/api/3/search/jql?jql={jql}&fields=status&maxResults=100")
            issues = r.get("issues", [])
        except JiraError:
            # one deleted issue makes the whole JQL fail: ask one by one and skip the missing ones
            issues = []
            for k in batch:
                try:
                    issues.append(_request("GET", f"/rest/api/3/issue/{urllib.parse.quote(k)}?fields=status"))
                except JiraError:
                    pass
        for it in issues:
            st = it["fields"]["status"]
            out[it["key"]] = (st["name"], st["statusCategory"]["key"])
    return out


def sync_status(force=False):
    """Read the status of the sent tickets back from Jira (at most once a minute).
    Only changes count: moved to Done in Jira -> done here (also the tasks the ticket was made from);
    moved back out of Done -> open again, unless the PM closed it here by hand. A status the PM sets
    here stays until the issue moves again in Jira. Returns the number of changes."""
    global _last_sync
    if mock() or not configured():
        return 0
    if not force and time.time() - _last_sync < SYNC_EVERY:
        return 0
    if not _sync_lock.acquire(blocking=False):
        return 0  # another request is already syncing
    try:
        _last_sync = time.time()
        with db.connect() as c:
            rows = db.rows(c.execute("SELECT id, jira_key, status, done_by_jira, jira_status FROM tasks WHERE jira_key IS NOT NULL "
                                     "ORDER BY id DESC LIMIT 1000"))
        if not rows:
            return 0
        found = _statuses([r["jira_key"] for r in rows])
        changes = 0
        with db.connect() as c:
            for r in rows:
                if r["jira_key"] not in found:
                    continue
                name, cat = found[r["jira_key"]]
                if name == r["jira_status"]:
                    continue  # nothing moved in Jira: what the PM set here by hand stays
                c.execute("UPDATE tasks SET jira_status = ? WHERE id = ?", (name, r["id"]))
                if cat == "done":
                    # the ticket and the tasks it was made from
                    cur = c.execute("UPDATE tasks SET status = 'done', completed_at = ?, done_by_jira = 1 "
                                    "WHERE (id = ? OR merged_into = ?) AND status = 'open'", (db.now(), r["id"], r["id"]))
                    changes += cur.rowcount
                else:
                    cur = c.execute("UPDATE tasks SET status = 'open', completed_at = NULL, done_by_jira = 0 "
                                    "WHERE (id = ? OR merged_into = ?) AND status = 'done' AND done_by_jira = 1",
                                    (r["id"], r["id"]))
                    changes += cur.rowcount
        return changes
    except Exception as e:  # Jira down or token expired: the app keeps working
        print(f"Jira status sync failed: {e}")
        return 0
    finally:
        _sync_lock.release()


def start_sync():
    """Background loop: keeps the statuses fresh even when nobody has the app open."""
    def loop():
        while True:
            sync_status()
            time.sleep(SYNC_EVERY)
    threading.Thread(target=loop, daemon=True, name="jira-sync").start()
