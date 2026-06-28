"""
vula/integrations/workspace.py — project-scoped chat ("Claude Projects" for Vula).

Builds a project context block (brief + client/team, recent filed docs, finances snapshot,
open to-dos) and runs the existing RAG/skill stack against it, so a thread inside a project
already "knows" that project. Threads + messages persist in vula_project_*.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def build_context(tenant_id: str, project: str) -> str:
    """A compact, high-signal brief of everything Vula knows about this project."""
    parts = [f"You are working inside the project **{project}**. Stay focused on it."]
    db = _client()

    # Per-project custom instructions.
    try:
        b = (db.table("vula_project_briefs").select("brief")
             .eq("tenant_id", tenant_id).eq("project", project).limit(1).execute().data or [])
        if b and b[0].get("brief"):
            parts.append(f"PROJECT BRIEF (follow this):\n{b[0]['brief']}")
    except Exception:
        pass

    # Finances snapshot.
    try:
        from vula.integrations.finances import finance_summary
        fs = finance_summary(tenant_id, project)
        row = next((p for p in fs.get("projects", [])), None)
        if row:
            line = f"Finances: in R{row['in']:,.0f}, out R{row['out']:,.0f}, net R{row['net']:,.0f}"
            if row.get("budget"):
                line += f", budget R{row['budget']:,.0f}, remaining R{row['remaining']:,.0f}"
            parts.append(line)
    except Exception:
        pass

    # Recent filed documents (titles + one-line summaries).
    try:
        docs = (db.table("vula_filed_documents").select("filename,category,summary")
                .eq("tenant_id", tenant_id).eq("project", project)
                .order("created_at", desc=True).limit(8).execute().data or [])
        if docs:
            lines = [f"- {d['filename']} ({d.get('category') or 'doc'}): {(d.get('summary') or '')[:80]}" for d in docs]
            parts.append("Recent documents on file:\n" + "\n".join(lines))
    except Exception:
        pass

    # Open to-dos.
    try:
        tasks = (db.table("vula_project_tasks").select("title")
                 .eq("tenant_id", tenant_id).eq("project", project).eq("status", "open")
                 .order("created_at", desc=True).limit(8).execute().data or [])
        if tasks:
            parts.append("Open to-dos:\n" + "\n".join(f"- {t['title']}" for t in tasks))
    except Exception:
        pass

    return "\n\n".join(parts)


def _thread_history(tenant_id: str, thread_id: str, limit: int = 12) -> str:
    try:
        rows = (_client().table("vula_project_messages").select("role,content")
                .eq("tenant_id", tenant_id).eq("thread_id", thread_id)
                .order("created_at", desc=True).limit(limit).execute().data or [])
    except Exception:
        return ""
    rows.reverse()
    return "\n".join(f"{'User' if r['role'] == 'user' else 'Vula'}: {r['content']}" for r in rows)


async def workspace_chat(tenant_id: str, project: str, thread_id: str, message: str) -> str:
    """Persist the user message, answer with project context + skills, persist the reply."""
    db = _client()
    try:
        db.table("vula_project_messages").insert(
            {"tenant_id": tenant_id, "thread_id": thread_id, "role": "user", "content": message}).execute()
    except Exception as exc:
        logger.debug("thread message save skipped (run migration 030?): %s", exc)

    context = build_context(tenant_id, project)
    history = _thread_history(tenant_id, thread_id)
    full_history = f"{context}\n\n(Conversation so far)\n{history}" if history else context

    try:
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply(tenant_id, message, conversation_history=full_history)
    except Exception as exc:
        logger.error("workspace chat error: %s", exc)
        reply = "I'm having trouble right now. Please try again in a moment."

    try:
        db.table("vula_project_messages").insert(
            {"tenant_id": tenant_id, "thread_id": thread_id, "role": "assistant", "content": reply}).execute()
        db.table("vula_project_threads").update({"updated_at": "now()"}).eq("id", thread_id).execute()
        # Auto-title the thread from the first user message.
        t = (db.table("vula_project_threads").select("title")
             .eq("id", thread_id).limit(1).execute().data or [{}])[0]
        if (t.get("title") or "New chat") == "New chat":
            title = (message[:40] + "…") if len(message) > 40 else message
            db.table("vula_project_threads").update({"title": title}).eq("id", thread_id).execute()
    except Exception:
        pass
    return reply
