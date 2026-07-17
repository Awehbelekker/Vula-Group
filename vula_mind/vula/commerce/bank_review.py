"""
vula/commerce/bank_review.py — WhatsApp review loop for bank transactions Vula couldn't
allocate confidently. Instead of the owner opening the Bank tab, Vula asks them one item
at a time ("R720 to 'Lonese Jacobs' — what's this for?"), applies the answer, LEARNS the
rule, and moves to the next. State lives on the row itself: categorized_by 'default' =
waiting, 'asked' = question sent (one at a time), 'owner' = answered.

A second, independent question type lives here too: an unmatched CREDIT — money in that
couldn't be matched to an invoice/order by amount+reference — asks "which order/customer is
this for?" (match_status 'unmatched' → 'asked' → 'matched'/'ignored'). Different axis from the
category loop above (WHO paid vs WHAT category), so it uses match_status, not categorized_by,
and the two loops never collide on the same row.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

PENDING = ("default", "asked")


def _client():
    from vula.commerce import service
    return service._client()


def pending_txns(tenant_id: str) -> List[dict]:
    try:
        return (_client().table("commerce_bank_transactions").select("*")
                .eq("tenant_id", tenant_id).in_("categorized_by", list(PENDING))
                .order("txn_date").limit(500).execute().data or [])
    except Exception as exc:
        log.debug("pending_txns skipped: %s", exc)
        return []


def _question(txn: dict, i: int, n: int) -> str:
    amt = int(txn.get("amount_cents") or 0) / 100
    arrow = "paid OUT" if txn.get("direction") == "out" else "received"
    return (f"🏦 {i}/{n}: *R{amt:,.2f}* {arrow} on {txn.get('txn_date')} — "
            f"\"{(txn.get('description') or '')[:90]}\".\n"
            "What is this? Reply with a category (e.g. stock / fuel / rent / wages / "
            "marketing / equipment / personal / sales), or 'skip' / 'stop'.")


def start_review(tenant_id: str) -> Optional[str]:
    """Mark the first pending transaction 'asked' and return the question to send.
    Returns None when there's nothing to review."""
    rows = pending_txns(tenant_id)
    if not rows:
        return None
    # Re-ask an already-'asked' one first (a previous question that never got answered).
    txn = next((t for t in rows if t.get("categorized_by") == "asked"), rows[0])
    try:
        _client().table("commerce_bank_transactions").update(
            {"categorized_by": "asked"}).eq("id", txn["id"]).execute()
    except Exception:
        pass
    return _question(txn, 1, len(rows))


def _match_account(answer: str, accounts: List[dict], direction: str) -> Optional[str]:
    """Map a short owner answer to an account code: exact code → name/word overlap → aliases."""
    low = (answer or "").strip().lower()
    if not low:
        return None
    aliases = {
        "personal": "owner_drawings", "private": "owner_drawings", "drawings": "owner_drawings",
        "own": "owner_drawings", "family": "owner_drawings",
        "stock": "cost_of_sales", "supplies": "cost_of_sales", "supplier": "cost_of_sales",
        "ingredients": "cost_of_sales", "fish": "cost_of_sales",
        "labour": "casual_labour", "labor": "casual_labour", "worker": "casual_labour",
        "wages": "wages", "salary": "wages", "staff": "wages",
        "petrol": "fuel", "diesel": "fuel", "transport": "delivery",
        "income": "other_income", "sale": "sales", "customer": "sales",
        "electricity": "utilities", "water": "utilities", "internet": "utilities",
        "advert": "marketing", "ads": "marketing",
    }
    codes = {a["code"] for a in accounts}
    cleaned = low.replace(" ", "_")
    if cleaned in codes:
        return cleaned
    for word, code in aliases.items():
        if word in low and code in codes:
            return code
    for a in accounts:
        name = a["name"].lower()
        if low in name or any(w and w in name for w in low.split()):
            return a["code"]
    return None


