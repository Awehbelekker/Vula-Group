"""Each stored piece of a document says which document it is (2026-10-25).

The retrieval diagnostic's "ranked too low" and "not found" misses were mostly invoices,
statements and receipts: a piece of an invoice reads "Qty 4 Hake fillet 2kg R1 280.00 …" and
says nothing about whose invoice it is, so "the Atlantis invoice" can't find it. So each piece is
embedded as

    Document: Invoice 00097750 from SOLID CAPE (PTY) LTD, 19 Oct 2026 — 00097750
    <the piece's own text>

The label is built by code from the filed record (vula_filed_documents: kind, number, party,
date — kb_records.describe) or, for a document that was never filed, its filed title or file
name. Nothing is written by a model, so a label can't name something the document doesn't.

Only the vector changes. The stored `text` is untouched (what the model and the literal search
read), and the label goes into the payload as `ctx_label`, with `ctx_v` marking the pieces done.
contextualize() is resumable and incremental: run nightly, it picks up only pieces without the
current ctx_v (new uploads, or a run cut short). revert=True re-embeds the plain text and clears
the marker — the way back if a measurement says the labels hurt.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

CTX_VERSION = 1
BATCH = 16


def _clean_name(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", (filename or "").strip())
    return re.sub(r"\s+", " ", re.sub(r"[_]+", " ", stem)).strip()


def label_for(payload: Dict[str, Any], filed: Optional[Dict[str, Any]]) -> str:
    """What this piece's document is, in words a question would use."""
    name = _clean_name(payload.get("filename") or "")
    if filed:
        from vula import kb_records
        head = kb_records.describe(filed)
        # A money document's kind, party and date are in `head`; its original file name often
        # carries the number. Any other document's filed name is its content title.
        money = filed.get("category") in kb_records.MONEY_CATEGORIES
        extra = name if money else (_clean_name(filed.get("filename") or "") or name)
        extra = re.sub(r"\s*\b20\d{6}-\d{4}\b", "", extra).strip(" -")
        return f"{head} — {extra}" if extra and extra.lower() not in head.lower() else head
    return name or "a document"


def embed_text(label: str, text: str) -> str:
    return f"Document: {label}\n\n{text}"


def _filed_rows(tenant_id: str, doc_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    from vula.commerce import service
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(doc_ids), 200):
        rows = (service._client().table("vula_filed_documents")
                .select("doc_id,filename,category,fields,created_at")
                .eq("tenant_id", tenant_id).in_("doc_id", doc_ids[i:i + 200]).execute().data or [])
        for r in rows:
            out.setdefault(str(r["doc_id"]), r)
    return out


async def _scroll(client: httpx.AsyncClient, base: str, name: str) -> List[Dict[str, Any]]:
    pts, offset = [], None
    while True:
        body: Dict[str, Any] = {"limit": 256, "with_vector": False,
                                "with_payload": ["doc_id", "filename", "text", "ctx_v"]}
        if offset is not None:
            body["offset"] = offset
        r = await client.post(f"{base}/collections/{name}/points/scroll", json=body)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        res = r.json().get("result", {})
        pts += res.get("points", [])
        offset = res.get("next_page_offset")
        if offset is None:
            return pts


