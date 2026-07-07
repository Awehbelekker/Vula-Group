"""
vula/clickup/service.py

Thin async wrapper over the ClickUp REST API v2, scoped per tenant. All calls
resolve the tenant's stored token + list ids via credentials.get_tenant_clickup_creds.

ClickUp API: base https://api.clickup.com/api/v2, header Authorization: <token>.
Dates are Unix epoch milliseconds.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from vula.clickup.credentials import get_tenant_clickup_creds, default_list_id

logger = logging.getLogger(__name__)

_BASE = "https://api.clickup.com/api/v2"


class ClickUpNotConnected(Exception):
    """Raised when a tenant has no connected ClickUp account."""


def _headers(token: str) -> dict:
    return {"Authorization": token, "Content-Type": "application/json"}


def _to_epoch_ms(value: Optional[str]) -> Optional[int]:
    """Parse an ISO date/datetime (or 'YYYY-MM-DD') into epoch ms, else None."""
    if not value:
        return None
    s = str(value).strip()
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


async def find_task(tenant_id: str, query: str, list_id: Optional[str] = None) -> Optional[dict]:
    """Find the first task whose title contains `query` (case-insensitive)."""
    rows = await list_tasks(tenant_id, list_id=list_id, limit=50)
    if not isinstance(rows, list):
        return None
    q = (query or "").lower().strip()
    return next((t for t in rows if q and q in (t.get("title") or "").lower()), None)


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
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{_BASE}/task/{task_id}/attachment",
            headers={"Authorization": token},
            files={"attachment": (filename, data, content_type)},
        )
        r.raise_for_status()
        d = r.json()
    return {"task_id": task_id, "attachment_id": d.get("id")}


async def register_webhook(tenant_id: str, callback_url: str) -> dict:
    """Register a ClickUp webhook on the tenant's team for task status changes."""
    creds = _creds_or_raise(tenant_id)
    team_id = creds.get("team_id")
    if not team_id:
        return {"error": "No ClickUp team_id stored for this tenant."}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{_BASE}/team/{team_id}/webhook", headers=_headers(creds["token"]),
            json={"endpoint": callback_url, "events": ["taskStatusUpdated", "taskUpdated"]},
        )
        r.raise_for_status()
        return r.json()