async def handle_answer(tenant_id: str, text: str) -> Optional[str]:
    """If a review question is outstanding, treat `text` as its answer: allocate, learn,
    and return the reply (confirmation + next question). None = not a review answer."""
    text = (text or "").strip()
    if not text or len(text) > 60:
        return None
    db = _client()
    try:
        asked = (db.table("commerce_bank_transactions").select("*")
                 .eq("tenant_id", tenant_id).eq("categorized_by", "asked")
                 .limit(1).execute().data or [])
    except Exception:
        return None
    if not asked:
        return None
    txn = asked[0]
    low = text.lower()

    if low in ("stop", "later", "cancel", "end"):
        db.table("commerce_bank_transactions").update(
            {"categorized_by": "default"}).eq("id", txn["id"]).execute()
        return "👍 No problem — the rest are waiting in your 🏦 Bank tab whenever you're ready."
    if low in ("skip", "next", "dunno", "not sure"):
        db.table("commerce_bank_transactions").update(
            {"categorized_by": "skipped"}).eq("id", txn["id"]).execute()
        return _next_or_done(tenant_id, "⏭ Skipped.")

    from vula.commerce import accounting
    accounts = accounting.ensure_chart(tenant_id)
    code = _match_account(text, accounts, txn.get("direction") or "out")
    if not code:
        return ("🤔 I couldn't match that to an account. Try one word like: stock, fuel, rent, "
                "wages, marketing, equipment, personal, sales — or 'skip'.")

    acct = next((a for a in accounts if a["code"] == code), None)
    vat = accounting.vat_for(acct, int(txn.get("amount_cents") or 0),
                             accounting.is_vat_registered(tenant_id))
    db.table("commerce_bank_transactions").update(
        {"account_code": code, "vat_cents": vat,
         "vat_treatment": (acct or {}).get("vat_treatment"),
         "categorized_by": "owner"}).eq("id", txn["id"]).execute()
    accounting.learn_category_rule(tenant_id, txn, code)   # similar lines auto-file next time
    return _next_or_done(
        tenant_id, f"✅ R{int(txn.get('amount_cents') or 0)/100:,.2f} → *{(acct or {}).get('name', code)}* — learned.")


async def kickoff(tenant_id: str, rec: Dict[str, Any]) -> None:
    """After a statement is reconciled: WhatsApp the owner the summary and, if anything
    needs their input, start the one-at-a-time review right there in the chat."""
    try:
        from vula.integrations.notify import notify_team
        msg = (f"🏦 Bank statement processed: *{rec.get('parsed', 0)} transactions*.\n"
               f"✅ {rec.get('matched_invoices', 0)} matched to invoices · "
               f"🧾 {rec.get('matched_expenses', 0)} matched to receipts · "
               f"👷 {rec.get('matched_workers', 0)} worker payments")
        if rec.get("needs_input"):
            q = start_review(tenant_id)
            if q:
                msg += f"\n\nA few I couldn't allocate — let's sort them here:\n\n{q}"
        else:
            msg += "\nEverything allocated ✅"
        await notify_team(tenant_id, "bank_review", msg)
    except Exception as exc:
        log.debug("bank review kickoff skipped: %s", exc)


def _next_or_done(tenant_id: str, prefix: str) -> str:
    rows = pending_txns(tenant_id)
    if not rows:
        return prefix + "\n\n🎉 That's everything — your books are fully allocated."
    txn = rows[0]
    try:
        _client().table("commerce_bank_transactions").update(
            {"categorized_by": "asked"}).eq("id", txn["id"]).execute()
    except Exception:
        pass
    return prefix + "\n\n" + _question(txn, 1, len(rows))


# ── Client/order matching for unmatched credits ─────────────────────────────────

def pending_client_txns(tenant_id: str) -> List[dict]:
    """Unmatched money-in that couldn't be auto-matched to an invoice or order."""
    try:
        return (_client().table("commerce_bank_transactions").select("*")
                .eq("tenant_id", tenant_id).eq("direction", "in")
                .in_("match_status", ["unmatched", "asked"])
                .order("txn_date").limit(500).execute().data or [])
    except Exception as exc:
        log.debug("pending_client_txns skipped: %s", exc)
        return []


def _client_question(txn: dict, i: int, n: int) -> str:
    amt = int(txn.get("amount_cents") or 0) / 100
    return (f"🏦 {i}/{n}: *R{amt:,.2f}* received on {txn.get('txn_date')} — "
            f"\"{(txn.get('description') or '')[:90]}\".\n"
            "Which order or customer is this for? Reply with the order number "
            "(e.g. OFF-00006) or the customer's name, or 'skip'.")


def start_client_review(tenant_id: str) -> Optional[str]:
    """Mark the first unmatched credit 'asked' and return the question to send.
    Returns None when there's nothing to review."""
    rows = pending_client_txns(tenant_id)
    if not rows:
        return None
    txn = next((t for t in rows if t.get("match_status") == "asked"), rows[0])
    try:
        _client().table("commerce_bank_transactions").update(
            {"match_status": "asked"}).eq("id", txn["id"]).execute()
    except Exception:
        pass
    return _client_question(txn, 1, len(rows))


async def _apply_order_match(tenant_id: str, txn: dict, order: dict) -> str:
    from vula.commerce import service
    await service.update_order_status(order["id"], "paid")
    try:
        from vula.api.yoco import _notify_order_paid
        await _notify_order_paid(tenant_id, order.get("display_id") or order["id"], order["id"],
                                 order.get("customer_phone"), order.get("customer_name") or "",
                                 int(order.get("total_cents") or 0))
    except Exception as exc:
        log.warning("order-paid notify failed for WhatsApp bank match: %s", exc)
    _client().table("commerce_bank_transactions").update(
        {"matched_order_id": order["id"], "match_status": "matched"}).eq("id", txn["id"]).execute()
    amt = int(txn.get("amount_cents") or 0) / 100
    return _next_client_or_done(
        tenant_id, f"✅ R{amt:,.2f} → order *{order.get('display_id') or order['id']}* "
                   f"({order.get('customer_name') or 'customer'}) — marked paid.")


