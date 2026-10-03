"""
vula/clickup/service.py

Thin async wrapper over the ClickUp REST API v2, scoped per tenant. All calls
resolve the tenant's stored token + list ids via credentials.get_tenant_clickup_creds.

ClickUp API: base https://api.clickup.com/api/v2, header Authorization: <token>.
Dates are Unix epoch milliseconds.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from vula.clickup.credentials import get_tenant_clickup_creds, default_list_id, _client

logger = logging.getLogger(__name__)

_BASE = "https://api.clickup.com/api/v2"

# South Africa Standard Time — no DST, fixed UTC+2. Relative dates ("today", "Friday")
# are resolved against this, not UTC, so a message sent late at night SAST still lands
# on the SA calendar day the sender meant.
_SAST = timezone(timedelta(hours=2))

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}


class ClickUpNotConnected(Exception):
    """Raised when a tenant has no connected ClickUp account."""


def _headers(token: str) -> dict:
    return {"Authorization": token, "Content-Type": "application/json"}


# People say "in two weeks", not "in 2 weeks" — especially in a voice note.
# 2026-09-02, real Gerflor transcript: "Please remind me to contact Danielle in two weeks" came
# back as "I couldn't set a reminder", because the resolver only understood digits. "in 2 weeks"
# worked; the identical request in words did not. Also normalises the trailing "time" in
# "in two weeks time", and "a"/"an"/"a couple of" which are the same thought spoken differently.
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "couple": 2, "few": 3, "fortnight": 14,
}


def _normalise_number_words(text: str) -> str:
    """Rewrite spoken quantities into the digit form the patterns below expect."""
    t = re.sub(r"\bin a fortnight\b", "in 14 days", text)
    t = re.sub(r"\ba couple of\b", "2", t)
    t = re.sub(r"\ba few\b", "3", t)
    t = re.sub(r"\s+time$", "", t)          # "in two weeks time"
    t = re.sub(r"\s+from now$", "", t)      # "in two weeks from now"

    def _sub(m):
        word = m.group(1)
        return str(_NUMBER_WORDS[word]) if word in _NUMBER_WORDS else m.group(0)

    pattern = r"\b(" + "|".join(_NUMBER_WORDS) + r")\b(?=\s+(?:day|week|month)s?\b)"
    return re.sub(pattern, _sub, t)


def _resolve_relative_date(text: str) -> Optional[datetime]:
    """Deterministically resolve common relative-date phrases ('today', 'tomorrow',
    'Friday', 'next Friday', 'in 3 days') against the server's real current date.

    This exists because asking an LLM to do its own date arithmetic in the prompt is
    unreliable (observed: 'next Friday' resolving to today, or to an arbitrary past-ish
    date) — so this is the source of truth for relative phrasing, not a fallback the LLM
    is expected to get right first. Returns None for anything it doesn't recognise (the
    caller then tries ISO parsing instead).

    Convention for weekday names (the "next Friday" problem has no single correct
    answer in English, so this picks one and is consistent): a bare or "this "-prefixed
    weekday means the nearest occurrence, which may be today; "next "-prefixed always
    means an occurrence strictly after today (skips to the following week if today
    already is that weekday).
    """
    text = re.sub(r"\s+", " ", (text or "").strip().lower())
    text = _normalise_number_words(text)
    today = datetime.now(_SAST).date()

    if text == "today":
        target = today
    elif text == "tomorrow":
        target = today + timedelta(days=1)
    elif (m := re.fullmatch(r"in (\d+) days?", text)):
        target = today + timedelta(days=int(m.group(1)))
    elif (m := re.fullmatch(r"in (\d+) weeks?", text)):
        target = today + timedelta(weeks=int(m.group(1)))
    elif (m := re.fullmatch(r"in (\d+) months?", text)):
        target = today + timedelta(days=30 * int(m.group(1)))
    elif (m := re.fullmatch(r"(next |this )?(\w+day)", text)) and m.group(2) in _WEEKDAYS:
        prefix, day_name = (m.group(1) or "").strip(), m.group(2)
        delta = (_WEEKDAYS[day_name] - today.weekday()) % 7
        if prefix == "next" and delta == 0:
            delta = 7
        target = today + timedelta(days=delta)
    else:
        return None

    return datetime(target.year, target.month, target.day, tzinfo=_SAST)


def _to_epoch_ms(value: Optional[str]) -> Optional[int]:
    """Parse a due-date value into epoch ms, else None.

    Tries deterministic relative-date phrases first (see _resolve_relative_date), then
    falls back to ISO date/datetime or 'YYYY-MM-DD' for anything already in that form.
    """
    if not value:
        return None
    s = str(value).strip()

    rel = _resolve_relative_date(s)
    if rel:
        return int(rel.timestamp() * 1000)

    for parse in (
        lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")),
        lambda x: datetime.strptime(x, "%Y-%m-%d"),
    ):
        try:
            dt = parse(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue
    return None


def _creds_or_raise(tenant_id: str) -> dict:
    creds = get_tenant_clickup_creds(tenant_id)
    if not creds:
        raise ClickUpNotConnected(tenant_id)
    return creds


# ── OAuth (one-click connect) ─────────────────────────────────────────────────

async def exchange_code(code: str) -> Optional[str]:
    """Exchange a ClickUp OAuth code for a (long-lived) access token."""
    from config import settings
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(f"{_BASE}/oauth/token", params={
            "client_id": settings.clickup_client_id,
            "client_secret": settings.clickup_client_secret,
            "code": code,
        })
        r.raise_for_status()
        return r.json().get("access_token")


async def discover_team_and_lists(token: str) -> dict:
    """After OAuth, find the user's first workspace and its lists (folderless +
    folder lists). Returns {team_id, team_name, lists: [{id, name}], default}.
    """
    hdr = _headers(token)
    out: dict[str, Any] = {"team_id": None, "team_name": None, "lists": [], "default": None}
    async with httpx.AsyncClient(timeout=20.0) as client:
        teams = (await client.get(f"{_BASE}/team", headers=hdr)).json().get("teams", [])
        if not teams:
            return out
        team = teams[0]
        out["team_id"], out["team_name"] = team.get("id"), team.get("name")

        spaces = (await client.get(f"{_BASE}/team/{team['id']}/space",
                                   headers=hdr, params={"archived": "false"})).json().get("spaces", [])
        lists: list[dict] = []
        for sp in spaces:
            sid = sp.get("id")
            # Folderless lists
            fl = (await client.get(f"{_BASE}/space/{sid}/list",
                                   headers=hdr, params={"archived": "false"})).json().get("lists", [])
            lists += [{"id": l.get("id"), "name": l.get("name")} for l in fl]
            # Lists inside folders
            folders = (await client.get(f"{_BASE}/space/{sid}/folder",
                                        headers=hdr, params={"archived": "false"})).json().get("folders", [])
            for f in folders:
                for l in f.get("lists", []):
                    lists.append({"id": l.get("id"), "name": f"{f.get('name')} / {l.get('name')}"})
            if len(lists) >= 50:
                break
    out["lists"] = lists
    out["default"] = lists[0]["id"] if lists else None
    return out


async def create_task(tenant_id: str, title: str, description: str = "",
                      due_date: Optional[str] = None, list_id: Optional[str] = None,
                      status: Optional[str] = None, assignees: Optional[list] = None) -> dict:
    """Create a ClickUp task. Returns {id, url, name} (or {error})."""
    creds = _creds_or_raise(tenant_id)
    lid = list_id or default_list_id(creds)
    if not lid:
        return {"error": "No ClickUp list configured for this tenant."}
    body: dict[str, Any] = {"name": title}
    if description:
        body["description"] = description
    ms = _to_epoch_ms(due_date)
    if ms:
        body["due_date"] = ms
    if status:
        body["status"] = status
    if assignees:
        body["assignees"] = assignees
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(f"{_BASE}/list/{lid}/task", headers=_headers(creds["token"]), json=body)
        r.raise_for_status()
        d = r.json()
    return {"id": d.get("id"), "url": d.get("url"), "name": d.get("name")}


async def list_team_members(tenant_id: str) -> list[dict]:
    """List the tenant's ClickUp workspace members. Returns [{id, username, email}]."""
    creds = _creds_or_raise(tenant_id)
    team_id = creds.get("team_id")
    if not team_id:
        return []
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_BASE}/team/{team_id}", headers=_headers(creds["token"]))
        r.raise_for_status()
        members = r.json().get("team", {}).get("members", [])
    out = []
    for m in members:
        u = m.get("user") or {}
        if u.get("id"):
            out.append({"id": u.get("id"), "username": u.get("username") or "", "email": u.get("email") or ""})
    return out


