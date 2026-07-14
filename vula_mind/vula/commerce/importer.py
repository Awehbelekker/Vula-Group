"""
vula/commerce/importer.py — historical onboarding: reconstruct a tenant's PAST orders from their
existing comms (WhatsApp chat exports, email history, pasted text) so Vula shows the full picture
from day one.

Two steps, deliberately: EXTRACT (LLM turns unstructured text into structured orders) returns a
draft for the owner to REVIEW; COMMIT only creates orders/contacts once the owner confirms — we
never auto-write guessed data into the books.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List

log = logging.getLogger(__name__)

IMPORT_TAG = "[VULA-IMPORT]"     # marks historical-import orders (channel has a CHECK, so use notes)


def _client():
    from vula.commerce import service
    return service._client()


def _now():
    from vula.commerce import service
    return service._now()


# ── Extract (LLM) ─────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = (
    "You reconstruct a small business's PAST ORDERS from a chat log or emails (e.g. a WhatsApp "
    "export). Return ONLY a JSON array — no prose. Each element is one order:\n"
    '{"customer_name":string|null,"customer_phone":string|null,"date":"YYYY-MM-DD"|null,'
    '"items":[{"name":string,"quantity":number,"unit_price_rands":number|null}],'
    '"total_rands":number|null,"delivery_address":string|null,"delivered":boolean,"paid":boolean,'
    '"notes":string|null}\n'
    "Rules: one element per distinct order/purchase. Only include real orders (ignore greetings, "
    "questions, small talk). Infer paid=true if payment/EFT/'paid'/proof is mentioned; delivered=true "
    "if delivery/collected/'received' is mentioned. Money in RANDS (not cents). If unknown, use null."
)


async def extract_orders(text: str) -> List[Dict[str, Any]]:
    """Draft structured orders from historical comms text (for the owner to review). Never commits."""
    import litellm
    from core.llm_router import resolve_generation_route, escalate_to_cloud
    if not (text or "").strip():
        return []
    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="history_import")
    # Extraction needs reliable structured output; prefer the cloud model (a one-off batch, not
    # per-message) — small local reasoning models return empty/verbose here. Falls back if no key.
    esc = escalate_to_cloud("history_import", task_type="history_import")
    if esc:
        model, api_key, api_base = esc
    chunk = text[:28000]     # keep within budget; the UI can import in batches
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "system", "content": _EXTRACT_SYSTEM}, {"role": "user", "content": chunk}],
            temperature=0, max_tokens=4000, api_key=api_key, api_base=api_base)
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("history extract failed: %s", exc)
        return []
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    i, j = raw.find("["), raw.rfind("]")
    if i < 0 or j < 0:
        return []
    try:
        arr = json.loads(raw[i:j + 1])
    except Exception:
        return []
    out = []
    for o in arr if isinstance(arr, list) else []:
        if not isinstance(o, dict):
            continue
        items = [it for it in (o.get("items") or []) if isinstance(it, dict) and (it.get("name") or "").strip()]
        if not items and not o.get("total_rands"):
            continue        # nothing orderable
        out.append({
            "customer_name": (o.get("customer_name") or "").strip() or None,
            "customer_phone": re.sub(r"[^\d+]", "", str(o.get("customer_phone") or "")) or None,
            "date": o.get("date") or None,
            "items": [{"name": (it.get("name") or "").strip(),
                       "quantity": float(it.get("quantity") or 1),
                       "unit_price_rands": float(it["unit_price_rands"]) if it.get("unit_price_rands") is not None else None}
                      for it in items],
            "total_rands": float(o["total_rands"]) if o.get("total_rands") is not None else None,
            "delivery_address": o.get("delivery_address") or None,
            "delivered": bool(o.get("delivered")),
            "paid": bool(o.get("paid")),
            "notes": o.get("notes") or None,
        })
    return out


# ── Historical INVOICES (e.g. a Xero export) ──────────────────────────────────
# Why: the bank statement's credits reference the tenant's OLD invoice numbers
# ("Payment Received: INVOICE 003983"). Registering those invoices lets bank
# reconciliation resolve "invoices sent vs paid" historically, not just forward.

_INVOICE_SYSTEM = (
    "You are given a small business's OLD INVOICE LIST — usually a Xero/accounting export "
    "(CSV or pasted table), but possibly free text. Return ONLY a JSON array, one element per "
    "invoice:\n"
    '{"invoice_number":string,"customer_name":string|null,"date":"YYYY-MM-DD"|null,'
    '"due_date":"YYYY-MM-DD"|null,"total_rands":number,"amount_paid_rands":number|null,'
    '"status":"paid"|"unpaid"|"partial"|null}\n'
    "Rules: keep the invoice number EXACTLY as written (e.g. INV-004032 or 003983). Money in "
    "RANDS. status paid if fully paid / amount due is 0; unpaid if still owed. Ignore header "
    "rows, totals rows and credit notes. Dates may be DD/MM/YYYY (South Africa — day first)."
)


async def extract_invoices(text: str) -> List[Dict[str, Any]]:
    """Draft structured invoices from a pasted export (for the owner to review). Never commits."""
    import litellm
    from core.llm_router import resolve_generation_route, escalate_to_cloud
    if not (text or "").strip():
        return []
    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="history_import")
    esc = escalate_to_cloud("history_import", task_type="history_import")
    if esc:
        model, api_key, api_base = esc
    out: List[Dict[str, Any]] = []
    # Chunk big exports on line boundaries so no output gets truncated.
    lines = text[:200000].splitlines()
    chunks, cur, size = [], [], 0
    for ln in lines:
        cur.append(ln)
        size += len(ln) + 1
        if size > 9000:
            chunks.append("\n".join(cur))
            cur, size = [], 0
    if cur:
        chunks.append("\n".join(cur))
    for chunk in chunks[:20]:
        try:
            resp = await litellm.acompletion(
                model=model,
                messages=[{"role": "system", "content": _INVOICE_SYSTEM},
                          {"role": "user", "content": chunk}],
                temperature=0, max_tokens=8000, api_key=api_key, api_base=api_base)
            raw = resp.choices[0].message.content or ""
        except Exception as exc:
            log.warning("invoice extract failed: %s", exc)
            continue
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j <= i:
            continue
        try:
            arr = json.loads(raw[i:j + 1])
        except Exception:
            try:
                import json_repair
                arr = json_repair.loads(raw[i:j + 1])
            except Exception:
                continue
        for r in arr if isinstance(arr, list) else []:
            if not isinstance(r, dict) or not (r.get("invoice_number") or "").strip():
                continue
            try:
                total = float(r.get("total_rands") or 0)
            except Exception:
                total = 0
            if total <= 0:
                continue
            out.append({
                "invoice_number": str(r["invoice_number"]).strip(),
                "customer_name": (r.get("customer_name") or "").strip() or None,
                "date": r.get("date") or None,
                "due_date": r.get("due_date") or None,
                "total_rands": total,
                "amount_paid_rands": float(r["amount_paid_rands"]) if r.get("amount_paid_rands") is not None else None,
                "status": (r.get("status") or "").lower() or None,
            })
    return out


def commit_invoices(tenant_id: str, invoices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Register reviewed historical invoices. Skips numbers that already exist. Paid → 'paid';
    unpaid/partial → 'sent' so bank reconciliation can match the payment when it lands."""
    db = _client()
    try:
        existing = {(r.get("invoice_number") or "").strip().lower()
                    for r in (db.table("commerce_invoices").select("invoice_number")
                              .eq("tenant_id", tenant_id).limit(5000).execute().data or [])}
    except Exception:
        existing = set()
    created, skipped = 0, 0
    for inv in invoices:
        num = (inv.get("invoice_number") or "").strip()
        if not num or num.lower() in existing:
            skipped += 1
            continue
        total = _cents(inv.get("total_rands"))
        if total <= 0:
            skipped += 1
            continue
        status = "paid" if (inv.get("status") == "paid") else "sent"
        row = {
            "id": str(uuid.uuid4()), "tenant_id": tenant_id,
            "invoice_number": num,
            "customer_name": inv.get("customer_name") or "Imported customer",
            "line_items": "[]",
            "subtotal_cents": total, "vat_cents": 0, "total_cents": total,
            "status": status,
            "date": inv.get("date") or None, "due_date": inv.get("due_date") or None,
            "direction": "outbound", "doc_type": "invoice", "source": "import",
        }
        try:
            db.table("commerce_invoices").insert(row).execute()
        except Exception as exc:
            # Older schema variants — drop optional columns and retry once.
            for k in ("date", "due_date", "direction", "doc_type", "source"):
                row.pop(k, None)
            try:
                db.table("commerce_invoices").insert(row).execute()
            except Exception as exc2:
                log.warning("import invoice insert failed (%s): %s", num, exc2)
                skipped += 1
                continue
        existing.add(num.lower())
        created += 1
    return {"created": created, "skipped": skipped}