async def _apply_invoice_match(tenant_id: str, txn: dict, invoice: dict) -> str:
    from vula.commerce import service
    await service.update_invoice_status(tenant_id, invoice["id"], "paid")
    _client().table("commerce_bank_transactions").update(
        {"matched_invoice_id": invoice["id"], "match_status": "matched"}).eq("id", txn["id"]).execute()
    amt = int(txn.get("amount_cents") or 0) / 100
    return _next_client_or_done(
        tenant_id, f"✅ R{amt:,.2f} → invoice *{invoice.get('invoice_number') or invoice['id']}* "
                   f"({invoice.get('customer_name') or 'customer'}) — marked paid.")


async def handle_client_answer(tenant_id: str, text: str) -> Optional[str]:
    """If a client-matching question is outstanding, treat `text` as the order number or
    customer name it's for. Returns the reply (confirmation + next question), or None if this
    wasn't a client-matching answer."""
    text = (text or "").strip()
    if not text or len(text) > 60:
        return None
    db = _client()
    try:
        asked = (db.table("commerce_bank_transactions").select("*")
                 .eq("tenant_id", tenant_id).eq("direction", "in")
                 .eq("match_status", "asked").limit(1).execute().data or [])
    except Exception:
        return None
    if not asked:
        return None
    txn = asked[0]
    low = text.lower()

    if low in ("stop", "later", "cancel", "end"):
        db.table("commerce_bank_transactions").update(
            {"match_status": "unmatched"}).eq("id", txn["id"]).execute()
        return "👍 No problem — it's waiting in your 🏦 Bank tab whenever you're ready."
    if low in ("skip", "next", "dunno", "not sure"):
        db.table("commerce_bank_transactions").update(
            {"match_status": "ignored"}).eq("id", txn["id"]).execute()
        return _next_client_or_done(tenant_id, "⏭ Skipped.")

    # 1. Trust an explicit order number / invoice number outright — direct human intent beats
    # an amount tolerance check (covers deposits/partial payments too).
    orow = (db.table("commerce_orders")
            .select("id,display_id,customer_name,customer_phone,total_cents,status")
            .eq("tenant_id", tenant_id).ilike("display_id", text)
            .eq("status", "pending_payment").limit(1).execute().data or [])
    if orow:
        return await _apply_order_match(tenant_id, txn, orow[0])
    irow = (db.table("commerce_invoices")
            .select("id,invoice_number,customer_name,total_cents,status")
            .eq("tenant_id", tenant_id).ilike("invoice_number", text)
            .in_("status", ["sent", "overdue"]).limit(1).execute().data or [])
    if irow:
        return await _apply_invoice_match(tenant_id, txn, irow[0])

    # 2. Name search — an amount match is required here since a name alone is weaker evidence
    # (reuses the same amount+name-overlap matcher the automatic path uses).
    from vula.commerce.bank_rec import _match_order, _match_invoice, _tok
    orders = (db.table("commerce_orders")
              .select("id,display_id,customer_name,customer_phone,total_cents")
              .eq("tenant_id", tenant_id).eq("status", "pending_payment").limit(500).execute().data or [])
    invoices = (db.table("commerce_invoices")
                .select("id,invoice_number,customer_name,total_cents,status,doc_type")
                .eq("tenant_id", tenant_id).in_("status", ["sent", "overdue"]).limit(500).execute().data or [])
    invoices = [i for i in invoices if (i.get("doc_type") or "invoice") == "invoice"]
    name_toks = _tok(text)
    order_hits = [o for o in orders if name_toks & _tok(o.get("customer_name"))]
    invoice_hits = [i for i in invoices if name_toks & _tok(i.get("customer_name"))]
    augmented = dict(txn, reference=f"{txn.get('reference') or ''} {text}")

    om = _match_order(augmented, order_hits) if order_hits else None
    if om:
        return await _apply_order_match(tenant_id, txn, om)
    im = _match_invoice(augmented, invoice_hits) if invoice_hits else None
    if im:
        return await _apply_invoice_match(tenant_id, txn, im)

    if order_hits or invoice_hits:
        return ("🤔 Found that name but the amount doesn't line up — reply with the exact "
                "order number instead (e.g. OFF-00006), or 'skip'.")
    return ("🤔 I couldn't find an order or invoice matching that. Try the order number "
            "(e.g. OFF-00006) or the exact customer name — or 'skip'.")


def _next_client_or_done(tenant_id: str, prefix: str) -> str:
    rows = pending_client_txns(tenant_id)
    if not rows:
        return prefix + "\n\n🎉 That's everything — all payments allocated."
    txn = rows[0]
    try:
        _client().table("commerce_bank_transactions").update(
            {"match_status": "asked"}).eq("id", txn["id"]).execute()
    except Exception:
        pass
    return prefix + "\n\n" + _client_question(txn, 1, len(rows))