async def resolve_assignee(tenant_id: str, name: str) -> Optional[dict]:
    """Match `name` (e.g. 'Nolo') against workspace members' username/email — case-insensitive
    substring match, so a first name is enough. Returns {id, username}, or None if no member matches."""
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for m in await list_team_members(tenant_id):
        if needle in (m["username"] or "").lower() or needle in (m["email"] or "").lower():
            return {"id": m["id"], "username": m["username"]}
    return None


async def list_tasks(tenant_id: str, list_id: Optional[str] = None,
                    status: Optional[str] = None, limit: int = 15,
                    assignee_id: Optional[str] = None) -> Any:
    """List tasks in a list, optionally filtered by status and/or assignee."""
    creds = _creds_or_raise(tenant_id)
    lid = list_id or default_list_id(creds)
    if not lid:
        return {"error": "No ClickUp list configured for this tenant."}
    params: dict[str, Any] = {"subtasks": "true", "include_closed": "false"}
    if status:
        params["statuses[]"] = status
    if assignee_id:
        params["assignees[]"] = assignee_id
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_BASE}/list/{lid}/task", headers=_headers(creds["token"]), params=params)
        r.raise_for_status()
        tasks = r.json().get("tasks", [])
    out = []
    for t in tasks[: max(1, min(int(limit or 15), 50))]:
        due = t.get("due_date")
        out.append({
            "id": t.get("id"),
            "title": t.get("name"),
            "status": (t.get("status") or {}).get("status"),
            "due_date": (datetime.fromtimestamp(int(due) / 1000, tz=timezone.utc).date().isoformat()
                         if due else None),
            "assignees": [a.get("username") for a in (t.get("assignees") or [])],
        })
    return out or {"message": "No tasks found."}


