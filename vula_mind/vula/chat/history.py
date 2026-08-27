"""
vula/chat/history.py

Conversation memory for WhatsApp and portal chat. Per-tenant, per-phone history in
Supabase (vula_chat_messages) so it is DURABLE across deploys (it used to be SQLite on
Railway's ephemeral disk, which wiped every restart). Same interface as before — callers
unchanged. History is injected into the LLM prompt so the AI remembers context.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)
_TABLE = "vula_chat_messages"


@dataclass
class ChatMessage:
    role: str          # "user" | "assistant"
    text: str
    created_at: str    # ISO timestamp
    phone: str = ""
    tenant_id: str = ""


def _client():
    from config import settings
    from supabase import create_client
    key = settings.supabase_service_role_key or settings.supabase_service_key
    return create_client(settings.supabase_url, key)


class ChatHistoryDB:
    """Supabase-backed conversation history store (durable, tenant-isolated)."""

    def save(self, tenant_id: str, phone: str, role: str, text: str) -> None:
        try:
            _client().table(_TABLE).insert({
                "tenant_id": tenant_id, "phone": phone or "", "role": role,
                "text": (text or "")[:4000],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as exc:  # never break a reply because history failed
            logger.debug("chat save skipped (run migration 034?): %s", exc)

    def get(self, tenant_id: str, phone: str = "", limit: int = 20,
            max_age_hours: Optional[float] = 24) -> List[ChatMessage]:
        try:
            q = (_client().table(_TABLE)
                 .select("role,text,created_at,phone,tenant_id")
                 .eq("tenant_id", tenant_id).eq("phone", phone or ""))
            if max_age_hours is not None:
                from core.time_fmt import cutoff_iso
                q = q.gte("created_at", cutoff_iso(max_age_hours))
            rows = q.order("created_at", desc=True).limit(limit).execute().data or []
        except Exception as exc:
            logger.debug("chat get skipped: %s", exc)
            return []
        rows.reverse()
        return [ChatMessage(role=r["role"], text=r["text"], created_at=r.get("created_at", ""),
                            phone=r.get("phone", ""), tenant_id=r.get("tenant_id", "")) for r in rows]

    def clear(self, tenant_id: str, phone: str = "") -> int:
        try:
            res = (_client().table(_TABLE).delete()
                   .eq("tenant_id", tenant_id).eq("phone", phone or "").execute())
            return len(res.data or [])
        except Exception:
            return 0

    def format_for_prompt(self, tenant_id: str, phone: str = "", limit: int = 6,
                          max_age_hours: Optional[float] = 24) -> str:
        """Last N exchanges as a formatted conversation string for prompt injection. Each line
        is tagged with its actual age (2026-08-27) — same fix as commerce/service.py's
        format_history, same real incident (a stale message resurfacing hours later with no
        way for the model to tell it wasn't fresh)."""
        msgs = self.get(tenant_id, phone, limit=limit * 2, max_age_hours=max_age_hours)
        if not msgs:
            return ""
        from core.time_fmt import relative_age_label
        lines = []
        for m in msgs:
            age = relative_age_label(m.created_at) if m.created_at else ""
            age_tag = f" ({age})" if age else ""
            lines.append(f"{'Client' if m.role == 'user' else 'Vula AI'}{age_tag}: {m.text}")
        return "\n".join(lines)


_db: Optional[ChatHistoryDB] = None


def get_db() -> ChatHistoryDB:
    global _db
    if _db is None:
        _db = ChatHistoryDB()
    return _db
