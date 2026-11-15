"""Files attached in the dashboard chat (13 Nov 2026).

Ian picked "attach files" as the next step for Ask anything: drop a spreadsheet, PDF, Word
document or photo into the chat and ask about it. The file is READ, not filed: saved under the
business's upload folder, its text extracted with the same DocumentParser every upload uses
(vula/ingestion/pipeline.py), and kept beside it for 24 hours for the person who attached it.
Nothing goes into the knowledge base unless they choose "Keep in our documents" (file_into).

An attachment belongs to one person's own thread (the dashboard chat's session key) — another
login can't use it, like the thread itself (CLAUDE.md: a staff member's thread is private).
The text is never written to the conversation history; only "[Attached: name]" is.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TTL_S = 24 * 3600
MAX_FILES = 3
MAX_CHARS = 60_000          # kept per file
OPEN_CHARS = 40_000         # given to the open chat, per message
BUSINESS_CHARS = 12_000     # given to the business assistant, per message
READABLE = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt", ".md",
            ".png", ".jpg", ".jpeg", ".webp", ".gif"}
_ID = re.compile(r"^[a-f0-9]{32}$")


class AttachmentError(ValueError):
    """A file that can't be attached; the message says why, for the person."""


def _dir(tenant_id: str) -> Path:
    from config import settings
    from vula.uploads import safe_filename
    d = Path(settings.upload_dir) / safe_filename(tenant_id) / "chat"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path(tenant_id: str, att_id: str) -> Path:
    return _dir(tenant_id) / f"{att_id}.json"


def prune(tenant_id: str, now: Optional[float] = None) -> int:
    """Remove this business's chat attachments older than a day (unless kept in documents)."""
    now = now or time.time()
    removed = 0
    try:
        for meta in _dir(tenant_id).glob("*.json"):
            try:
                m = json.loads(meta.read_text())
            except (OSError, ValueError):
                continue
            if now - float(m.get("created_at") or 0) < TTL_S:
                continue
            if not m.get("filed"):
                Path(m.get("path") or "").unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            removed += 1
    except OSError as exc:
        logger.debug("chat attachment prune skipped: %s", exc)
    return removed


async def attach(tenant_id: str, session_key: str, name: str, content: bytes,
                 mime: Optional[str] = None) -> Dict[str, Any]:
    """Save and read one file for this person. Returns its public summary."""
    from config import settings
    from vula.ingestion.pipeline import DocumentParser
    from vula.uploads import safe_upload_path
    ext = Path(name or "").suffix.lower()
    if ext not in READABLE:
        raise AttachmentError(f"{name}: Vula can read PDF, Word, Excel, CSV, text and photos.")
    if not content:
        raise AttachmentError(f"{name} is empty.")
    if len(content) > settings.max_file_mb * 1024 * 1024:
        raise AttachmentError(f"{name} is over {settings.max_file_mb} MB.")
    prune(tenant_id)
    att_id = uuid.uuid4().hex
    path = safe_upload_path(_dir(tenant_id), f"{att_id}-{name}")
    path.write_bytes(content)
    try:
        pages = await DocumentParser().parse(path)
    except Exception as exc:  # noqa: BLE001 — a file we can't read is said so, never a crash
        logger.info("chat attachment unreadable (%s): %s", ext, exc)
        pages = []
    text = "\n\n".join(t for _p, t in pages if t and t.strip()).strip()
    if not text:
        path.unlink(missing_ok=True)
        raise AttachmentError(f"Vula couldn't read any text in {name}.")
    truncated = len(text) > MAX_CHARS
    meta = {"id": att_id, "session_key": session_key, "name": name, "mime": mime, "path": str(path),
            "pages": len(pages), "chars": len(text), "truncated": truncated,
            "text": text[:MAX_CHARS], "created_at": time.time(), "filed": False}
    _meta_path(tenant_id, att_id).write_text(json.dumps(meta))
    return public(meta)


def public(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {k: meta.get(k) for k in ("id", "name", "pages", "chars", "truncated", "filed")}


def get(tenant_id: str, session_key: str, att_id: str) -> Optional[Dict[str, Any]]:
    """This person's attachment, or None: unknown, expired, or someone else's."""
    if not _ID.match(att_id or ""):
        return None
    try:
        meta = json.loads(_meta_path(tenant_id, att_id).read_text())
    except (OSError, ValueError):
        return None
    if meta.get("session_key") != session_key:
        return None
    if time.time() - float(meta.get("created_at") or 0) >= TTL_S and not meta.get("filed"):
        return None
    return meta


def for_message(tenant_id: str, session_key: str, ids: List[str]) -> List[Dict[str, Any]]:
    """The attachments a message refers to, all this person's own — KeyError naming the first
    that isn't (unknown, expired or someone else's)."""
    out = []
    for att_id in list(dict.fromkeys(ids or []))[:MAX_FILES]:
        meta = get(tenant_id, session_key, att_id)
        if meta is None:
            raise KeyError(att_id)
        out.append(meta)
    return out


def block(attachments: List[Dict[str, Any]], budget: int) -> str:
    """The attached files' text for a prompt, fenced as untrusted content, sharing `budget`
    characters between them; each says when it was cut short."""
    from core.prompt_safety import fence
    if not attachments:
        return ""
    share = max(2000, budget // len(attachments))
    parts = []
    for a in attachments:
        text = a.get("text") or ""
        cut = len(text) > share or a.get("truncated")
        parts.append(fence(f"ATTACHED_FILE: {a.get('name')}", text[:share]
                           + ("\n[… the rest of this file isn't shown]" if cut else "")))
    return "\n".join(parts)


def note(message: str, attachments: List[Dict[str, Any]]) -> str:
    """The user turn as it's kept in history: the message and the file names, never their text."""
    if not attachments:
        return message
    return f"{message}\n[Attached: {', '.join(str(a.get('name')) for a in attachments)}]"


async def file_into(tenant_id: str, session_key: str, att_id: str) -> Optional[Dict[str, Any]]:
    """"Keep in our documents": the file goes into the knowledge base and is filed like any
    upload (profile_from_docs.file_into_kb), and is kept past the 24 hours. None when it isn't
    this person's attachment."""
    meta = get(tenant_id, session_key, att_id)
    if meta is None:
        return None
    if not meta.get("filed"):
        from vula.commerce.profile_from_docs import file_into_kb
        await file_into_kb(tenant_id, Path(meta["path"]), meta.get("mime"))
        meta["filed"] = True
        _meta_path(tenant_id, att_id).write_text(json.dumps(meta))
    return public(meta)