async def list_programme_tasks(tenant_id: str, list_id: str) -> list[dict]:
    """Every task in a list (closed ones too, all pages) with start and due dates, assignees and
    the list's name — a project programme read from ClickUp (vula/commerce/project_programme.py).
    Dates are the SAST calendar day."""
    from zoneinfo import ZoneInfo
    sast = ZoneInfo("Africa/Johannesburg")
    day = lambda ms: (datetime.fromtimestamp(int(ms) / 1000, tz=sast).date().isoformat() if ms else None)
    creds = _creds_or_raise(tenant_id)
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for page in range(20):
            r = await client.get(f"{_BASE}/list/{list_id}/task", headers=_headers(creds["token"]),
                                 params={"subtasks": "true", "include_closed": "true", "page": page})
            r.raise_for_status()
            body = r.json()
            for t in body.get("tasks") or []:
                out.append({
                    "id": t.get("id"), "name": t.get("name") or "",
                    "status": ((t.get("status") or {}).get("status") or "").lower(),
                    "closed": ((t.get("status") or {}).get("type") or "") in ("closed", "done"),
                    "start_date": day(t.get("start_date")), "due_date": day(t.get("due_date")),
                    "assignees": [a.get("username") for a in (t.get("assignees") or []) if a.get("username")],
                    "list": (t.get("list") or {}).get("name") or "",
                })
            if body.get("last_page", True) or not body.get("tasks"):
                break
    return out


async def find_task(tenant_id: str, query: str, list_id: Optional[str] = None) -> Optional[dict]:
    """Find the first task whose title contains `query` (case-insensitive)."""
    rows = await list_tasks(tenant_id, list_id=list_id, limit=50)
    if not isinstance(rows, list):
        return None
    q = (query or "").lower().strip()
    return next((t for t in rows if q and q in (t.get("title") or "").lower()), None)


