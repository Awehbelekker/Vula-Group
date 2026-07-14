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


# ── Statement password (encrypted, per tenant) ────────────────────────────────

def set_statement_password(tenant_id: str, password: str, bank: str = "Capitec") -> None:
    from vula.email_imap.credentials import encrypt_secret
    _client().table("vula_email_accounts").update(
        {"statement_password": encrypt_secret(password), "bank": bank}
    ).eq("tenant_id", tenant_id).execute()


def get_statement_password(tenant_id: str) -> Optional[str]:
    try:
        rows = (_client().table("vula_email_accounts").select("statement_password")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
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


# ── Reconciliation ────────────────────────────────────────────────────────────

def _tok(s: str) -> set:
    return set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))


def _match_invoice(txn: Dict[str, Any], invoices: List[dict]) -> Optional[dict]:
    """Confident match of a credit to an outstanding invoice by amount (+ name/reference boost)."""
    amt = txn["amount_cents"]
    tol = max(100, int(amt * 0.01))          # R1 or 1%
    blob = _tok(txn.get("description")) | _tok(txn.get("reference"))
    best, best_score = None, 0
    for inv in invoices:
        if abs(int(inv.get("total_cents") or 0) - amt) > tol:
            continue
        score = 2                            # exact-ish amount is the anchor
        name_toks = _tok(inv.get("customer_name")) | _tok(inv.get("invoice_number"))
        if blob & name_toks:
            score += 3                       # name/number appears in the txn reference
        if score > best_score:
            best, best_score = inv, score
    # Require amount match; if multiple invoices share the amount and none name-matched, it's ambiguous.
    if best is None:
        return None
    same_amt = [i for i in invoices if abs(int(i.get("total_cents") or 0) - amt) <= tol]
    if len(same_amt) > 1 and best_score < 5:
        return None                          # ambiguous → leave for review
    return best


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
    matched_expenses = 0
    used_invoice_ids: set = set()
    for idx, t in enumerate(txns):
        status, inv_id, worker_id, wk_project, exp_id = "unmatched", None, None, None, None
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
        if t["direction"] == "in" and inv_id and "sales" in acc_map:
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
            "matched_invoice_id": inv_id, "matched_expense_id": exp_id,
            "match_status": status, "source_file": source_file,
            "account_code": code, "vat_cents": vat_cents,
            "vat_treatment": (acc_map.get(code) or {}).get("vat_treatment"), "categorized_by": cat_src,
            "worker_id": worker_id, "project": wk_project,
        }
        try:
            db.table("commerce_bank_transactions").upsert(
                row, on_conflict="tenant_id,txn_date,amount_cents,description").execute()
            saved += 1
        except Exception as exc:
            # 058/059 columns (account_code/vat/worker_id/project) may not exist yet — retry with core.
            for k in ("account_code", "vat_cents", "vat_treatment", "categorized_by", "worker_id", "project"):
                row.pop(k, None)
            try:
                db.table("commerce_bank_transactions").upsert(
                    row, on_conflict="tenant_id,txn_date,amount_cents,description").execute()
                saved += 1
            except Exception as exc2:
                log.debug("bank txn upsert skipped (run migration 057?): %s", exc2)

    return {"parsed": len(txns), "saved": saved, "matched_invoices": matched_invoices,
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
    result = await reconcile(tenant_id, txns, source_file=source_file or Path(pdf_path).name)
    log.info("bank statement reconciled for %s: %s", tenant_id, result)
    return result