# ── Commit (create real orders + contacts) ────────────────────────────────────

def _cents(rands) -> int:
    try:
        return int(round(float(rands) * 100))
    except Exception:
        return 0


async def commit_orders(tenant_id: str, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create real historical orders + contacts from reviewed drafts. Idempotent-ish: skips a draft
    that already has an identical import order (same phone/name + date + total)."""
    from vula.commerce import service
    db = _client()
    # Product name → id, to link items where we can.
    try:
        products = await service.list_products(tenant_id, in_stock_only=False)
    except Exception:
        products = []
    pmap = {(p.get("name") or "").lower(): p for p in products}

    # Allocate display ids sequentially in the batch — the standard helper picks the most-recent
    # order by created_at, but imports have PAST dates so it wouldn't advance (→ duplicate ids).
    prefix = tenant_id.upper()[:3]
    seq = 0
    try:
        for r in (db.table("commerce_orders").select("display_id").eq("tenant_id", tenant_id)
                  .limit(10000).execute().data or []):
            m = re.search(r"(\d+)$", r.get("display_id") or "")
            if m:
                seq = max(seq, int(m.group(1)))
    except Exception:
        pass

    created, skipped = 0, 0
    for o in orders:
        items_in = o.get("items") or []
        line_items = []
        for it in items_in:
            name = (it.get("name") or "").strip()
            if not name:
                continue
            p = pmap.get(name.lower())
            unit = _cents(it.get("unit_price_rands")) or (int(p.get("price_cents")) if p else 0)
            qty = float(it.get("quantity") or 1)
            line_items.append({"product_id": (p.get("id") if p else None), "product_name": name,
                               "quantity": qty, "unit_price_cents": unit,
                               "total_cents": int(round(qty * unit))})
        subtotal = sum(i["total_cents"] for i in line_items)
        total = _cents(o.get("total_rands")) or subtotal
        if total <= 0 and not line_items:
            skipped += 1
            continue

        created_at = (o.get("date") + "T12:00:00+00:00") if o.get("date") else _now()
        # skip obvious duplicate re-imports (import orders carry the IMPORT_TAG in delivery_notes)
        try:
            dup = (db.table("commerce_orders").select("id").eq("tenant_id", tenant_id)
                   .like("delivery_notes", f"{IMPORT_TAG}%").eq("total_cents", total)
                   .eq("customer_name", o.get("customer_name") or "Imported customer").limit(1).execute().data or [])
            if dup:
                skipped += 1
                continue
        except Exception:
            pass

        status = "delivered" if o.get("delivered") else ("paid" if o.get("paid") else "pending_payment")
        seq += 1
        order = {
            "id": str(uuid.uuid4()), "display_id": f"{prefix}-{seq:05d}",
            "tenant_id": tenant_id, "customer_phone": o.get("customer_phone") or "",
            "customer_name": o.get("customer_name") or "Imported customer",
            "delivery_address": o.get("delivery_address") or "—",
            "subtotal_cents": subtotal or total, "delivery_cents": max(total - subtotal, 0) if subtotal else 0,
            "total_cents": total, "status": status, "channel": "web",   # 'web' — channel has a CHECK
            "payment_method": "eft" if o.get("paid") else None,
            "delivery_notes": (IMPORT_TAG + (f" {o['notes']}" if o.get("notes") else "")),
            "created_at": created_at, "updated_at": _now(),
        }
        try:
            res = db.table("commerce_orders").insert(order).execute()
        except Exception as exc:
            order.pop("payment_method", None)
            log.warning("import order insert retried: %s", exc)
            res = db.table("commerce_orders").insert(order).execute()
        oid = res.data[0]["id"]
        if line_items:
            try:
                db.table("commerce_order_items").insert(
                    [{"id": str(uuid.uuid4()), "order_id": oid, **{k: li[k] for k in
                      ("product_id", "product_name", "quantity", "unit_price_cents", "total_cents")}}
                     for li in line_items]).execute()
            except Exception as exc:
                log.warning("import order items insert failed: %s", exc)
        # Capture the customer as a contact.
        if o.get("customer_phone"):
            try:
                db.table("commerce_contacts").upsert({
                    "tenant_id": tenant_id, "phone": re.sub(r"\D", "", o["customer_phone"]),
                    "name": o.get("customer_name"), "source": "import", "consent_status": "unknown",
                    "updated_at": _now()}, on_conflict="tenant_id,phone").execute()
            except Exception:
                pass
        created += 1
    return {"created": created, "skipped": skipped}