async def get_task(tenant_id: str, task_id: str) -> Optional[dict]:
    """Fetch one task's full detail — status, list, tags — using this tenant's token.
    Returns None on a 404 (also used to probe "does this tenant's ClickUp see this
    task id?" when a webhook payload doesn't carry a tenant)."""
    creds = _creds_or_raise(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_BASE}/task/{task_id}", headers=_headers(creds["token"]))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        d = r.json()
    return {
        "id": d.get("id"), "title": d.get("name"), "description": d.get("description") or "",
        "status": ((d.get("status") or {}).get("status") or "").lower(),
        "list_id": (d.get("list") or {}).get("id"),
        "tags": [t.get("name", "").lower() for t in (d.get("tags") or [])],
        "assignees": [a.get("username") for a in (d.get("assignees") or [])],
    }


async def update_task_status(tenant_id: str, task_id: str, status: str, notes: str = "") -> dict:
    """Update a ClickUp task's status (and optionally append a comment)."""
    creds = _creds_or_raise(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.put(f"{_BASE}/task/{task_id}", headers=_headers(creds["token"]),
                             json={"status": status})
        r.raise_for_status()
        if notes:
            try:
                await client.post(f"{_BASE}/task/{task_id}/comment",
                                 headers=_headers(creds["token"]),
                                 json={"comment_text": notes})
            except Exception:
                pass
    return {"updated": task_id, "new_status": status}


async def update_task_status_by_name(tenant_id: str, title_query: str, status: str) -> dict:
    """Find a task by title fragment and update its status."""
    match = await find_task(tenant_id, title_query)
    if not match:
        return {"error": f"No task matching '{title_query}'."}
    res = await update_task_status(tenant_id, match["id"], status)
    res["title"] = match["title"]
    return res


async def update_task_due_date(tenant_id: str, task_id: str, due_date: str) -> dict:
    """Update a ClickUp task's due date. `due_date` goes through the same relative-date
    resolution as create_task (e.g. 'Friday', 'tomorrow', or an ISO date)."""
    creds = _creds_or_raise(tenant_id)
    ms = _to_epoch_ms(due_date)
    if not ms:
        return {"error": f"Couldn't understand due date '{due_date}'."}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.put(f"{_BASE}/task/{task_id}", headers=_headers(creds["token"]),
                             json={"due_date": ms})
        r.raise_for_status()
    return {"updated": task_id, "new_due_date": due_date}


async def update_task_due_date_by_name(tenant_id: str, title_query: str, due_date: str) -> dict:
    """Find a task by title fragment and update its due date."""
    match = await find_task(tenant_id, title_query)
    if not match:
        return {"error": f"No task matching '{title_query}'."}
    res = await update_task_due_date(tenant_id, match["id"], due_date)
    res["title"] = match["title"]
    return res


async def set_reminder(tenant_id: str, title: str, due_date: str,
                      list_id: Optional[str] = None) -> dict:
    """Create a due-dated task so ClickUp's own notifications act as the reminder.

    ClickUp's standalone reminder API is user-scoped and limited, so we model a
    reminder as a task with a due date (which triggers ClickUp notifications).
    """
    return await create_task(tenant_id, title=f"⏰ {title}", due_date=due_date, list_id=list_id)


_DOCS_TASK_TITLE = "📎 Project Documents"


async def attach_file_to_list(tenant_id: str, list_id: str, filename: str,
                              data: bytes, content_type: str = "application/octet-stream",
                              task_id: Optional[str] = None) -> dict:
    """File a document into a ClickUp list. ClickUp attachments are task-scoped,
    so we attach to one "📎 Project Documents" task. If `task_id` is given (an
    already-known project documents task) we attach straight to it; otherwise we
    find-or-create the task in `list_id`. Returns {task_id, attachment_id} or {error}.
    """
    creds = _creds_or_raise(tenant_id)
    token = creds["token"]

    if not task_id:
        # Find-or-create the documents task in this list.
        existing = await find_task(tenant_id, _DOCS_TASK_TITLE, list_id=list_id)
        if existing:
            task_id = existing["id"]
        else:
            created = await create_task(
                tenant_id, title=_DOCS_TASK_TITLE,
                description="Documents filed here automatically by Vula from WhatsApp.",
                list_id=list_id,
            )
            task_id = created.get("id")
            if not task_id:
                return {"error": created.get("error", "Could not create documents task.")}

    # Attach the file (multipart). Note: no Content-Type header — httpx sets the
    # multipart boundary; ClickUp auth header carries the token.
    async def _attach(tid: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await client.post(f"{_BASE}/task/{tid}/attachment",
                                     headers={"Authorization": token},
                                     files={"attachment": (filename, data, content_type)})

    r = await _attach(task_id)
    # 2026-09-29 (DIGG): every attach into ATLANTIS FOODS failed "401 Unauthorized" while the same
    # token synced fine — the remembered documents task had been deleted, moved or made private
    # in ClickUp. A refused/missing remembered task isn't a dead connection: file into a fresh
    # documents task in the project's list instead.
    if r.status_code in (401, 403, 404) and list_id:
        logger.warning("ClickUp refused documents task %s (%s) — using a fresh one in list %s",
                       task_id, r.status_code, list_id)
        existing = await find_task(tenant_id, _DOCS_TASK_TITLE, list_id=list_id)
        fresh = existing["id"] if existing and existing.get("id") != task_id else None
        if not fresh:
            created = await create_task(
                tenant_id, title=_DOCS_TASK_TITLE,
                description="Documents filed here automatically by Vula from WhatsApp.",
                list_id=list_id)
            fresh = created.get("id")
        if fresh:
            task_id = fresh
            r = await _attach(task_id)
    r.raise_for_status()
    d = r.json()
    return {"task_id": task_id, "attachment_id": d.get("id")}


# Everything that meaningfully changes a task the team cares about. Verified against ClickUp's
# published event list (developer.clickup.com/docs/webhooks). Previously only taskStatusUpdated
# and taskUpdated were subscribed, and the handler dropped everything that wasn't a status
# change — so an assignment, a due-date move, a priority bump or a comment in ClickUp never
# reached the person on WhatsApp.
WEBHOOK_EVENTS = [
    "taskCreated", "taskUpdated", "taskDeleted", "taskStatusUpdated",
    "taskAssigneeUpdated", "taskDueDateUpdated", "taskPriorityUpdated",
    "taskCommentPosted", "taskMoved",
]


async def register_webhook(tenant_id: str, callback_url: str) -> dict:
    """Register a ClickUp webhook on the tenant's team.

    Returns ClickUp's response, which includes the per-webhook `secret` used to sign every
    delivery (X-Signature). That secret MUST be stored — without it the inbound endpoint can't
    tell a real ClickUp delivery from anyone else's POST. See migration 151.
    """
    creds = _creds_or_raise(tenant_id)
    team_id = creds.get("team_id")
    if not team_id:
        return {"error": "No ClickUp team_id stored for this tenant."}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{_BASE}/team/{team_id}/webhook", headers=_headers(creds["token"]),
            json={"endpoint": callback_url, "events": WEBHOOK_EVENTS},
        )
        r.raise_for_status()
        return r.json()


async def add_comment(tenant_id: str, task_id: str, text: str,
                      notify_all: bool = True) -> dict:
    """Post a comment onto a ClickUp task, so a WhatsApp reply lands where the team works."""
    creds = _creds_or_raise(tenant_id)
    if not (task_id and (text or "").strip()):
        return {"error": "Need a task and something to say."}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{_BASE}/task/{task_id}/comment", headers=_headers(creds["token"]),
            json={"comment_text": text.strip(), "notify_all": notify_all},
        )
        r.raise_for_status()
        d = r.json()
    return {"id": d.get("id"), "task_id": task_id}


