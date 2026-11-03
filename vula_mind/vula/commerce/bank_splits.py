"""vula/commerce/bank_splits.py — one bank payment split across projects.

3 Nov 2026: a payment to a subcontractor paid workers on two jobs, but a bank line could
only carry one project, so one project's costs were too high and the other's too low. A split gives
parts of a line to other projects; what the parts don't cover stays on the line's own project.

  split(tenant, txn_id, parts)   store the parts (replacing any earlier split of that line), read back
  apply(tenant, rows)            bank rows as project costing should see them: each part its own row,
                                 the line itself reduced to what is left (dropped when nothing is)
  for_txn / describe             what a line is split into, in words

The bank line itself is never changed (amount, date, balance), so the statement still reconciles.
Money in integer cents; parts can't add up to more than the line. Migration 244.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "commerce_bank_splits"
MAX_PARTS = 10


def _client():
    from vula.commerce.service import _client as c
    return c()


def _money(c: int) -> str:
    return f"R{c / 100:,.2f}"


def _line(tenant_id: str, txn_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table("commerce_bank_transactions")
            .select("id,txn_date,description,amount_cents,direction,project,account_code")
            .eq("tenant_id", tenant_id).eq("id", txn_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def for_txn(tenant_id: str, txn_id: str) -> List[Dict[str, Any]]:
    return (_client().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("txn_id", txn_id)
            .eq("status", "active").limit(MAX_PARTS * 3).execute().data or [])


def _active(tenant_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """txn_id → its active parts (tenant-scoped). Empty before migration 244."""
    try:
        from vula.commerce.ledger import _all_pages
        rows = _all_pages(lambda: (_client().table(TABLE).select("txn_id,project,amount_cents,account_code,note")
                                   .eq("tenant_id", tenant_id).eq("status", "active").order("created_at")))
    except Exception as exc:  # noqa: BLE001 — before migration 244: no splits
        log.debug("bank splits read skipped: %s", exc)
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["txn_id"]), []).append(r)
    return out


def check_parts(line: Dict[str, Any], parts: List[Dict[str, Any]], known: List[str]) -> List[Dict[str, Any]]:
    """Validated parts: a known project each, whole cents > 0, not on the line's own project, at
    most MAX_PARTS, together no more than the line. ValueError says what is wrong."""
    from vula.commerce.service import project_key
    if not parts:
        raise ValueError("Say which project gets how much.")
    if len(parts) > MAX_PARTS:
        raise ValueError(f"At most {MAX_PARTS} parts per payment.")
    by_key = {project_key(p): p for p in known}
    out, total = [], 0
    for p in parts:
        name = by_key.get(project_key(p.get("project")))
        if not name:
            raise ValueError(f"There's no project called “{p.get('project')}”.")
        try:
            cents = int(p.get("amount_cents"))
        except (TypeError, ValueError):
            raise ValueError("Each part needs an amount.") from None
        if cents <= 0:
            raise ValueError("Each part must be more than R0.")
        if line.get("project") and project_key(name) == project_key(line["project"]):
            raise ValueError(f"The payment is already on {name}; what isn't split off stays there.")
        total += cents
        out.append({"project": name, "amount_cents": cents, "note": (p.get("note") or "").strip()[:300] or None})
    if total > int(line.get("amount_cents") or 0):
        raise ValueError(f"The parts ({_money(total)}) are more than the payment ({_money(int(line['amount_cents']))}).")
    return out


def split(tenant_id: str, txn_id: str, parts: List[Dict[str, Any]], by: Optional[str] = None,
          source: str = "whatsapp") -> Dict[str, Any]:
    """Split a bank line: the parts go to their projects, the rest stays on the line's project. An
    earlier split of the same line is marked replaced (never deleted). Read back.
    Returns {"line", "parts", "remainder_cents"}; ValueError / LookupError for bad input."""
    from vula.commerce import expenses
    line = _line(tenant_id, txn_id)
    if not line:
        raise LookupError("That bank payment isn't on this business's statements.")
    good = check_parts(line, parts, expenses.known_projects(tenant_id))
    db = _client()
    old = for_txn(tenant_id, txn_id)
    for r in old:
        db.table(TABLE).update({"status": "replaced"}).eq("tenant_id", tenant_id).eq("id", r["id"]).execute()
    db.table(TABLE).insert([{"tenant_id": tenant_id, "txn_id": txn_id, "project": p["project"],
                             "amount_cents": p["amount_cents"], "account_code": line.get("account_code"),
                             "note": p["note"], "status": "active", "created_by": by} for p in good]).execute()
    back = for_txn(tenant_id, txn_id)
    want = sorted((p["project"], p["amount_cents"]) for p in good)
    if sorted((r["project"], int(r["amount_cents"])) for r in back) != want:
        raise RuntimeError("the split didn't read back — please check it")
    try:
        from vula.commerce import decisions
        decisions.record(tenant_id, describe(line, back), project=None, subject="Bank payment split",
                         amount_cents=sum(p["amount_cents"] for p in good),
                         refs=[{"table": "commerce_bank_transactions", "id": str(txn_id)}],
                         made_by=by, source=source)
    except Exception as exc:  # noqa: BLE001 — the split stands without the note
        log.debug("split decision note skipped: %s", exc)
    return {"line": line, "parts": back,
            "remainder_cents": int(line["amount_cents"]) - sum(int(r["amount_cents"]) for r in back)}


def unsplit(tenant_id: str, txn_id: str) -> int:
    """Put the whole payment back on its own project. Returns the parts replaced."""
    old = for_txn(tenant_id, txn_id)
    for r in old:
        _client().table(TABLE).update({"status": "replaced"}).eq("tenant_id", tenant_id).eq("id", r["id"]).execute()
    if for_txn(tenant_id, txn_id):
        raise RuntimeError("the split is still there — please check it")
    return len(old)


def describe(line: Dict[str, Any], parts: List[Dict[str, Any]]) -> str:
    total = int(line.get("amount_cents") or 0)
    rest = total - sum(int(p["amount_cents"]) for p in parts)
    bits = [f"{_money(int(p['amount_cents']))} to {p['project']}" for p in parts]
    if rest:
        bits.append(f"{_money(rest)} stays on {line.get('project') or 'no project'}")
    return (f"The {_money(total)} payment of {line.get('txn_date')} (“{line.get('description')}”) is split: "
            + ", ".join(bits) + ".")


def apply(tenant_id: str, rows: List[Dict[str, Any]],
          splits: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> List[Dict[str, Any]]:
    """`rows` (bank lines) with each split line replaced by its parts plus what is left on the line.
    Rows without a split come back unchanged; a part keeps the line's date, direction and account."""
    splits = _active(tenant_id) if splits is None else splits
    if not splits:
        return rows
    out = []
    for r in rows:
        parts = splits.get(str(r.get("id")))
        if not parts:
            out.append(r)
            continue
        total = int(r.get("amount_cents") or 0)
        given = 0
        for i, p in enumerate(parts):
            cents = min(int(p["amount_cents"]), total - given)     # never more than the line
            if cents <= 0:
                continue
            given += cents
            out.append({**r, "id": f"{r['id']}:split:{i + 1}", "project": p["project"],
                        "amount_cents": cents, "account_code": p.get("account_code") or r.get("account_code"),
                        "split_of": r["id"]})
        if total - given > 0:
            out.append({**r, "amount_cents": total - given, "split_rest": True})
    return out


