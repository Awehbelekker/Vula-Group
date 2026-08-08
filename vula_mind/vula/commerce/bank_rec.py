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
                     name_fields: tuple) -> Optional[dict]:
    """Confident match of a credit to an outstanding invoice/order by amount (+ name/reference
    boost). Shared by _match_invoice and _match_order — same tolerance + ambiguity rules."""
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
    same_amt = [c for c in candidates if abs(int(c.get(amount_key) or 0) - amt) <= tol]
    if len(same_amt) > 1 and best_score < 5:
        return None                          # ambiguous → leave for review
    return best


def _match_invoice(txn: Dict[str, Any], invoices: List[dict]) -> Optional[dict]:
    return _match_by_amount(txn, invoices, "total_cents", ("customer_name", "invoice_number"))


def _match_order(txn: Dict[str, Any], orders: List[dict]) -> Optional[dict]:
    return _match_by_amount(txn, orders, "total_cents", ("customer_name", "display_id"))


async def reconcile(tenant_id: str, txns: List[Dict[str, Any]], source_file: str = "") -> Dict[str, Any]:
    """Persist transactions and reconcile: credits → mark matching invoices paid; debits → expenses.
    Each transaction is also allocated to a chart-of-accounts category (learned/AI) with VAT."""
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
                  .select("txn_date,amount_cents,description,account_code,vat_cents,vat_treatment,categorized_by")
                  .eq("tenant_id", tenant_id)
                  .in_("categorized_by", ["owner", "receipt", "labour", "asked", "skipped"])
                  .limit(2000).execute().data or []):
            protected[(str(r.get("txn_date")), int(r.get("amount_cents") or 0),
                       (r.get("description") or ""))] = r
    except Exception:
        pass

    # Categorise the whole statement up front — batched cloud calls with direction-aware
    # account lists (fast + consistent; per-line small-model calls misfiled suppliers).
    batch_cats = await accounting.categorize_batch(tenant_id, txns, accounts)

    matched_invoices, unmatched_in, unmatched_out, saved, matched_workers, needs_input = 0, 0, 0, 0, 0, 0
    matched_expenses, matched_orders = 0, 0
    used_invoice_ids: set = set()
    used_order_ids: set = set()
    for idx, t in enumerate(txns):
        status, inv_id, order_id, worker_id, wk_project, exp_id = "unmatched", None, None, None, None, None
        if t["direction"] == "in":
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
        try:
            db.table("commerce_bank_transactions").upsert(
                row, on_conflict="tenant_id,txn_date,amount_cents,description").execute()
            saved += 1
        except Exception:
            # 058/059/074 columns may not exist yet (account_code/vat/worker_id/project/matched_order_id)
            # — retry with core columns only.
            for k in ("account_code", "vat_cents", "vat_treatment", "categorized_by", "worker_id",
                     "project", "matched_order_id"):
                row.pop(k, None)
            try:
                db.table("commerce_bank_transactions").upsert(
                    row, on_conflict="tenant_id,txn_date,amount_cents,description").execute()
                saved += 1
            except Exception as exc2:
                log.debug("bank txn upsert skipped (run migration 057?): %s", exc2)

    return {"parsed": len(txns), "saved": saved, "matched_invoices": matched_invoices,
            "matched_orders": matched_orders,
            "matched_workers": matched_workers, "matched_expenses": matched_expenses,
            "needs_input": needs_input,
            "unmatched_credits": unmatched_in, "unmatched_debits": unmatched_out}


async def ingest_statement(tenant_id: str, pdf_path: Path, password: Optional[str] = None,
                           source_file: str = "") -> Dict[str, Any]:
    """Full pipeline: decrypt (stored ID password if not given) → parse → reconcile."""
    pwd = password if password is not None else get_statement_password(tenant_id)
    try:
        text = extract_pdf_text(Path(pdf_path), pwd)
    except Exception as exc:
        return {"error": f"could not read statement (wrong password?): {exc}"}
    if not text.strip():
        return {"error": "no readable text in the statement (scanned image?)"}
    txns = await extract_transactions(text)
    if not txns:
        return {"error": "no transactions found in the statement"}
    reconciled = reconciliation_ok(txns)
    if not reconciled:
        log.warning("bank statement extraction quality check FAILED for %s (%s) — running "
                    "balances don't reconcile, at least one transaction was likely misread",
                    tenant_id, source_file)
    result = await reconcile(tenant_id, txns, source_file=source_file or Path(pdf_path).name)
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
