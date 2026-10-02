"""
vula/commerce/bank_rec.py — read an emailed bank statement, parse transactions, reconcile.

The no-API path to moving a tenant off Xero: their weekly Capitec statement arrives by email as a
password-protected PDF (password = ID number). Vula unlocks it, extracts each transaction, and
reconciles: credits are matched to outstanding invoices (marked PAID), debits to expenses. Anything
uncertain is left `unmatched` for one-tap review — we never auto-mark an invoice on a weak match.

Reuses: pdfplumber (encrypted PDF), the LLM router, invoice mark-paid (service.update_invoice_status),
and Fernet secret storage (email credentials).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _now():
    from vula.commerce import service
    return service._now()


# ── Statement password (encrypted, on the primary mailbox) ─────────────────────
# A tenant can have several connected mailboxes (migration 093) — bank statements are
# assumed to arrive at whichever one is marked primary. Scoping both read and write to
# is_primary=True (not just tenant_id) matters now: without it, the UPDATE below would
# silently overwrite this field on every connected account, not just one.

def set_statement_password(tenant_id: str, password: str, bank: str = "Capitec") -> None:
    from vula.email_imap.credentials import encrypt_secret
    _client().table("vula_email_accounts").update(
        {"statement_password": encrypt_secret(password), "bank": bank}
    ).eq("tenant_id", tenant_id).eq("is_primary", True).execute()


def get_statement_password(tenant_id: str) -> Optional[str]:
    try:
        rows = (_client().table("vula_email_accounts").select("statement_password")
                .eq("tenant_id", tenant_id).eq("is_primary", True).limit(1).execute().data or [])
        enc = rows[0].get("statement_password") if rows else None
        if not enc:
            return None
        from vula.email_imap.credentials import decrypt_secret
        return decrypt_secret(enc)
    except Exception as exc:
        log.debug("statement password read skipped: %s", exc)
        return None


# ── PDF → text (handles password-protected + scanned) ─────────────────────────

def extract_pdf_text(pdf_path: Path, password: Optional[str] = None) -> str:
    """Text of a (possibly encrypted) PDF statement, including table rows."""
    import pdfplumber
    out: List[str] = []
    with pdfplumber.open(str(pdf_path), password=password or "") as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for table in (page.extract_tables() or []):
                for row in table:
                    if row:
                        text += "\n" + " | ".join(str(c) for c in row if c)
            out.append(text)
    return "\n".join(out).strip()


# ── Transaction extraction (LLM over the statement text) ──────────────────────

_TXN_SYSTEM = (
    "You are a bank-statement parser. From the statement text, extract EVERY transaction line as a "
    "JSON array. Return ONLY the JSON array, no prose or markdown. Each element:\n"
    '{"date":"YYYY-MM-DD","description":string,"amount_cents":integer,'
    '"direction":"in|out","balance_cents":integer|null,"reference":string|null}\n'
    "Rules: amount_cents is the ABSOLUTE value in cents (Rands×100). direction is 'in' for money "
    "received (credit/deposit) and 'out' for money paid (debit/withdrawal/fee). Ignore opening/closing "
    "balance summary lines. If a field is unknown use null. Dates on South African statements are "
    "DD/MM/YYYY (day first — 11/07/2026 means 11 July 2026); convert to YYYY-MM-DD accordingly."
)


async def extract_transactions(statement_text: str) -> List[Dict[str, Any]]:
    """Structured transactions from statement text. A real monthly statement has 100+ lines —
    far more JSON than one small-model call can emit — so prefer the CLOUD route (a once-a-month
    batch where accuracy matters) and process the text in CHUNKS so no output is truncated."""
    import litellm
    from core.llm_router import resolve_generation_route, escalate_to_cloud

    if not statement_text.strip():
        return []
    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="bank_statement")
    esc = escalate_to_cloud("bank_statement", task_type="bank_statement")
    if esc:
        model, api_key, api_base = esc

    # Chunk on line boundaries (~9k chars ≈ 40-60 transactions per call).
    lines = statement_text[:120000].splitlines()
    chunks, cur, size = [], [], 0
    for ln in lines:
        cur.append(ln)
        size += len(ln) + 1
        if size > 9000:
            chunks.append("\n".join(cur))
            cur, size = [], 0
    if cur:
        chunks.append("\n".join(cur))

    arr: List[Any] = []
    for chunk in chunks[:12]:
        try:
            resp = await litellm.acompletion(
                model=model,
                messages=[{"role": "system", "content": _TXN_SYSTEM},
                          {"role": "user", "content": chunk}],
                temperature=0, max_tokens=8000, api_key=api_key, api_base=api_base,
            )
            raw = resp.choices[0].message.content or ""
        except Exception as exc:
            log.warning("statement extraction failed: %s", exc)
            continue
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j < 0:
            continue
        try:
            part = json.loads(raw[i:j + 1])
        except Exception:
            try:
                import json_repair
                part = json_repair.loads(raw[i:j + 1])
            except Exception:
                continue
        if isinstance(part, list):
            arr.extend(part)

    out = []
    for t in arr if isinstance(arr, list) else []:
        if not isinstance(t, dict):
            continue
        try:
            amt = int(float(t.get("amount_cents") or 0))
        except Exception:
            continue
        if amt == 0:
            continue        # models often emit debits NEGATIVE despite the prompt — keep them
        direction = str(t.get("direction") or "").lower()
        if not direction.startswith(("in", "out")):
            direction = "out" if amt < 0 else "in"
        out.append({
            "date": (t.get("date") or None),
            "description": (t.get("description") or "").strip()[:300],
            "amount_cents": abs(amt),
            "direction": "out" if direction.startswith("out") else "in",
            "balance_cents": int(float(t["balance_cents"])) if t.get("balance_cents") is not None else None,
            "reference": (t.get("reference") or None),
        })
    return out


# ── Extraction quality (2026-08 accuracy audit) ────────────────────────────────
# A bank statement has no single "total" the way an invoice does (extraction_quality.py's
# line-sum-vs-stated-total check doesn't apply), but SA statements typically show a running
# balance per line — the same "don't trust the model's confidence, check the arithmetic"
# discipline can cross-check that instead: each line's balance should equal the previous
# line's balance plus/minus this transaction's amount.

def reconciliation_ok(txns: List[Dict[str, Any]]) -> bool:
    """False only on a genuine mismatch between consecutive extracted running balances — never
    penalises a statement format that doesn't show a running balance at all (nothing to check
    there, so it passes)."""
    prev_balance = None
    for t in txns:
        bal = t.get("balance_cents")
        if bal is None:
            prev_balance = None  # gap in the running balance — can't compare across it
            continue
        if prev_balance is not None:
            expected = (prev_balance + t["amount_cents"] if t["direction"] == "in"
                        else prev_balance - t["amount_cents"])
            if abs(expected - bal) > 100:  # R1 tolerance (fee-ordering/rounding quirks)
                return False
        prev_balance = bal
    return True


# ── Reconciliation ────────────────────────────────────────────────────────────

def _tok(s: str) -> set:
    return set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))


def _match_by_amount(txn: Dict[str, Any], candidates: List[dict], amount_key: str,
                     name_fields: tuple, require_name: bool = False) -> Optional[dict]:
    """Confident match of a credit to an outstanding invoice/order by amount (+ name/reference
    boost). Shared by _match_invoice and _match_order — same tolerance + ambiguity rules.

    require_name makes name/reference evidence MANDATORY rather than a score boost. Amount alone
    is reasonable evidence for money IN — a credit of exactly R1,152.93 against the single
    outstanding invoice for R1,152.93 is specific. It is not reasonable for money OUT against
    supplier bills, where payments are round (R400, R1,500) and dozens of open bills compete.
    Measured against production 2026-09-06: matching the existing backlog on amount alone paired
    "The Crazy Store R99.90" with a Pick n Pay bill, "Vida e Caffe R98" with Yoco, and
    "Int On Debit Balance R0.11" with SOLID CAPE — 66 such matches across two tenants, nearly
    all wrong, each of which would have marked a real supplier bill paid and posted a payables
    entry for it.
    """
    amt = txn["amount_cents"]
    tol = max(100, int(amt * 0.01))          # R1 or 1%
    blob = _tok(txn.get("description")) | _tok(txn.get("reference"))
    best, best_score = None, 0
    for c in candidates:
        if abs(int(c.get(amount_key) or 0) - amt) > tol:
            continue
        score = 2                            # exact-ish amount is the anchor
        name_toks = set()
        for f in name_fields:
            name_toks |= _tok(c.get(f))
        if blob & name_toks:
            score += 3                       # name/number appears in the txn reference
        if score > best_score:
            best, best_score = c, score
    # Require amount match; if multiple candidates share the amount and none name-matched, ambiguous.
    if best is None:
        return None
    if require_name and best_score < 5:
        return None                          # amount alone is not evidence for this side
    same_amt = [c for c in candidates if abs(int(c.get(amount_key) or 0) - amt) <= tol]
    if len(same_amt) > 1 and best_score < 5:
        return None                          # ambiguous → leave for review
    return best


def _match_invoice(txn: Dict[str, Any], invoices: List[dict]) -> Optional[dict]:
    return _match_by_amount(txn, invoices, "total_cents", ("customer_name", "invoice_number"))


def _match_order(txn: Dict[str, Any], orders: List[dict]) -> Optional[dict]:
    return _match_by_amount(txn, orders, "total_cents", ("customer_name", "display_id"))


def _match_supplier_bill(txn: Dict[str, Any], bills: List[dict]) -> Optional[dict]:
    """An outgoing payment against a supplier's invoice.

    2026-09-03: money OUT was matched against casual labour and card expenses, but never against
    inbound supplier INVOICES — so paying a supplier confirmed the money left the bank while the
    bill stayed 'draft' forever. That is why off-the-hook showed R80,542.72 "still owed",
    including bills from 2020-2023 that were certainly settled, and why a payables report could
    not be trusted. Same amount-anchored matching as the money-in side, keyed on the supplier's
    name instead of the customer's.
    """
    return _match_by_amount(txn, bills, "total_cents", ("supplier", "invoice_number"),
                            require_name=True)


def _digits(phone: Optional[str]) -> str:
    n = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "27" + n[1:] if n.startswith("0") else n


def propose_pop_match(tenant_id: str, amount_cents: int, reference: Optional[str] = None,
                      payee: Optional[str] = None, sender_phone: Optional[str] = None) -> Optional[tuple]:
    """Best-guess candidate for a WhatsApp-sent proof-of-payment screenshot. Returns
    ("invoice"|"order", candidate_row) or None — never applied automatically here; a screenshot
    is easier to fake/misread than a real bank statement, so unlike reconcile()'s confident-match
    auto-apply, this is always just a proposal for the owner to confirm.

    When sender_phone is known (a customer messaging about their OWN order/invoice — the common
    case when this is sent by the tenant's storefront line, not the owner), that customer's own
    outstanding invoices/orders are tried FIRST, by amount alone (no name/reference boost
    needed — the phone match already identifies who it is). This is a materially stronger signal
    than the generic tenant-wide amount+name matcher a bank-statement line has to fall back on,
    since a bank statement never knows which customer paid. Falls back to the generic matcher
    (across every outstanding invoice/order) when there's no sender phone or no match by phone."""
    db = _client()
    try:
        invoices = (db.table("commerce_invoices")
                    .select("id,invoice_number,customer_name,customer_phone,total_cents,status,doc_type")
                    .eq("tenant_id", tenant_id).in_("status", ["sent", "overdue"])
                    .limit(500).execute().data or [])
        invoices = [i for i in invoices if (i.get("doc_type") or "invoice") == "invoice"]
    except Exception:
        invoices = []
    try:
        orders = (db.table("commerce_orders")
                  .select("id,display_id,customer_name,customer_phone,total_cents")
                  .eq("tenant_id", tenant_id).eq("status", "pending_payment")
                  .limit(500).execute().data or [])
    except Exception:
        orders = []

    if sender_phone:
        sender = _digits(sender_phone)
        own_invoices = [i for i in invoices if _digits(i.get("customer_phone")) == sender]
        own_orders = [o for o in orders if _digits(o.get("customer_phone")) == sender]
        own_amount_matches_inv = [i for i in own_invoices if int(i.get("total_cents") or 0) == amount_cents]
        if len(own_amount_matches_inv) == 1:
            return ("invoice", own_amount_matches_inv[0])
        own_amount_matches_ord = [o for o in own_orders if int(o.get("total_cents") or 0) == amount_cents]
        if len(own_amount_matches_ord) == 1:
            return ("order", own_amount_matches_ord[0])
        # More than one of the customer's own open items share this amount — still worth
        # narrowing the generic search to just their own items rather than the whole tenant.
        if own_invoices or own_orders:
            invoices, orders = own_invoices, own_orders

    txn = {"amount_cents": amount_cents, "description": payee or "", "reference": reference or ""}
    im = _match_invoice(txn, invoices)
    if im:
        return ("invoice", im)
    om = _match_order(txn, orders)
    if om:
        return ("order", om)
    return None


def _known_supplier_names(tenant_id: str) -> List[str]:
    try:
        rows = (_client().table("commerce_suppliers").select("name")
                .eq("tenant_id", tenant_id).limit(500).execute().data or [])
    except Exception as exc:
        log.debug("supplier-name load skipped for %s: %s", tenant_id, exc)
        return []
    return [r.get("name") or "" for r in rows]


def _open_supplier_bills(tenant_id: str) -> List[dict]:
    """Bills still owed — the same draft-inclusive load reconcile() uses, since that is the
    status a scanned supplier invoice arrives in."""
    try:
        bills = (_client().table("commerce_invoices")
                 .select("id,invoice_number,supplier,total_cents,status,doc_type,direction")
                 .eq("tenant_id", tenant_id).eq("direction", "inbound")
                 .in_("status", ["draft", "sent", "overdue", "part_paid"])
                 .limit(500).execute().data or [])
    except Exception as exc:
        log.debug("supplier-bill load skipped for %s: %s", tenant_id, exc)
        return []
    return [b for b in bills if (b.get("doc_type") or "invoice") == "invoice"]


def classify_pop_direction(tenant_id: str, payee: Optional[str]) -> tuple:
    """Which way did the money move? Returns (direction, confident, reason).

    2026-09-03: this path hardcoded direction="in", so a proof of payment the OWNER sent for a
    bill THEY paid was staged as a customer paying them, and matched against their own
    outstanding invoices. The PDF path already got this right (see _SUPPLIER_CHECK_FIELD in
    vula/api/whatsapp.py, added for DIGG's real FNB payment notifications); the photo path
    did not, and a photographed POP is the common case for an owner on the move.

    The payee — who RECEIVED the money — settles it, so this reuses the same issuer test
    service.classify_direction uses for documents rather than inventing a second rule:
    paid to us is money in, paid to a supplier we know is money out. Anything unrecognised
    stays 'in' (the prior behaviour) but unconfident, so the review question still gets asked
    instead of a guess being applied."""
    from vula.commerce.service import _same_business
    payee = (payee or "").strip()
    if not payee:
        return "in", False, "no payee on the document"
    try:
        from vula.api.tenants import get_config as _get_tenant_config
        tenant_name = (_get_tenant_config(tenant_id) or {}).get("display_name") or tenant_id
    except Exception:
        tenant_name = tenant_id
    if _same_business(payee, tenant_name) or _same_business(payee, tenant_id):
        return "in", True, "paid to this business"
    for name in _known_supplier_names(tenant_id):
        if name and _same_business(payee, name):
            return "out", True, "paid to a known supplier"
    if any(_same_business(payee, b.get("supplier") or "") for b in _open_supplier_bills(tenant_id)):
        return "out", True, "paid to a supplier we owe"
    return "in", False, "unrecognised payee"


def _stage_supplier_pop(tenant_id: str, amount_cents: int, txn_date: Optional[str],
                        reference: Optional[str], payee: Optional[str]) -> str:
    """The owner paid a supplier and sent the confirmation. Stage it as money OUT against a
    bill, and ask before settling it — same never-auto-apply rule as the money-in side."""
    bills = _open_supplier_bills(tenant_id)
    cand = _match_supplier_bill(
        {"amount_cents": amount_cents, "description": payee or "", "reference": reference or ""},
        bills)
    row = {
        "tenant_id": tenant_id,
        "txn_date": (txn_date or _now())[:10],
        "description": "WhatsApp proof of payment" + (f" — {payee}" if payee else ""),
        "amount_cents": amount_cents,
        "direction": "out",
        "reference": reference,
        "match_status": "asked",
        "asked_at": _now(),
        "source_file": "whatsapp_pop",
        "proposed_match_type": "supplier_bill" if cand else None,
        "proposed_match_id": (cand or {}).get("id"),
    }
    try:
        _client().table("commerce_bank_transactions").insert(row).execute()
    except Exception as exc:
        log.debug("supplier pop staging insert failed (run migration 132?): %s", exc)

    amt = amount_cents / 100
    if cand:
        return (f"📸 Got the payment confirmation — R{amt:,.2f} to *{payee}*. That looks like "
                f"bill *{cand.get('invoice_number') or cand.get('id')}*. Reply *yes* to mark it "
                f"paid, or tell me the right bill number.")
    return (f"📸 Got the payment confirmation — R{amt:,.2f} to *{payee}*. I couldn't find a "
            f"matching open bill — reply with the bill number if you have it, or 'skip'.")


def stage_pop_for_review(tenant_id: str, amount_cents: int, txn_date: Optional[str] = None,
                         reference: Optional[str] = None, payee: Optional[str] = None,
                         sender_phone: Optional[str] = None) -> str:
    """Stage a WhatsApp proof-of-payment screenshot into the SAME interactive review flow
    bank_review.py already uses for an unmatched bank-statement credit — never auto-mark-paid.
    Returns the WhatsApp reply to send: a specific "does this match X — reply yes" proposal when
    a confident candidate is found, otherwise the same open "which order is this for" question
    the statement-sourced flow already asks. Marks the row 'asked' immediately (rather than
    'unmatched') since we're sending the question right now, not waiting for a batched digest.

    sender_phone should be the WhatsApp sender's number whenever known (both a customer texting
    the storefront line and an owner forwarding a screenshot have a real sender number) — it
    scopes matching to that person's own open invoices/orders first, see propose_pop_match.

    A POP the owner sent for a bill THEY paid moves money the other way — see
    classify_pop_direction — and is staged against supplier bills instead."""
    direction, _confident, reason = classify_pop_direction(tenant_id, payee)
    if direction == "out":
        log.info("pop for %s staged as money out (%s): %s", tenant_id, reason, payee)
        return _stage_supplier_pop(tenant_id, amount_cents, txn_date, reference, payee)
    candidate = propose_pop_match(tenant_id, amount_cents, reference, payee, sender_phone)
    match_type, cand = candidate if candidate else (None, None)
    row = {
        "tenant_id": tenant_id,
        "txn_date": (txn_date or _now())[:10],
        "description": "WhatsApp proof of payment" + (f" — {payee}" if payee else ""),
        "amount_cents": amount_cents,
        "direction": "in",
        "reference": reference,
        "match_status": "asked",
        "asked_at": _now(),
        "source_file": "whatsapp_pop",
        "proposed_match_type": match_type,
        "proposed_match_id": (cand or {}).get("id"),
    }
    try:
        _client().table("commerce_bank_transactions").insert(row).execute()
    except Exception as exc:
        log.debug("pop staging insert failed (run migration 132?): %s", exc)

    amt = amount_cents / 100
    # Neither us nor a supplier we know, and nothing matched on the money-in side either. The
    # real case: DIGG's "Mr Onito Tiler" — a subcontractor paid ad hoc, so no supplier record
    # and no bill to match. Asking "which order is this for?" is the wrong question, and it sat
    # unanswered for weeks. Ask which WAY the money went instead; "I paid" flips it to a bill.
    if not _confident and payee and not match_type:
        return (f"📸 Got the payment confirmation for R{amt:,.2f} — *{payee}*. Did you pay "
                f"them, or is this someone paying you? Reply *I paid* if it went out, or "
                f"the order/invoice number if it came in.")
    if match_type == "invoice":
        return (f"📸 Got your payment screenshot — R{amt:,.2f}. Looks like it matches invoice "
                f"*{cand.get('invoice_number')}* ({cand.get('customer_name') or 'customer'}). "
                f"Reply *yes* to confirm, or tell me the right order/invoice number.")
    if match_type == "order":
        return (f"📸 Got your payment screenshot — R{amt:,.2f}. Looks like it matches order "
                f"*{cand.get('display_id')}* ({cand.get('customer_name') or 'customer'}). "
                f"Reply *yes* to confirm, or tell me the right order/invoice number.")
    return (f"📸 Got your payment screenshot for R{amt:,.2f} — which order or invoice is this "
            f"for? Reply with the number (e.g. OFF-00006) or the customer's name.")


async def reconcile(tenant_id: str, txns: List[Dict[str, Any]], source_file: str = "",
                    auto_settle: bool = True) -> Dict[str, Any]:
    """Persist transactions and reconcile: credits → mark matching invoices paid; debits → expenses.
    Each transaction is also allocated to a chart-of-accounts category (learned/AI) with VAT.

    auto_settle=False persists and categorises the transactions but marks NOTHING paid (no
    invoice/order/supplier-bill/card-expense status changes, no customer notifications) — used
    when the statement's running balances don't reconcile, i.e. at least one amount was likely
    misread, so a "match" could settle the wrong invoice and message the wrong customer."""
    from vula.commerce import service, accounting
    db = _client()

    # Chart of accounts + VAT status for allocation.
    accounts = accounting.ensure_chart(tenant_id)
    acc_map = {a["code"]: a for a in accounts}
    try:
        vat_reg = bool((await service.get_invoice_settings(tenant_id) or {}).get("vat_registered", True))
    except Exception:
        vat_reg = True

    # Outstanding invoices to match credits against.
    try:
        invoices = (db.table("commerce_invoices")
                    .select("id,invoice_number,customer_name,total_cents,status,doc_type")
                    .eq("tenant_id", tenant_id).in_("status", ["sent", "overdue"])
                    .limit(2000).execute().data or [])
        invoices = [i for i in invoices if (i.get("doc_type") or "invoice") == "invoice"]
    except Exception as exc:
        log.debug("reconcile: invoice load skipped: %s", exc)
        invoices = []

    # Supplier bills still owed — the money-OUT counterpart of the list above. Loaded separately
    # because they sit in 'draft' (that is how a scanned supplier invoice arrives), so the
    # sent/overdue filter above would never see them. 2026-09-03: without this, paying a
    # supplier confirmed the money left the bank while the bill stayed unpaid forever.
    try:
        supplier_bills = (db.table("commerce_invoices")
                          .select("id,invoice_number,supplier,total_cents,status,doc_type,"
                                  "direction,vat_cents,paid_at,project")
                          .eq("tenant_id", tenant_id).eq("direction", "inbound")
                          .in_("status", ["draft", "sent", "overdue", "part_paid"])
                          .limit(2000).execute().data or [])
        supplier_bills = [b for b in supplier_bills
                          if (b.get("doc_type") or "invoice") == "invoice"]
    except Exception as exc:
        log.debug("reconcile: supplier-bill load skipped: %s", exc)
        supplier_bills = []

    # Orders awaiting an EFT payment (checkout has always offered EFT as a payment method,
    # alongside online/COD) — a customer's proof-of-payment email needs to reconcile against
    # THESE too, not just invoices.
    try:
        orders = (db.table("commerce_orders")
                  .select("id,display_id,customer_name,customer_phone,total_cents,status")
                  .eq("tenant_id", tenant_id).eq("status", "pending_payment")
                  .limit(2000).execute().data or [])
    except Exception as exc:
        log.debug("reconcile: order load skipped: %s", exc)
        orders = []

    # Card-machine settlement summaries (whatsapp._analyze_document "Settlement Statement"):
    # their net payout is what lands in the bank, so an otherwise unexplained credit of exactly
    # that amount a few days later is card sales. Allocation only — nothing is marked paid, and
    # a settlement whose figures weren't found in its own text is never used.
    settlements = _load_settlements(db, tenant_id)
    used_settlements: set = set()

    # Casual-labour workers to recognise payments to (by bank account / name).
    from vula.commerce import labour
    workers = labour.list_workers(tenant_id)

    # Open company-card expense claims: a statement debit that matches one IS that purchase —
    # link them (single source of truth, no double-count) and let the RECEIPT's category win
    # (the slip knows what was bought; the statement line usually doesn't).
    try:
        card_exps = (db.table("commerce_expenses")
                     .select("id,date,amount_cents,account_code,supplier")
                     .eq("tenant_id", tenant_id).eq("paid_with", "company_card")
                     .in_("status", ["submitted", "approved"]).limit(1000).execute().data or [])
    except Exception:
        card_exps = []
    used_exp_ids: set = set()

    def _match_card_expense(t: Dict[str, Any]) -> Optional[dict]:
        from datetime import date as _date
        best = None
        for e in card_exps:
            if e["id"] in used_exp_ids:
                continue
            if abs(int(e.get("amount_cents") or 0) - t["amount_cents"]) > max(100, t["amount_cents"] // 100):
                continue
            try:
                d1 = _date.fromisoformat(str(t.get("date"))[:10])
                d2 = _date.fromisoformat(str(e.get("date"))[:10])
                if abs((d1 - d2).days) > 4:
                    continue
            except Exception:
                pass
            best = e
            break
        return best

    # Re-ingesting the same statement must NEVER clobber human work: rows the owner (or a
    # receipt/worker match) already allocated keep their allocation on upsert.
    protected: Dict[tuple, dict] = {}
    try:
        for r in (db.table("commerce_bank_transactions")
                  .select("txn_date,amount_cents,description,account_code,vat_cents,vat_treatment,categorized_by,project,trade")
                  .eq("tenant_id", tenant_id)
                  # 'merchant' joins these because it reflects a decided merchant profile —
                  # often the owner's own once-per-merchant answer — and must survive a
                  # statement being re-uploaded.
                  .in_("categorized_by",
                       ["owner", "receipt", "labour", "asked", "skipped", "merchant"])
                  .limit(2000).execute().data or []):
            protected[(str(r.get("txn_date")), int(r.get("amount_cents") or 0),
                       (r.get("description") or ""))] = r
    except Exception:
        pass

    # Owner allocations learned from earlier statements (project + trade per counterparty).
    from vula.commerce import allocation
    alloc_rules = allocation.load_rules(tenant_id)
    canon: Dict[str, Optional[str]] = {}

    # Categorise the whole statement up front — batched cloud calls with direction-aware
    # account lists (fast + consistent; per-line small-model calls misfiled suppliers).
    batch_cats = await accounting.categorize_batch(tenant_id, txns, accounts)

    matched_invoices, unmatched_in, unmatched_out, saved, matched_workers, needs_input = 0, 0, 0, 0, 0, 0
    matched_expenses, matched_orders, matched_bills, card_settlements = 0, 0, 0, 0
    used_invoice_ids: set = set()
    used_order_ids: set = set()
    used_bill_ids: set = set()
    for idx, t in enumerate(txns):
        status, inv_id, order_id, worker_id, wk_project, exp_id = "unmatched", None, None, None, None, None
        if not auto_settle:
            if t["direction"] == "in":
                unmatched_in += 1
            else:
                w = labour.match_worker(t, workers)
                if w:
                    worker_id, wk_project, status = w["id"], w.get("default_project"), "matched"
                    matched_workers += 1
                else:
                    unmatched_out += 1
        elif t["direction"] == "in":
            candidates = [i for i in invoices if i["id"] not in used_invoice_ids]
            m = _match_invoice(t, candidates)
            if m:
                try:
                    await service.update_invoice_status(tenant_id, m["id"], "paid")
                    inv_id, status = m["id"], "matched"
                    used_invoice_ids.add(m["id"])
                    matched_invoices += 1
                except Exception as exc:
                    log.warning("mark invoice paid failed: %s", exc)
            else:
                order_candidates = [o for o in orders if o["id"] not in used_order_ids]
                om = _match_order(t, order_candidates)
                if om:
                    try:
                        await service.update_order_status(om["id"], "paid")
                        order_id, status = om["id"], "matched"
                        used_order_ids.add(om["id"])
                        matched_orders += 1
                        from vula.api.yoco import _notify_order_paid
                        await _notify_order_paid(
                            tenant_id, om.get("display_id") or om["id"], om["id"],
                            om.get("customer_phone"), om.get("customer_name") or "",
                            int(om.get("total_cents") or 0))
                    except Exception as exc:
                        log.warning("mark order paid failed: %s", exc)
                else:
                    unmatched_in += 1
        else:
            w = labour.match_worker(t, workers)
            if w:
                worker_id, wk_project, status = w["id"], w.get("default_project"), "matched"
                matched_workers += 1
            else:
                exp = _match_card_expense(t)
                if exp:
                    exp_id, status = exp["id"], "matched"
                    used_exp_ids.add(exp["id"])
                    matched_expenses += 1
                    try:     # the statement confirms the card purchase actually cleared
                        db.table("commerce_expenses").update(
                            {"status": "paid", "updated_at": _now()}).eq("id", exp["id"]).execute()
                    except Exception:
                        pass
                else:
                    # Last fallback: an outgoing payment settling a SUPPLIER'S INVOICE. Placed
                    # after worker and card-expense matching so nothing that already matches
                    # changes behaviour — this only catches what previously fell straight to
                    # unmatched_out, leaving the bill owed forever.
                    bill_candidates = [b for b in supplier_bills if b["id"] not in used_bill_ids]
                    bm = _match_supplier_bill(t, bill_candidates)
                    if bm:
                        try:
                            await service.update_invoice_status(tenant_id, bm["id"], "paid")
                            inv_id, status = bm["id"], "matched"
                            # the payment is for the bill's project (job costing)
                            wk_project = wk_project or bm.get("project")
                            used_bill_ids.add(bm["id"])
                            matched_bills += 1
                        except Exception as exc:
                            log.warning("mark supplier bill paid failed: %s", exc)
                            unmatched_out += 1
                    else:
                        unmatched_out += 1

        # Allocate to a chart-of-accounts category + VAT. Invoice-matched credits → sales;
        # worker-matched debits → casual labour (and learn the mapping); expense-matched
        # debits → the RECEIPT's account.
        code, cat_src = None, "matched"
        if t["direction"] == "in" and (inv_id or order_id) and "sales" in acc_map:
            code = "sales"
        elif worker_id and "casual_labour" in acc_map:
            code, cat_src = "casual_labour", "labour"
            accounting.learn_category_rule(tenant_id, t, "casual_labour")
        elif exp_id:
            e = next((x for x in card_exps if x["id"] == exp_id), None)
            if e and e.get("account_code") in acc_map:
                code, cat_src = e["account_code"], "receipt"
                accounting.learn_category_rule(tenant_id, t, code)
        if (not code and t["direction"] == "in" and not (inv_id or order_id)
                and "sales" in acc_map):
            st = _match_settlement(t, [x for x in settlements if x["id"] not in used_settlements])
            if st:
                code, cat_src = "sales", "settlement"
                used_settlements.add(st["id"])
                card_settlements += 1
                unmatched_in = max(0, unmatched_in - 1)
        own_wages = (t["direction"] == "out" and not worker_id
                     and allocation.is_own_wages(tenant_id, t.get("description")))
        if not code and own_wages and "wages" in acc_map:
            code, cat_src = "wages", "rule"      # a running cost shared across the projects
        if not code:
            cat = batch_cats[idx]
            code, cat_src = cat["account_code"], cat["source"]
        # A previously human-allocated row keeps its allocation (re-upload safety).
        prior = protected.get((str(t.get("date")), t["amount_cents"], t.get("description") or ""))
        if prior:
            code = prior.get("account_code") or code
            cat_src = prior.get("categorized_by") or cat_src

        if cat_src == "default":
            needs_input += 1          # Vula wasn't sure — ask the owner to allocate
        vat_cents = (int(prior.get("vat_cents") or 0) if prior
                     else accounting.vat_for(acc_map.get(code), t["amount_cents"], vat_reg))

        row = {
            "tenant_id": tenant_id, "txn_date": t.get("date"), "description": t.get("description") or "",
            "amount_cents": t["amount_cents"], "direction": t["direction"],
            "balance_cents": t.get("balance_cents"), "reference": t.get("reference"),
            "matched_invoice_id": inv_id, "matched_expense_id": exp_id, "matched_order_id": order_id,
            "match_status": status, "source_file": source_file,
            "account_code": code, "vat_cents": vat_cents,
            "vat_treatment": (acc_map.get(code) or {}).get("vat_treatment"), "categorized_by": cat_src,
            "worker_id": worker_id, "project": wk_project,
        }
        # Project/trade: an owner's earlier allocation of this very line wins, then the
        # worker's default project, then a clear learned rule (vula/commerce/allocation.py).
        s_project, s_trade = ((None, None) if own_wages
                              else allocation.suggest(alloc_rules, t.get("description"), None, tenant_id))
        project = (prior or {}).get("project") or wk_project or s_project
        trade = (prior or {}).get("trade") or s_trade
        if project:
            if project not in canon:
                from vula.commerce.service import canonical_project
                canon[project] = canonical_project(tenant_id, project)
            row["project"] = canon[project]
        if trade:
            row["trade"] = trade
        try:
            db.table("commerce_bank_transactions").upsert(
                row, on_conflict="tenant_id,txn_date,amount_cents,description").execute()
            saved += 1
        except Exception:
            # 058/059/074 columns may not exist yet (account_code/vat/worker_id/project/matched_order_id)
            # — retry with core columns only.
            for k in ("account_code", "vat_cents", "vat_treatment", "categorized_by", "worker_id",
                     "project", "matched_order_id", "trade"):
                row.pop(k, None)
            try:
                db.table("commerce_bank_transactions").upsert(
                    row, on_conflict="tenant_id,txn_date,amount_cents,description").execute()
                saved += 1
            except Exception as exc2:
                log.debug("bank txn upsert skipped (run migration 057?): %s", exc2)

    return {"parsed": len(txns), "saved": saved, "auto_settled": auto_settle,
            "matched_invoices": matched_invoices,
            "matched_orders": matched_orders,
            "matched_workers": matched_workers, "matched_expenses": matched_expenses,
            "needs_input": needs_input, "card_settlements": card_settlements,
            "unmatched_credits": unmatched_in, "unmatched_debits": unmatched_out}


def _load_settlements(db, tenant_id: str) -> List[Dict[str, Any]]:
    """Filed settlement summaries from the last ~4 months with a verified net payout."""
    from datetime import date, timedelta
    try:
        rows = (db.table("vula_filed_documents").select("id,fields,created_at")
                .eq("tenant_id", tenant_id).eq("category", "Settlement Statement")
                .gte("created_at", (date.today() - timedelta(days=120)).isoformat())
                .limit(1000).execute().data or [])
    except Exception as exc:
        log.debug("reconcile: settlement load skipped: %s", exc)
        return []
    out = []
    for r in rows:
        f = r.get("fields") or {}
        if f.get("_unverified_figures"):
            continue
        try:
            net = int(f.get("net_cents"))
        except (TypeError, ValueError):
            continue
        if net > 0 and f.get("date"):
            out.append({"id": r["id"], "net_cents": net, "date": str(f["date"])[:10],
                        "provider": f.get("provider")})
    return out


def _match_settlement(t: Dict[str, Any], settlements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The settlement whose net payout equals this credit, dated on or up to 5 days before it.
    Only a single candidate counts — two identical payouts in the window are left for the owner."""
    from datetime import date as _date
    try:
        td = _date.fromisoformat(str(t.get("date"))[:10])
    except ValueError:
        return None
    hits = []
    for s_ in settlements:
        if s_["net_cents"] != int(t.get("amount_cents") or 0):
            continue
        try:
            gap = (td - _date.fromisoformat(s_["date"])).days
        except ValueError:
            continue
        if 0 <= gap <= 5:
            hits.append(s_)
    return hits[0] if len(hits) == 1 else None


_BANK_NAMES = re.compile(
    r"\b(capitec|first national bank|fnb|standard bank|absa|nedbank|investec|tyme ?bank|"
    r"discovery bank|african bank|bidvest bank|old mutual|bank zero)\b", re.IGNORECASE)
_BALANCE_WORDS = re.compile(r"\b(opening|closing) balance\b|balance brought forward|"
                            r"\bbank statement\b|\baccount statement\b", re.IGNORECASE)
_NOT_OUR_BANK = re.compile(
    r"accounts? receivable|statement of account|activity statement|customer statement|"
    r"\btax invoice\b|\binvoice (no|number|#)|\bamount due\b|\bageing\b|\baged\b|"
    r"\b(30|60|90|120)\s*days\b|remittance advice|\breceipt (no|number)\b", re.IGNORECASE)


# Said only by a supplier's statement of account (never by the bank about its own customer).
_SUPPLIER_ONLY = re.compile(
    r"accounts? receivable|statement of account|customer statement|activity statement|"
    r"\b(amount|balance|total) (now )?due\b|\bageing\b|\baged (analysis|balance)|remittance advice|"
    r"\bcurrent\s+30\s*days\b", re.IGNORECASE)
# The bank's own statement layout (FNB, Capitec, ABSA, Standard Bank, Nedbank all print these).
_BANK_LAYOUT = re.compile(
    r"statement period|universal branch code|transactions in rand|statement date\s*:|"
    r"account number\s*:?\s*\d{6,}", re.IGNORECASE)


def looks_like_own_bank_statement(text: str) -> bool:
    """Is this the business's OWN bank account statement — not a supplier's statement of
    account, an invoice or a receipt with the word "statement" on it? 2026-09-28: every emailed
    PDF with "statement" in its name/subject went through ingest_statement, so 121 of
    digg-demo's 372 "bank" lines came from suppliers' Accounts Receivable Statements, DOW004's
    statement, a SportyBet activity statement, invoices and receipts — R664k in and R538k out
    that never touched DIGG's bank. A real statement names the bank and shows balances; a
    supplier statement shows an amount due, ageing columns or invoice numbers."""
    head = (text or "")[:6000]
    # 2026-10-01: Solid Cape's "Accounts Receivable Statements" still got in by email — it
    # prints its own bank details (a bank name) and a balance, which the "bankish" exception
    # below took for a bank statement. Words only a supplier's statement uses now decide,
    # unless the page has the bank statement's own layout.
    if _SUPPLIER_ONLY.search(head) and not _BANK_LAYOUT.search(head):
        return False
    bankish = bool(_BANK_NAMES.search(head)) and bool(_BALANCE_WORDS.search(head))
    if _NOT_OUR_BANK.search(head) and not bankish:
        return False
    return bool(_BANK_NAMES.search(head) or _BALANCE_WORDS.search(head))


_FEE_DESC = re.compile(r"^\s*(bank charges?|service fees?|other fees?|monthly (account )?fees?|fees?)\s*$",
                       re.IGNORECASE)


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (s or "").lower())
            if w not in {"fnb", "app", "payment", "pmt", "rtc", "account", "off", "purchase", "pos",
                         "send", "money", "digg", "bank", "charge"}}


def supersede_misread(tenant_id: str, txns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A statement that reconciled to the cent replaces what an earlier, unverified read of the
    same days put in the books. The upsert key includes the amount, so a misread amount (R8,200
    for R82,000) would otherwise stay next to the corrected line. Only rows that are plainly the
    same transaction are set aside (match_status 'ignored', reversible):
      - same day and direction, sharing a payee word with a verified line, an amount the verified
        statement doesn't have on that day;
      - a separate "… bank charge" line (FNB's accrued-charge column misread as a debit).
    Rows from a different account (no shared words) and anything already matched to an invoice,
    order or expense are left alone. An owner's project/trade on a set-aside row moves to the
    verified line."""
    if not txns:
        return {"superseded": 0}
    days = sorted({t["date"] for t in txns})
    on_day: Dict[tuple, List[Dict[str, Any]]] = {}
    for t in txns:
        on_day.setdefault((t["date"], t["direction"]), []).append(t)
    db = _client()
    try:
        rows = (db.table("commerce_bank_transactions")
                .select("id,txn_date,description,amount_cents,direction,match_status,project,trade,"
                        "matched_invoice_id,matched_order_id,matched_expense_id")
                .eq("tenant_id", tenant_id).gte("txn_date", days[0]).lte("txn_date", days[-1])
                .execute().data or [])
    except Exception as exc:
        log.debug("supersede read skipped: %s", exc)
        return {"superseded": 0}
    from collections import Counter
    verified = Counter((t["date"], t["direction"], t["amount_cents"]) for t in txns)
    exact = {(t["date"], t["direction"], t["amount_cents"], t["description"]) for t in txns}
    live = [r for r in rows if r.get("match_status") != "ignored"]
    # The same line read twice in different words (same day, direction, amount): keep as many
    # rows as the statement has — the verified wording, then anything matched/allocated first.
    keep_ids = set()
    by_key: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in live:
        k = (str(r.get("txn_date"))[:10], r.get("direction"), int(r.get("amount_cents") or 0))
        by_key.setdefault(k, []).append(r)
    for k, rs in by_key.items():
        if k not in verified:
            continue
        rs.sort(key=lambda r: ((*k, r.get("description") or "") not in exact,
                               not (r.get("matched_invoice_id") or r.get("matched_order_id")
                                    or r.get("matched_expense_id") or r.get("project"))))
        keep_ids.update(r["id"] for r in rs[:verified[k]])
    # more rows than the statement has for this day and amount: the extras are the same line
    # booked again (an earlier read's wording, a blank description) — however they're worded
    surplus = {r["id"] for rs in by_key.values() for r in rs
               if r["id"] not in keep_ids
               and (str(r.get("txn_date"))[:10], r.get("direction"), int(r.get("amount_cents") or 0)) in verified}
    out, carried = [], 0
    for r in live:
        d, direction = str(r.get("txn_date"))[:10], r.get("direction")
        if r["id"] in keep_ids:
            continue
        if r.get("matched_invoice_id") or r.get("matched_order_id") or r.get("matched_expense_id"):
            continue
        same = on_day.get((d, direction)) or []
        desc = r.get("description") or ""
        # "… bank charge" (the accrued-charge column misread as a debit), or a fee line read in
        # other words ("Bank Charges", "Service Fees", "Other Fees") on a day the verified
        # statement has its own charge lines
        fee_line = desc.lower().endswith("bank charge") or (
            _FEE_DESC.match(desc) and any(t["description"].startswith("FNB bank charges") for t in same))
        twin = next((t for t in same if _words(t["description"]) & _words(desc)), None)
        if not (fee_line or twin or r["id"] in surplus):
            continue
        out.append(r["id"])
        if twin and (r.get("project") or r.get("trade")):
            try:
                db.table("commerce_bank_transactions").update(
                    {k: r[k] for k in ("project", "trade") if r.get(k)}
                ).eq("tenant_id", tenant_id).eq("txn_date", d).eq("amount_cents", twin["amount_cents"]
                ).eq("description", twin["description"]).is_("project", "null").execute()
                carried += 1
            except Exception as exc:
                log.debug("allocation carry-over skipped: %s", exc)
    for i in range(0, len(out), 100):
        try:
            db.table("commerce_bank_transactions").update({"match_status": "ignored"}).eq(
                "tenant_id", tenant_id).in_("id", out[i:i + 100]).execute()
        except Exception as exc:
            log.warning("supersede update failed: %s", exc)
            return {"superseded": 0}
    if out:
        log.info("%s: verified statement %s–%s set aside %d misread line(s)",
                 tenant_id, days[0], days[-1], len(out))
    return {"superseded": len(out), "allocations_carried": carried}


async def ingest_statement(tenant_id: str, pdf_path: Path, password: Optional[str] = None,
                           source_file: str = "", trusted: bool = False) -> Dict[str, Any]:
    """Full pipeline: decrypt (stored ID password if not given) → parse → reconcile.
    `trusted`: the owner uploaded it as their bank statement (dashboard) — skip the check that
    an automatically-detected PDF (email/WhatsApp) really is their own bank statement.

    An FNB statement is read deterministically first (vula/ingestion/fnb_statement.py) and only
    accepted when every running balance reconciles; anything else goes to the LLM as before."""
    pwd = password if password is not None else get_statement_password(tenant_id)
    exact = None
    try:
        from vula.ingestion import fnb_statement
        exact = fnb_statement.parse(fnb_statement.pdf_text(Path(pdf_path), pwd))
    except Exception as exc:
        log.debug("deterministic statement read skipped: %s", exc)
    if exact and exact["transactions"]:
        txns = exact["transactions"]
        result = await reconcile(tenant_id, txns, source_file=source_file or Path(pdf_path).name,
                                 auto_settle=True)
        result.update(supersede_misread(tenant_id, txns))
        result["extraction_reconciled"] = True
        result["parser"] = "fnb"
        log.info("bank statement (FNB, exact) reconciled for %s: %s", tenant_id, result)
        return result
    try:
        text = extract_pdf_text(Path(pdf_path), pwd)
    except Exception as exc:
        return {"error": f"could not read statement (wrong password?): {exc}"}
    if not text.strip():
        return {"error": "no readable text in the statement (scanned image?)"}
    if not trusted and not looks_like_own_bank_statement(text):
        log.info("%s: %s is not the business's own bank statement — filed only, no bank lines",
                 tenant_id, source_file)
        return {"error": "not a bank statement of this business (supplier statement/invoice?)",
                "not_bank_statement": True}
    txns = await extract_transactions(text)
    if not txns:
        return {"error": "no transactions found in the statement"}
    reconciled = reconciliation_ok(txns)
    if not reconciled:
        log.warning("bank statement extraction quality check FAILED for %s (%s) — running "
                    "balances don't reconcile, at least one transaction was likely misread",
                    tenant_id, source_file)
    # A statement whose running balances don't add up has at least one misread amount — still
    # save and categorise it for review, but don't let it settle invoices/orders or message
    # customers "your payment was received" on the strength of a possibly-wrong figure.
    result = await reconcile(tenant_id, txns, source_file=source_file or Path(pdf_path).name,
                             auto_settle=reconciled)
    result["extraction_reconciled"] = reconciled
    log.info("bank statement reconciled for %s: %s", tenant_id, result)
    return result


# ── Single-document payment confirmations (POP) ────────────────────────────────
# A customer forwarding one bank "payment confirmation"/"proof of payment" for a single EFT is a
# different document shape from a weekly multi-transaction statement — one paragraph or receipt-
# style layout, not a table of rows — so it gets its own targeted extraction prompt rather than
# reusing the statement parser (which expects "every transaction line" and may see nothing to
# extract in prose).

_CONFIRMATION_SYSTEM = (
    "You read ONE bank payment confirmation / proof-of-payment document (not a multi-line "
    "statement). Extract the single payment as a JSON array with exactly one element, or an "
    "empty array [] if this isn't actually a payment confirmation. Return ONLY the JSON array, "
    "no prose or markdown. The element:\n"
    '{"date":"YYYY-MM-DD","description":string,"amount_cents":integer,'
    '"direction":"in|out","reference":string|null}\n'
    "Rules: amount_cents is the paid amount in cents (Rands×100). direction is 'in' if this "
    "confirms money the account HOLDER received/was paid, 'out' if it confirms a payment the "
    "account holder MADE to someone else (most customer-forwarded proofs-of-payment for an "
    "order/invoice are 'out' from the customer's bank, which is 'in' from the business's side — "
    "always answer from the RECEIVING business's perspective: a customer's proof of payment is "
    "'in'). reference is any invoice/order number, beneficiary reference, or payer name shown. "
    "Dates on South African documents are DD/MM/YYYY (day first — 17/07/2026 means 17 July 2026); "
    "convert to YYYY-MM-DD in your output."
)


async def extract_payment_confirmation(text: str) -> List[Dict[str, Any]]:
    """Single-transaction extraction for a payment-confirmation document. Reuses the same
    JSON-cleanup as extract_transactions but with a prompt suited to a one-payment document."""
    import litellm
    from core.llm_router import resolve_generation_route

    if not text.strip():
        return []
    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="bank_statement")
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "system", "content": _CONFIRMATION_SYSTEM},
                      {"role": "user", "content": text[:6000]}],
            temperature=0, max_tokens=500, api_key=api_key, api_base=api_base,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("payment confirmation extraction failed: %s", exc)
        return []
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    i, j = raw.find("["), raw.rfind("]")
    if i < 0 or j < 0:
        return []
    try:
        arr = json.loads(raw[i:j + 1])
    except Exception:
        try:
            import json_repair
            arr = json_repair.loads(raw[i:j + 1])
        except Exception:
            return []

    out = []
    for t in arr if isinstance(arr, list) else []:
        if not isinstance(t, dict):
            continue
        try:
            amt = int(float(t.get("amount_cents") or 0))
        except Exception:
            continue
        if amt <= 0:
            continue
        direction = str(t.get("direction") or "in").lower()
        # A malformed/unconverted date (e.g. DD/MM/YYYY slipping through) must never reach the
        # DB as a DATE column value — fall back to today rather than let the insert fail silently.
        raw_date = str(t.get("date") or "").strip()[:10]
        date = raw_date if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date) else None
        out.append({
            "date": date,
            "description": (t.get("description") or "").strip()[:300],
            "amount_cents": amt,
            "direction": "out" if direction.startswith("out") else "in",
            "balance_cents": None,
            "reference": t.get("reference") or None,
        })
    return out


async def ingest_payment_confirmation(tenant_id: str, pdf_path: Path,
                                      source_file: str = "") -> Dict[str, Any]:
    """Single-document pipeline for a POP/payment-confirmation email attachment: read (usually
    unencrypted, unlike a full statement) → extract the one payment → reconcile against
    outstanding invoices AND pending-EFT orders."""
    pwd = None
    try:
        text = extract_pdf_text(Path(pdf_path), pwd)
    except Exception:
        text = ""
    if not text.strip():
        # Some banks encrypt even a one-page confirmation with the same ID-number password
        # used for statements — worth one retry before giving up.
        stored = get_statement_password(tenant_id)
        if stored:
            try:
                text = extract_pdf_text(Path(pdf_path), stored)
            except Exception as exc:
                return {"error": f"could not read document: {exc}"}
    if not text.strip():
        return {"error": "no readable text (scanned image?)"}
    txns = await extract_payment_confirmation(text)
    if not txns:
        return {"error": "not recognised as a payment confirmation"}
    result = await reconcile(tenant_id, txns, source_file=source_file or Path(pdf_path).name)
    log.info("payment confirmation reconciled for %s: %s", tenant_id, result)
    return result
