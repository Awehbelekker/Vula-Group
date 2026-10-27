"""
vula/app_errors.py — crashes in the dashboard on people's devices (2026-10-27).

The dashboard reports its own uncaught errors (vula_dashboard/src/lib/appErrors.js); they're
kept here, counted — one row per distinct error per build per business (migration 230) — and
listed in Master › Health, so a screen breaking on a customer's phone is seen without anyone
having to tell Ian.

POPIA: nothing a customer typed or a business's data. Before anything is stored the message is
scrubbed (emails, numbers of 4+ digits, ids, URL queries), stack frames are kept as file:line
only, and the browser is reduced to "Chrome 141 · Android". No user, no IP.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "vula_app_errors"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_LONG_ID = re.compile(r"\b(?=[A-Za-z0-9_-]{20,}\b)(?=[^\s]*\d)[A-Za-z0-9_-]+\b")   # tokens, keys
_DIGITS = re.compile(r"\d[\d\s-]{3,}\d|\d{4,}")
_URL = re.compile(r"(https?://[^\s?#'\"]+)[?#][^\s'\"]*")
_FRAME = re.compile(r"((?:https?://[^\s()]+)?/(?:assets|src)/[^\s():]+):(\d+)(?::(\d+))?")
_PAGE = re.compile(r"^[a-z0-9#/_-]{0,60}$")


def scrub(text: str, limit: int = 300) -> str:
    """The message with anything that could be a person's or a business's data taken out."""
    t = (text or "").replace("\n", " ").strip()
    t = _URL.sub(r"\1", t)
    t = _EMAIL.sub("[email]", t)
    t = _UUID.sub("[id]", t)
    t = _LONG_ID.sub("[id]", t)
    t = _DIGITS.sub("[n]", t)
    return re.sub(r"\s+", " ", t)[:limit]


def frames(stack: str, keep: int = 5) -> str:
    """The first few frames from Vula's own files, as file:line — never anything else."""
    out = []
    for m in _FRAME.finditer(stack or ""):
        path = re.sub(r"^https?://[^/]+", "", m.group(1)).split("?")[0]
        out.append(f"{path}:{m.group(2)}")
        if len(out) >= keep:
            break
    return "\n".join(out)


def browser(ua: str) -> str:
    """'Chrome 141 · Android' — the browser and system, nothing that identifies a device."""
    ua = ua or ""
    system = next((name for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                          ("Windows", "Windows"), ("Mac OS", "Mac"), ("Linux", "Linux"))
                   if key in ua), "other")
    for rx, name in ((r"Edg/(\d+)", "Edge"), (r"SamsungBrowser/(\d+)", "Samsung"), (r"CriOS/(\d+)", "Chrome"),
                     (r"Chrome/(\d+)", "Chrome"), (r"FxiOS/(\d+)", "Firefox"), (r"Firefox/(\d+)", "Firefox"),
                     (r"Version/(\d+)[\d.]* (?:Mobile/\S+ )?Safari", "Safari")):
        m = re.search(rx, ua)
        if m:
            return f"{name} {m.group(1)} · {system}"
    return f"other · {system}"


def _db():
    from vula.commerce.service import _client
    return _client()


def record(*, message: str, stack: str = "", build: str = "", page: str = "", ua: str = "",
           tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Count one occurrence. Returns the stored row (scrubbed), or None if it couldn't be kept.
    Never raises: reporting an error must never become another one."""
    msg = scrub(message)
    if not msg:
        return None
    fr = frames(stack)
    build = re.sub(r"[^\w.-]", "", build or "")[:24] or "unknown"
    page = page if _PAGE.match(page or "") else ""
    top = fr.split("\n", 1)[0]
    fp = hashlib.sha256(f"{tenant_id or ''}|{build}|{msg}|{top}".encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).isoformat()
    try:
        db = _db()
        hit = db.table(TABLE).select("id,count").eq("fingerprint", fp).limit(1).execute().data or []
        if hit:
            row = {"count": int(hit[0].get("count") or 0) + 1, "last_seen": now, "page": page or None,
                   "resolved_at": None}
            db.table(TABLE).update(row).eq("id", hit[0]["id"]).execute()
            return {"id": hit[0]["id"], "message": msg, **row}
        row = {"fingerprint": fp, "tenant_id": tenant_id, "build": build, "page": page or None,
               "message": msg, "frames": fr or None, "browser": browser(ua), "count": 1,
               "first_seen": now, "last_seen": now}
        db.table(TABLE).insert(row).execute()
        return row
    except Exception as exc:
        log.warning("app error not recorded: %s", exc)
        return None


def recent(days: int = 14, include_resolved: bool = False, limit: int = 200) -> List[Dict[str, Any]]:
    """For Master: the errors seen in the last `days`, most recent first."""
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))).isoformat()
    q = (_db().table(TABLE).select("id,tenant_id,build,page,message,frames,browser,count,first_seen,last_seen,resolved_at")
         .gte("last_seen", since))
    if not include_resolved:
        q = q.is_("resolved_at", "null")
    return q.order("last_seen", desc=True).limit(max(1, min(limit, 500))).execute().data or []


def resolve(error_id: str) -> bool:
    """Master marked it fixed. It comes back (unresolved) if it happens again."""
    rows = (_db().table(TABLE).update({"resolved_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", error_id).execute().data or [])
    return bool(rows)
