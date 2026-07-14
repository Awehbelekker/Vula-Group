"""
vula/commerce/labour.py — casual labour register + bank-payment recognition.

Day/week workers who aren't on payroll and issue no invoice: load a worker once (name, ID, bank
account, rate, default project) and Vula recognises their payments straight off the bank statement,
files them as casual labour (no VAT, deductible), links them to the worker + project (job costing),
and remembers the mapping.

Recognition mirrors bank_rec._match_invoice: bank account is the strongest signal, else the worker's
name in the transaction description/reference. Ambiguous → left for review (never guess).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _now():
    from vula.commerce import service
    return service._now()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _tok(s: str) -> set:
    return set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))


# ── Worker register ───────────────────────────────────────────────────────────

def list_workers(tenant_id: str, active_only: bool = True) -> List[dict]:
    q = _client().table("commerce_workers").select("*").eq("tenant_id", tenant_id)
    if active_only:
        q = q.eq("active", True)
    try:
        return q.order("name").execute().data or []
    except Exception as exc:
        log.debug("list_workers skipped (run migration 059?): %s", exc)
        return []


def upsert_worker(tenant_id: str, body: Dict[str, Any]) -> dict:
    row = {
        "tenant_id": tenant_id,
        "name": (body.get("name") or "").strip(),
        "id_number": (body.get("id_number") or "").strip() or None,
        "bank_account": _digits(body.get("bank_account") or "") or None,
        "phone": body.get("phone"),
        "rate_cents": int(round(float(body.get("rate_rands") or 0) * 100)) if body.get("rate_rands") else body.get("rate_cents"),
        "rate_period": body.get("rate_period") or "daily",
        "type": body.get("type") or "casual",
        "default_project": body.get("default_project") or None,
        "active": bool(body.get("active", True)),
        "updated_at": _now(),
    }
    if not row["name"]:
        raise ValueError("worker name is required")
    db = _client()
    if body.get("id"):
        db.table("commerce_workers").update(row).eq("tenant_id", tenant_id).eq("id", body["id"]).execute()
        row["id"] = body["id"]
        return row
    row["id"] = str(uuid4())
    res = db.table("commerce_workers").insert(row).execute()
    return (res.data or [row])[0]


def delete_worker(tenant_id: str, worker_id: str) -> None:
    _client().table("commerce_workers").update({"active": False, "updated_at": _now()}) \
        .eq("tenant_id", tenant_id).eq("id", worker_id).execute()


# ── Matching a bank debit to a worker ─────────────────────────────────────────

def match_worker(txn: dict, workers: List[dict]) -> Optional[dict]:
    """Match an out-going payment to a worker. Bank account (if present) is definitive; else the
    worker's name appearing in the description/reference. Ambiguous name-only matches are skipped."""
    if txn.get("direction") != "out" or not workers:
        return None
    blob = f"{txn.get('description') or ''} {txn.get('reference') or ''}"
    blob_digits = _digits(blob)
    blob_toks = _tok(blob)
    # 1) Bank account in the line → definitive.
    for w in workers:
        acc = _digits(w.get("bank_account") or "")
        if len(acc) >= 6 and acc in blob_digits:
            return w
    # 2) Name match — require the surname (or a distinctive name token) to appear.
    best, best_hits = None, 0
    for w in workers:
        name_toks = {t for t in _tok(w.get("name")) if len(t) >= 3}
        hits = len(name_toks & blob_toks)
        if hits > best_hits:
            best, best_hits = w, hits
    # Need a real name hit; if several workers tie on a single common token, treat as ambiguous.
    if best and best_hits >= 1:
        ties = [w for w in workers if len(_tok(w.get("name")) & blob_toks) == best_hits]
        if best_hits >= 2 or len(ties) == 1:
            return best
    return None


# ── Labour report (per worker / project) ──────────────────────────────────────

def labour_report(tenant_id: str, since: str = None, until: str = None,
                  project: str = None) -> dict:
    db = _client()
    workers = {w["id"]: w for w in list_workers(tenant_id, active_only=False)}
    q = (db.table("commerce_bank_transactions")
         .select("txn_date,amount_cents,description,worker_id,project")
         .eq("tenant_id", tenant_id).not_.is_("worker_id", "null"))
    if since:
        q = q.gte("txn_date", since)
    if until:
        q = q.lte("txn_date", until)
    if project:
        q = q.eq("project", project)
    try:
        rows = q.order("txn_date", desc=True).limit(5000).execute().data or []
    except Exception as exc:
        log.debug("labour_report skipped (run migration 059?): %s", exc)
        rows = []
    by_worker: Dict[str, dict] = {}
    by_project: Dict[str, int] = {}
    for r in rows:
        w = workers.get(r.get("worker_id")) or {}
        wid = r.get("worker_id")
        agg = by_worker.setdefault(wid, {"worker": w.get("name") or "—", "id_number": w.get("id_number"),
                                         "payments": 0, "total_cents": 0})
        agg["payments"] += 1
        agg["total_cents"] += int(r.get("amount_cents") or 0)
        if r.get("project"):
            by_project[r["project"]] = by_project.get(r["project"], 0) + int(r.get("amount_cents") or 0)
    total = sum(a["total_cents"] for a in by_worker.values())
    return {
        "since": since, "until": until, "total_cents": total, "payment_count": len(rows),
        "by_worker": sorted(by_worker.values(), key=lambda x: -x["total_cents"]),
        "by_project": sorted([{"project": k, "total_cents": v} for k, v in by_project.items()],
                             key=lambda x: -x["total_cents"]),
        "payments": [{"date": r.get("txn_date"), "worker": (workers.get(r.get("worker_id")) or {}).get("name"),
                      "project": r.get("project"), "amount_cents": int(r.get("amount_cents") or 0),
                      "description": r.get("description")} for r in rows[:200]],
    }