def to_cents(v: Any) -> Optional[int]:
    """'R15,050', '15 050.00', 15050 → 1505000 (rand, as the owner wrote it). None if not a number."""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip().lower().replace("r", "").replace(" ", "").replace(" ", "")
    if s.count(",") and not s.count("."):
        s = s.replace(",", "") if len(s.split(",")[-1]) == 3 else s.replace(",", ".")
    s = s.replace(",", "")
    try:
        return int((Decimal(s) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def find_lines(tenant_id: str, amount_cents: int, date: Optional[str] = None,
               words: str = "") -> List[Dict[str, Any]]:
    """Money-out bank lines of exactly `amount_cents` (within 5 days of `date` when given, and whose
    description has every word in `words`), newest first."""
    from datetime import date as _d, timedelta
    q = (_client().table("commerce_bank_transactions")
         .select("id,txn_date,description,amount_cents,direction,project,account_code")
         .eq("tenant_id", tenant_id).eq("amount_cents", int(amount_cents)).neq("direction", "in"))
    if date:
        try:
            d = _d.fromisoformat(str(date)[:10])
            q = q.gte("txn_date", (d - timedelta(days=5)).isoformat()).lte("txn_date", (d + timedelta(days=5)).isoformat())
        except ValueError:
            pass
    rows = q.order("txn_date", desc=True).limit(20).execute().data or []
    want = [w for w in (words or "").lower().split() if len(w) >= 3]
    return [r for r in rows if all(w in (r.get("description") or "").lower() for w in want)]
