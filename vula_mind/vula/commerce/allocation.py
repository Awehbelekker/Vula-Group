"""
vula/commerce/allocation.py — which project and trade a bank line belongs to, learned.

2026-09-28 (DIGG): a bank line only ever got a project when it matched a registered worker, so
HPC001's R1.5M of payments were invisible to job costing. Every allocation the owner makes (the
dashboard picker, or the categories on an imported statement sheet) is remembered two ways:

- merchant: the line's counterparty identity (merchants.merchant_key — "NELITHO WAGES" →
  "nelitho wages") → project + trade;
- prefix: the first word of that identity when the owner's lines under it all go to one project
  ("hpc doors", "hpc screen", "hpc pc4" → "hpc" → HPC Bokaap), project only.

A rule is applied only when it's clear: at least two hits and ≥ 80% of that signal's hits on
the same answer. "BWH" bought for three different jobs stays unallocated for the owner to pick.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_TABLE = "commerce_allocation_rules"
_MIN_HITS = 2
_DOMINANCE = 0.8
# First words that name no project ("send andries…", "pay", "payment to…").
_GENERIC_PREFIX = {"send", "pay", "payment", "transfer", "fnb", "absa", "cash", "atm", "debit",
                   "credit", "card", "int", "interest", "fee", "fees", "reimbursement", "digg",
                   "yoco", "home", "salary", "wages", "the"}


def _client():
    from vula.commerce import service
    return service._client()


def signals(description: Optional[str], payee: Optional[str] = None) -> List[Tuple[str, str]]:
    from vula.commerce.merchants import merchant_key
    key = merchant_key(payee or description) or merchant_key(description)
    out: List[Tuple[str, str]] = []
    if key:
        out.append(("merchant", key))
        first = key.split()[0]
        if len(first) >= 3 and first not in _GENERIC_PREFIX:
            out.append(("prefix", first))
    return out


def learn(tenant_id: str, description: Optional[str], project: Optional[str],
          trade: Optional[str] = None, payee: Optional[str] = None) -> None:
    """Remember that a line like this goes to this project/trade (one more hit)."""
    if not project and not trade:
        return
    db = _client()
    now = datetime.now(timezone.utc).isoformat()
    for stype, sig in signals(description, payee):
        p, t = project, (trade if stype == "merchant" else None)
        if stype == "prefix" and not p:
            continue
        try:
            q = (db.table(_TABLE).select("id,hits").eq("tenant_id", tenant_id)
                 .eq("signal_type", stype).eq("signal", sig))
            q = q.eq("project", p) if p else q.is_("project", "null")
            q = q.eq("trade", t) if t else q.is_("trade", "null")
            ex = q.limit(1).execute().data or []
            if ex:
                (db.table(_TABLE).update({"hits": int(ex[0].get("hits") or 0) + 1, "updated_at": now})
                 .eq("id", ex[0]["id"]).execute())
            else:
                db.table(_TABLE).insert({"tenant_id": tenant_id, "signal_type": stype, "signal": sig,
                                         "project": p, "trade": t, "hits": 1, "updated_at": now}).execute()
        except Exception as exc:
            log.debug("allocation learn skipped (run migration 185?): %s", exc)


def load_rules(tenant_id: str) -> List[Dict]:
    try:
        rows = (_client().table(_TABLE).select("signal,signal_type,project,trade,hits")
                .eq("tenant_id", tenant_id).limit(5000).execute().data or [])
        return [r for r in rows if isinstance(r, dict)]
    except Exception as exc:
        log.debug("allocation rules read skipped: %s", exc)
        return []


def _decide(rules: List[Dict], stype: str, sig: str) -> Optional[Dict]:
    mine = [r for r in rules if r.get("signal_type") == stype and r.get("signal") == sig]
    total = sum(int(r.get("hits") or 0) for r in mine)
    if not mine or total < _MIN_HITS:
        return None
    best = max(mine, key=lambda r: int(r.get("hits") or 0))
    return best if int(best.get("hits") or 0) >= _DOMINANCE * total else None


def suggest(rules: List[Dict], description: Optional[str],
            payee: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(project, trade) for a line from learned rules, or (None, None) when not clear."""
    project = trade = None
    for stype, sig in signals(description, payee):
        hit = _decide(rules, stype, sig)
        if not hit:
            continue
        project = project or hit.get("project")
        if stype == "merchant":
            trade = trade or hit.get("trade")
    return project, trade


def apply_to_existing(tenant_id: str) -> int:
    """Put already-saved, unallocated bank lines on a project wherever a learned rule is clear
    (e.g. after a statement sheet taught "HPC …" → HPC Bokaap). Returns how many were set."""
    from vula.commerce.ledger import _all_pages
    rules = load_rules(tenant_id)
    if not rules:
        return 0
    db = _client()

    def make():
        return (db.table("commerce_bank_transactions")
                .select("id,description,payee,project,trade,match_status")
                .eq("tenant_id", tenant_id).is_("project", "null"))
    try:
        rows = _all_pages(make)
    except Exception as exc:
        log.debug("apply allocations skipped: %s", exc)
        return 0
    n = 0
    for r in rows:
        if r.get("match_status") == "ignored":
            continue
        project, trade = suggest(rules, r.get("description"), r.get("payee"))
        if not project:
            continue
        patch = {"project": project}
        if trade and not r.get("trade"):
            patch["trade"] = trade
        try:
            db.table("commerce_bank_transactions").update(patch).eq("tenant_id", tenant_id).eq("id", r["id"]).execute()
            n += 1
        except Exception as exc:
            log.debug("allocation apply failed for %s: %s", r.get("id"), exc)
    return n
