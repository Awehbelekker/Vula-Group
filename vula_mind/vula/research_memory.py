"""What Vula found when it researched something, kept so it isn't researched again (13 Nov 2026).

Ian asked how Vula can learn from its conversations so there's less research. Research mode
(vula/research.py) used to throw each answer away after showing it, so the same question the next
day — or a colleague's — ran the searches and page reads again. Now a finished, cited answer is kept
(vula_research_notes, migration 269) and reused for a similar question:

  * Kept for the business's whole team unless the person chose "keep private" (Ian's choice:
    team, with an opt-out). Their chat with Vula itself stays private — only the finding is shared.
  * Reused for 30 days when it's about prices, stock or suppliers, 6 months otherwise (Ian's
    choice: by topic), always shown with its date and a "Research again" button.
  * Only a clean answer is kept: at least one cited web source, and no price it couldn't find in
    its sources (research.finish's "unchecked").
  * Found the way approved learned answers are (vula/escalation.py): the question is indexed in the
    business's knowledge base only to find it; the row here is the truth, re-read every time; and
    the same distinguishing-word guard stops a "12' bag" note answering a "9'6 bag" question.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE = "vula_research_notes"
DOC_PREFIX = "research_note_"
SOURCE_TYPE = "research_note"
DAYS = {"prices": 30, "general": 180}
PRUNE_AFTER_DAYS = 30

_PRICES = re.compile(r"\b(price|prices|pricing|cost|costs|costing|quote|quotes|quotation|rate|rates|tariff|"
                     r"stock|in stock|supplier|suppliers|stockist|availability|available|cheapest|"
                     r"how much|per (?:m2|m²|m3|kg|unit|litre|bag|ton|hour|day))\b|\bR\s?\d", re.I)
# Words that differ between two phrasings of the same research question without changing it.
_SAME_QUESTION = set("""cost costs price prices pricing much cheapest cheap best good current latest today
please find research look tell compare comparison options option what which where when does south
africa african local locally available buy buying sell selling stock suppliers supplier there their
about know some give""".split())


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def kind(question: str, answer: str = "") -> str:
    """'prices' (reused 30 days) or 'general' (180 days)."""
    return "prices" if _PRICES.search(f"{question or ''} {answer or ''}") else "general"


def clean(result: Dict[str, Any]) -> bool:
    """Worth keeping: it cites at least one web page and states no price its sources don't."""
    return bool(result.get("sources")) and not result.get("unchecked") and not result.get("reused")


def _same_question(a: str, b: str) -> bool:
    from vula.escalation import _distinguishing_tokens, _tokens
    qa, qb = _tokens(a), _tokens(b)
    if not qa or not qb:
        return False
    return not (_distinguishing_tokens(qa, qb) - _SAME_QUESTION)


async def save(tenant_id: str, person_key: str, question: str, result: Dict[str, Any],
               shared: bool = True, replaces: Optional[str] = None) -> Optional[str]:
    """Keep a finished research answer. Returns its id, or None when it isn't kept."""
    if not tenant_id or not question.strip() or not clean(result):
        return None
    k = kind(question, result.get("answer", ""))
    now = _now()
    row = {"tenant_id": tenant_id, "question": question.strip()[:1000], "answer": result["answer"],
           "sources": result.get("sources") or [], "documents": result.get("documents") or [],
           "searched": result.get("searched") or [], "kind": k, "shared": bool(shared),
           "created_by": person_key or None, "created_at": now.isoformat(),
           "expires_at": (now + timedelta(days=DAYS[k])).isoformat()}
    try:
        got = _client().table(TABLE).insert(row).execute().data or []
        note_id = got[0]["id"] if got and got[0].get("id") else None
    except Exception as exc:  # noqa: BLE001 — the answer was still given; it just isn't kept
        logger.warning("research note not saved (tenant=%s): %s", tenant_id, exc)
        return None
    if not note_id:
        return None
    if replaces:
        try:
            (_client().table(TABLE).update({"superseded_by": note_id})
             .eq("tenant_id", tenant_id).eq("id", replaces).execute())
        except Exception as exc:  # noqa: BLE001
            logger.debug("research note %s not marked superseded: %s", replaces, exc)
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        doc_id = DOC_PREFIX + note_id
        await VulaIngestionPipeline(tenant_id=tenant_id).ingest_text(
            content=row["question"], filename=f"{doc_id}.txt", doc_id=doc_id,
            source_type=SOURCE_TYPE, audience="staff")
    except Exception as exc:  # noqa: BLE001 — not findable by meaning yet; the row is kept
        logger.debug("research note %s not indexed: %s", note_id, exc)
    prune(tenant_id)
    return note_id


def _usable(row: Dict[str, Any], tenant_id: str, person_key: str) -> bool:
    if not isinstance(row, dict) or row.get("tenant_id") != tenant_id or row.get("superseded_by"):
        return False
    if str(row.get("expires_at") or "") <= _now().isoformat():
        return False
    return bool(row.get("shared")) or (bool(person_key) and row.get("created_by") == person_key)


def _get(tenant_id: str, note_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", note_id)
            .limit(1).execute().data or [])
    return rows[0] if rows else None