async def list_comments(tenant_id: str, task_id: str, limit: int = 20) -> list[dict]:
    """Recent comments on a task, newest first — [{id, text, by, at}]."""
    creds = _creds_or_raise(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_BASE}/task/{task_id}/comment",
                             headers=_headers(creds["token"]))
        r.raise_for_status()
        items = r.json().get("comments", []) or []
    out = []
    for c in items[:limit]:
        out.append({
            "id": c.get("id"),
            "text": (c.get("comment_text") or "").strip(),
            "by": (c.get("user") or {}).get("username"),
            "at": c.get("date"),
        })
    return out


async def assign_task(tenant_id: str, task_id: str, assignee_name: str) -> dict:
    """Assign a task to a workspace member by name. Never guesses: if the name doesn't resolve
    to a real member, it says so rather than leaving the task quietly unassigned."""
    creds = _creds_or_raise(tenant_id)
    member = await resolve_assignee(tenant_id, assignee_name)
    if not member:
        members = await list_team_members(tenant_id)
        names = ", ".join(m.get("username") or "" for m in members if m.get("username"))
        return {"error": f"No ClickUp member matching '{assignee_name}'."
                         + (f" Workspace has: {names}" if names else "")}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.put(
            f"{_BASE}/task/{task_id}", headers=_headers(creds["token"]),
            json={"assignees": {"add": [member["id"]], "rem": []}},
        )
        r.raise_for_status()
    return {"task_id": task_id, "assigned_to": member.get("username"), "assignee_id": member["id"]}