async def contextualize(tenant_id: str, revert: bool = False, max_points: Optional[int] = None,
                        dry_run: bool = False, pause_s: float = 0.2) -> Dict[str, Any]:
    """Re-embed this tenant's pieces with their document label (or, revert=True, without it).
    Returns counts; stops early (resumable) after `max_points` or on repeated embed failures."""
    from vula.ingestion.pipeline import EmbeddingEngine, QdrantStore
    store, embedder = QdrantStore(), EmbeddingEngine()
    name = store._collection_name(tenant_id)
    out: Dict[str, Any] = {"tenant": tenant_id, "revert": revert, "done": 0, "skipped": 0,
                           "failed": 0, "todo": 0, "examples": []}
    async with httpx.AsyncClient(timeout=60.0, headers=store._headers()) as client:
        pts = await _scroll(client, store.base, name)
        todo = [p for p in pts if (p.get("payload") or {}).get("text")
                and (((p.get("payload") or {}).get("ctx_v") == CTX_VERSION) == revert)]
        out["points"], out["todo"] = len(pts), len(todo)
        if max_points is not None:
            todo = todo[:max_points]
        filed = await asyncio.to_thread(
            _filed_rows, tenant_id,
            sorted({str((p.get("payload") or {}).get("doc_id")) for p in todo
                    if (p.get("payload") or {}).get("doc_id")}))
        labels = {p["id"]: label_for(p["payload"], filed.get(str(p["payload"].get("doc_id"))))
                  for p in todo}
        seen_docs = set()
        for p in todo:
            did = str(p["payload"].get("doc_id"))
            if did not in seen_docs and len(out["examples"]) < 8:
                seen_docs.add(did)
                out["examples"].append(labels[p["id"]])
        if dry_run:
            return out
        failures_in_a_row = 0
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            texts = [p["payload"]["text"] if revert else embed_text(labels[p["id"]], p["payload"]["text"])
                     for p in batch]
            try:
                vecs = await embedder.embed_batch(texts)
            except Exception as exc:  # noqa: BLE001 — the local box can drop out mid-run
                out["failed"] += len(batch)
                failures_in_a_row += 1
                logger.warning("contextualize %s: embed batch failed (%s)", tenant_id, type(exc).__name__)
                if failures_in_a_row >= 3:
                    out["stopped"] = "embedding unavailable"
                    break
                await asyncio.sleep(10)
                continue
            failures_in_a_row = 0
            good = [(p, v) for p, v in zip(batch, vecs) if v]
            out["skipped"] += len(batch) - len(good)
            if not good:
                continue
            r = await client.put(f"{store.base}/collections/{name}/points/vectors",
                                 params={"wait": "true"},
                                 json={"points": [{"id": p["id"], "vector": v} for p, v in good]})
            r.raise_for_status()
            ids = [p["id"] for p, _ in good]
            if revert:
                r = await client.post(f"{store.base}/collections/{name}/points/payload/delete",
                                      params={"wait": "true"},
                                      json={"keys": ["ctx_v", "ctx_label", "ctx_at"], "points": ids})
            else:
                # The label differs per document: one payload call per label in the batch.
                groups: Dict[str, List[Any]] = {}
                for p, _ in good:
                    groups.setdefault(labels[p["id"]], []).append(p["id"])
                at = datetime.now(timezone.utc).isoformat()
                for label, gids in groups.items():
                    r = await client.post(f"{store.base}/collections/{name}/points/payload",
                                          params={"wait": "true"},
                                          json={"payload": {"ctx_v": CTX_VERSION, "ctx_label": label[:300],
                                                            "ctx_at": at}, "points": gids})
                    r.raise_for_status()
            r.raise_for_status()
            out["done"] += len(good)
            await asyncio.sleep(pause_s)
    return out


async def run(tenants: List[str], revert: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    """Every tenant in turn; one tenant's failure doesn't stop the next."""
    by: Dict[str, Any] = {}
    for t in tenants:
        try:
            by[t] = await contextualize(t, revert=revert, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            by[t] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        logger.info("contextualize %s: %s", t, {k: v for k, v in by[t].items() if k != "examples"})
    return {"by_tenant": by,
            "summary": {t: {k: v.get(k) for k in ("points", "todo", "done", "failed", "error", "stopped")
                            if v.get(k) is not None} for t, v in by.items()}}


async def forget_labels(tenant_id: str, doc_id: str) -> None:
    """Clear one document's label markers so the next night relabels it (its details changed)."""
    from vula.ingestion.pipeline import QdrantStore
    store = QdrantStore()
    name = store._collection_name(tenant_id)
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=store._headers()) as client:
            await client.post(f"{store.base}/collections/{name}/points/payload/delete",
                              params={"wait": "true"},
                              json={"keys": ["ctx_v"],
                                    "filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}})
    except Exception as exc:  # noqa: BLE001
        logger.debug("forget_labels skipped for %s: %s", doc_id, exc)