async def _candidates(tenant_id: str, question: str) -> List[str]:
    """Note ids whose question is close in meaning (best first)."""
    from vula.escalation import _SEMANTIC_SCORE_THRESHOLD
    from vula.ingestion.pipeline import VulaIngestionPipeline
    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    vec = await pipeline.embedder.embed(question)
    hits = await pipeline.store.search(tenant_id, vec, limit=5, score_threshold=_SEMANTIC_SCORE_THRESHOLD,
                                       source_type=SOURCE_TYPE)
    out = []
    for h in hits or []:
        doc_id = str(h.get("doc_id") or "")
        if doc_id.startswith(DOC_PREFIX) and doc_id[len(DOC_PREFIX):] not in out:
            out.append(doc_id[len(DOC_PREFIX):])
    return out


def _by_words(tenant_id: str, question: str) -> List[Dict[str, Any]]:
    """Fallback when the index can't be searched: this business's live notes, newest first."""
    rows = (_client().table(TABLE).select("*").eq("tenant_id", tenant_id)
            .gt("expires_at", _now().isoformat()).order("created_at", desc=True).limit(50).execute().data or [])
    return rows


async def find(tenant_id: str, person_key: str, question: str, count: bool = True) -> Optional[Dict[str, Any]]:
    """The newest usable note on the same question, or None. Counts the reuse (unless `count`
    is False: looking for the note "Research again" replaces)."""
    if not tenant_id or not (question or "").strip():
        return None
    rows: List[Dict[str, Any]] = []
    try:
        for note_id in await _candidates(tenant_id, question):
            row = _get(tenant_id, note_id)
            if row:
                rows.append(row)
    except Exception as exc:  # noqa: BLE001 — fall back to the plain word match
        logger.debug("research note search fell back to words: %s", exc)
        try:
            rows = _by_words(tenant_id, question)
        except Exception as exc2:  # noqa: BLE001
            logger.debug("research notes unreadable: %s", exc2)
            return None
    for row in rows:
        if _usable(row, tenant_id, person_key) and _same_question(question, row.get("question") or ""):
            if not count:
                return row
            try:
                (_client().table(TABLE).update({"reused_count": int(row.get("reused_count") or 0) + 1,
                                                "last_reused_at": _now().isoformat()})
                 .eq("tenant_id", tenant_id).eq("id", row["id"]).execute())
            except Exception:  # noqa: BLE001
                pass
            return row
    return None


def describe(row: Dict[str, Any], person_key: str = "") -> Dict[str, Any]:
    """What the dashboard shows about a reused note."""
    return {"id": row.get("id"), "date": _date(row.get("created_at")), "kind": row.get("kind"),
            "shared": bool(row.get("shared")), "mine": bool(person_key) and row.get("created_by") == person_key}


def _date(ts: Any) -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts or "")[:10]
    return f"{dt.day} {dt:%b %Y}"


def block(row: Dict[str, Any]) -> str:
    """A note for a prompt (Ask anything without the research toggle), fenced and dated."""
    from urllib.parse import urlparse
    from core.prompt_safety import fence
    hosts = ", ".join(dict.fromkeys(urlparse(s.get("url") or "").netloc for s in row.get("sources") or []))
    return fence("EARLIER_RESEARCH", f"Researched on {_date(row.get('created_at'))} (sources: {hosts}).\n"
                 f"Question then: {row.get('question')}\nWhat was found:\n{row.get('answer')}")


def set_shared(tenant_id: str, person_key: str, note_id: str, shared: bool) -> Optional[Dict[str, Any]]:
    """Share a note with the team or keep it private. Only the person who asked can. None otherwise."""
    if not person_key:
        return None
    row = _get(tenant_id, note_id)
    if not row or row.get("created_by") != person_key:
        return None
    (_client().table(TABLE).update({"shared": bool(shared)})
     .eq("tenant_id", tenant_id).eq("id", note_id).execute())
    return {**describe({**row, "shared": bool(shared)}, person_key)}


def prune(tenant_id: str) -> None:
    """Drop notes that expired more than a month ago."""
    try:
        cutoff = (_now() - timedelta(days=PRUNE_AFTER_DAYS)).isoformat()
        (_client().table(TABLE).delete().eq("tenant_id", tenant_id).lt("expires_at", cutoff).execute())
    except Exception as exc:  # noqa: BLE001
        logger.debug("research notes prune skipped: %s", exc)


def erase_for(tenant_id: str, person_key: str) -> int:
    """Delete this person's notes ("delete my data"). Returns how many."""
    if not tenant_id or not person_key:
        return 0
    rows = (_client().table(TABLE).delete().eq("tenant_id", tenant_id).eq("created_by", person_key)
            .execute().data or [])
    for r in rows:
        try:
            import asyncio
            from vula.ingestion.pipeline import VulaIngestionPipeline
            coro = VulaIngestionPipeline(tenant_id=tenant_id).store.delete_document(
                tenant_id, DOC_PREFIX + str(r.get("id")))
            try:
                asyncio.get_running_loop().create_task(coro)
            except RuntimeError:
                asyncio.run(coro)
        except Exception as exc:  # noqa: BLE001 — the row (the truth) is gone; the index entry finds nothing
            logger.debug("research note index entry not removed: %s", exc)
    return len(rows)


def export_for(tenant_id: str, person_key: str) -> List[Dict[str, Any]]:
    """This person's notes, for "export my data": the question and when."""
    if not tenant_id or not person_key:
        return []
    rows = (_client().table(TABLE).select("question,created_at").eq("tenant_id", tenant_id)
            .eq("created_by", person_key).order("created_at").execute().data or [])
    return [{"question": r.get("question"), "date": str(r.get("created_at") or "")[:10]} for r in rows]