# ── ClickUp Docs (v3 API — a structurally separate surface from everything above) ──
# 2026-09-18: everything above talks to ClickUp's v2 REST API (tasks/comments/lists/webhooks).
# Docs live under a different, v3 base URL entirely — tasks and Docs are not the same feature
# in ClickUp's own API design. `authorize_url` (vula/api/clickup.py) requests no explicit OAuth
# `scope` param, so the existing token already carries whatever access level the ClickUp app
# itself was approved for (ClickUp's OAuth model is coarse-grained, not per-scope like Google/
# Microsoft) — in practice this means an already-connected tenant should not need to re-consent
# for Docs access, but every call below still fails closed (empty list / None, never raises past
# this module) so a tenant whose token genuinely lacks Docs access degrades gracefully rather
# than breaking the sync loop for their tasks, which are unrelated.
_BASE_V3 = "https://api.clickup.com/api/v3"


async def list_docs(tenant_id: str) -> list[dict]:
    """List every Doc in the tenant's ClickUp workspace. Returns [] on any failure (not
    connected, no team_id, the token can't see Docs, a transient API error) — never raises,
    since this feeds the best-effort KB sync loop, not a user-facing tool reply."""
    try:
        creds = _creds_or_raise(tenant_id)
    except Exception:
        return []
    team_id = creds.get("team_id")
    if not team_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{_BASE_V3}/workspaces/{team_id}/docs",
                                 headers=_headers(creds["token"]))
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.debug("ClickUp list_docs failed for %s: %s", tenant_id, exc)
        return []
    docs = data if isinstance(data, list) else data.get("docs", [])
    return [{"id": d.get("id"), "name": d.get("name") or "Untitled"} for d in docs if d.get("id")]


async def list_pages(tenant_id: str, doc_id: str) -> list[dict]:
    """List a Doc's pages, WITH content (content_format=text/md pulls it in the same call —
    avoids an extra round trip per page). Returns [] on any failure, same fail-closed shape as
    list_docs."""
    try:
        creds = _creds_or_raise(tenant_id)
    except Exception:
        return []
    team_id = creds.get("team_id")
    if not team_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{_BASE_V3}/workspaces/{team_id}/docs/{doc_id}/pages",
                headers=_headers(creds["token"]), params={"content_format": "text/md"})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.debug("ClickUp list_pages failed for %s/%s: %s", tenant_id, doc_id, exc)
        return []
    pages = data if isinstance(data, list) else data.get("pages", [])
    return [{"id": p.get("id"), "name": p.get("name") or "Untitled page",
            "content": p.get("content") or ""} for p in pages if p.get("id")]


