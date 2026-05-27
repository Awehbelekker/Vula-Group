"""
vula/chat/history.py

Conversation memory for WhatsApp and portal chat.
Stores per-tenant, per-phone message history in SQLite.
History is injected into the LLM prompt so the AI remembers context.
"""
from __future__ import annotations

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config import settings

logger = logging.getLogger(__name__)

DB_PATH = settings.data_dir / "chat_history.db"


@dataclass
class ChatMessage:
    role: str          # "user" | "assistant"
    text: str
    created_at: str    # ISO timestamp
    phone: str = ""
    tenant_id: str = ""


class ChatHistoryDB:
    """SQLite-backed conversation history store."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id   TEXT NOT NULL,
                    phone       TEXT NOT NULL DEFAULT '',
                    role        TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    text        TEXT NOT NULL,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_tenant_phone ON chat_messages(tenant_id, phone)")
            conn.commit()

    def save(self, tenant_id: str, phone: str, role: str, text: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO chat_messages (tenant_id, phone, role, text, created_at) VALUES (?, ?, ?, ?, ?)",
                (tenant_id, phone, role, text[:4000], datetime.utcnow().isoformat()),
            )
            conn.commit()

    def get(self, tenant_id: str, phone: str = "", limit: int = 20) -> List[ChatMessage]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT role, text, created_at, phone, tenant_id
                   FROM chat_messages
                   WHERE tenant_id = ? AND phone = ?
                   ORDER BY id DESC LIMIT ?""",
                (tenant_id, phone, limit),
            ).fetchall()
        return [ChatMessage(role=r[0], text=r[1], created_at=r[2], phone=r[3], tenant_id=r[4]) for r in reversed(rows)]

    def clear(self, tenant_id: str, phone: str = "") -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM chat_messages WHERE tenant_id = ? AND phone = ?",
                (tenant_id, phone),
            )
            conn.commit()
            return cur.rowcount

    def format_for_prompt(self, tenant_id: str, phone: str = "", limit: int = 6) -> str:
        """Return the last N exchanges as a formatted conversation string for prompt injection."""
        msgs = self.get(tenant_id, phone, limit=limit * 2)
        if not msgs:
            return ""
        lines = []
        for m in msgs:
            label = "Client" if m.role == "user" else "Vula AI"
            lines.append(f"{label}: {m.text}")
        return "\n".join(lines)


_db: Optional[ChatHistoryDB] = None


def get_db() -> ChatHistoryDB:
    global _db
    if _db is None:
        _db = ChatHistoryDB()
    return _db