async def get_page_content(tenant_id: str, doc_id: str, page_id: str) -> Optional[str]:
    """One page's content directly — for a caller that already has a specific page id (e.g. a
    tool responding to 'what does the X doc say') rather than sweeping every page via
    list_pages. Returns None on any failure."""
    try:
        creds = _creds_or_raise(tenant_id)
    except Exception:
        return None
    team_id = creds.get("team_id")
    if not team_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{_BASE_V3}/workspaces/{team_id}/docs/{doc_id}/pages/{page_id}",
                headers=_headers(creds["token"]), params={"content_format": "text/md"})
            r.raise_for_status()
            return r.json().get("content") or ""
    except Exception as exc:
        logger.debug("ClickUp get_page_content failed for %s/%s/%s: %s", tenant_id, doc_id, page_id, exc)
        return None


async def process_all_clickup_sync() -> int:
    """Sync every connected tenant's ClickUp lists into their knowledge base (called by the
    scheduled background loop, _clickup_sync_loop in vula/api/server.py — mirrors
    vula.email_imap.sync.process_all_email_sync's shape).

    2026-09-18: sync_tenant_clickup_kb (vula/api/clickup.py) already did the right thing but
    was only ever reachable via its own HTTP route, which nothing called — ClickUp content
    never actually reached the KB in practice. This is what makes it happen on its own."""
    from vula.api.clickup import sync_tenant_clickup_kb
    try:
        rows = (_client().table("vula_clickup_accounts").select("tenant_id")
                .eq("status", "connected").execute().data or [])
    except Exception:
        return 0
    from vula.integrations.sync_status import record_sync_result

    total = 0
    for r in rows:
        tenant_id = r["tenant_id"]
        try:
            res = await sync_tenant_clickup_kb(tenant_id)
            total += res.get("synced_lists", 0) or 0
            if res.get("needs_reconnect"):
                continue          # mark_needs_reconnect already recorded the error
            record_sync_result("vula_clickup_accounts", tenant_id, ok=not res.get("error"),
                               error=res.get("error") or "")
        except Exception as exc:
            logger.warning("ClickUp KB sync failed for %s: %s", tenant_id, exc)
            record_sync_result("vula_clickup_accounts", tenant_id, ok=False, error=str(exc))
    return total


# ── Connection health ─────────────────────────────────────────────────────────
# 2026-10-03 (Ian, DIGG): "why do we struggle with ClickUp?" From 1 Aug no filed document reached
# ClickUp (0 of 129, against 78 of 85 in July) and the assistant's list_tasks got 401 — Vula's
# stored token had lost access to the workspace. Every failure was a log warning, the filing
# reply still read fine, and the KB sync recorded "ok" because it treats a refused list as an
# empty one, so the dashboard said "connected" the whole time. A refused token is now detected,
# the connection marked needs_reconnect (get_tenant_clickup_creds stops handing it out, so the
# callers' existing "ClickUp isn't connected" paths speak up), and the owner is told once a day.

_CHECKED: dict[str, float] = {}
_RECHECK_SECS = 600


async def check_access(tenant_id: str, token: Optional[str] = None,
                       team_id: Optional[str] = None) -> dict:
    """Can this token still reach the tenant's workspace? {"ok": True | False | None, "reason"}.
    False only on ClickUp's own answer (401/403, or the workspace isn't among the token's
    authorised ones); a network error or 5xx is None — unknown, never treated as refused."""
    if token is None:
        creds = get_tenant_clickup_creds(tenant_id)
        if not creds or not creds.get("token"):
            return {"ok": None, "reason": "not connected"}
        token, team_id = creds["token"], team_id or creds.get("team_id")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{_BASE}/team", headers=_headers(token))
    except Exception as exc:
        return {"ok": None, "reason": f"ClickUp unreachable: {type(exc).__name__}"}
    if r.status_code in (401, 403):
        return {"ok": False, "reason": f"ClickUp refused Vula's sign-in ({r.status_code})"}
    if r.status_code >= 400:
        return {"ok": None, "reason": f"ClickUp error {r.status_code}"}
    teams = (r.json() or {}).get("teams") or []
    ids = {str(t.get("id")) for t in teams}
    if team_id and str(team_id) not in ids:
        return {"ok": False, "reason": "Vula's sign-in no longer includes this ClickUp workspace"}
    return {"ok": True, "reason": "", "teams": [{"id": t.get("id"), "name": t.get("name")} for t in teams]}


def mark_needs_reconnect(tenant_id: str, reason: str) -> None:
    from datetime import date
    from vula.clickup.credentials import invalidate
    try:
        _client().table("vula_clickup_accounts").update({
            "status": "needs_reconnect", "last_sync_status": "error",
            "last_sync_error": reason[:500],
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
        }).eq("tenant_id", tenant_id).execute()
    except Exception as exc:
        logger.warning("could not flag ClickUp for reconnect (%s): %s", tenant_id, exc)
    invalidate(tenant_id)
    logger.warning("ClickUp for %s needs reconnecting: %s", tenant_id, reason)
    try:
        import asyncio
        from vula.integrations.notify import notify_team
        msg = ("⚠️ ClickUp needs reconnecting — Vula can no longer reach your workspace "
               f"({reason}). Until then documents are filed in Vula only. Reconnect in the "
               "dashboard under Settings › ClickUp; anything filed meanwhile is sent across "
               "once it's back.")
        asyncio.get_running_loop().create_task(notify_team(
            tenant_id, "integration_alert", msg,
            idem_key=f"clickup-reconnect:{tenant_id}:{date.today().isoformat()}"))
    except Exception as exc:
        logger.debug("ClickUp reconnect notice skipped for %s: %s", tenant_id, exc)


async def verify_or_flag(tenant_id: str) -> Optional[bool]:
    """After a ClickUp call was refused: check the sign-in (at most every 10 minutes per tenant)
    and flag the connection if ClickUp really refuses it. Returns check_access's ok."""
    import time
    now = time.monotonic()
    if now - _CHECKED.get(tenant_id, -1e9) < _RECHECK_SECS:
        return None
    _CHECKED[tenant_id] = now
    res = await check_access(tenant_id)
    if res.get("ok") is False:
        mark_needs_reconnect(tenant_id, res["reason"])
    return res.get("ok")


def looks_refused(exc: BaseException) -> bool:
    """An httpx error ClickUp answered with 401/403/404 — the shape a lost sign-in takes."""
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    if code is None:
        m = re.search(r"\b(401|403|404)\b", str(exc))
        return bool(m)
    return code in (401, 403, 404)


async def refile_missing(tenant_id: str, limit: int = 300) -> dict:
    """Send documents that were filed in Vula but never reached ClickUp (a matched list, no
    ClickUp task) across, oldest first. Run after a reconnect. Stops at the first refusal."""
    from vula.integrations.doc_filing import attach_into_project
    from vula.storage_links import fetch
    rows = (_client().table("vula_filed_documents")
            .select("id,project,clickup_list_id,filename,file_url,mime")
            .eq("tenant_id", tenant_id).eq("status", "filed")
            .not_.is_("clickup_list_id", "null").is_("clickup_task_id", "null")
            .not_.is_("file_url", "null").order("created_at").limit(limit).execute().data or [])
    done = failed = 0
    for r in rows:
        try:
            data = await fetch(r["file_url"])
        except Exception as exc:
            logger.warning("refile: couldn't read %s: %s", r["id"], exc)
            failed += 1
            continue
        att = await attach_into_project(tenant_id, r.get("project"), r["clickup_list_id"],
                                        r.get("filename") or "document", data,
                                        r.get("mime") or "application/octet-stream")
        if not att.get("clickup_task_id"):
            failed += 1
            if await verify_or_flag(tenant_id) is False:
                break
            continue
        try:
            _client().table("vula_filed_documents").update({
                "clickup_task_id": att["clickup_task_id"],
                "clickup_list_id": att.get("clickup_list_id") or r["clickup_list_id"],
            }).eq("id", r["id"]).execute()
            done += 1
        except Exception as exc:
            logger.warning("refile: couldn't record %s: %s", r["id"], exc)
    logger.info("ClickUp refile for %s: %d sent, %d failed of %d", tenant_id, done, failed, len(rows))
    return {"tenant_id": tenant_id, "candidates": len(rows), "sent": done, "failed": failed}
