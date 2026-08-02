"""
vula/commerce/accounting.py — the off-Xero accounting core.

Allocates each reconciled bank transaction to a chart-of-accounts category, computes VAT
(per-tenant, only when registered), and produces cash-basis P&L / VAT / general-ledger from the
reconciled ledger. Categorisation is learned-rule-first (grows as the owner corrects, reusing the
doc_filing learn pattern), then an LLM fallback over the tenant's chart of accounts.

Reports are cash-basis off `commerce_bank_transactions` (the reconciled ledger) — no double-count.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


# ── Default SA small-business chart of accounts ───────────────────────────────
# (code, name, type, vat_treatment, deductible, sort)
DEFAULT_CHART = [
    ("sales",            "Sales / income",        "income",  "standard", True, 10),
    ("other_income",     "Other income",          "income",  "standard", True, 20),
    ("cost_of_sales",    "Cost of sales / stock", "expense", "standard", True, 30),
    ("wages",            "Salaries & wages",      "expense", "none",     True, 40),
    ("casual_labour",    "Casual labour",         "expense", "none",     True, 45),
    ("rent",             "Rent",                  "expense", "standard", True, 50),
    ("packaging",        "Packaging",             "expense", "standard", True, 60),
    ("delivery",         "Delivery / transport",  "expense", "standard", True, 70),
    ("fuel",             "Fuel",                  "expense", "zero",     True, 80),
    ("marketing",        "Marketing",             "expense", "standard", True, 90),
    ("utilities",        "Utilities",             "expense", "standard", True, 100),
    ("bank_charges",     "Bank charges",          "expense", "standard", True, 110),
    ("equipment",        "Equipment / assets",    "expense", "standard", True, 120),
    ("professional_fees","Professional fees",     "expense", "standard", True, 130),
    ("insurance",        "Insurance",             "expense", "standard", True, 140),
    ("other_expense",    "Other expenses",        "expense", "standard", True, 150),
    ("owner_drawings",   "Owner drawings",        "equity",  "none",     False, 160),
    # Balance-sheet accounts (added for the general ledger, migration 121) — the original chart
    # was income/expense/equity only, fine for cash-basis P&L/VAT but a journal entry needs a
    # real Bank/Cash asset and VAT liability/asset side.
    ("bank_cash",        "Bank / cash",           "asset",     "none",  False, 5),
    ("accounts_payable", "Accounts payable",      "liability", "none",  False, 170),
    ("vat_output",       "VAT output (owed)",     "liability", "none",  False, 180),
    ("vat_input",        "VAT input (claimable)", "asset",     "none",  False, 190),
]


def ensure_chart(tenant_id: str) -> List[dict]:
    """Return the tenant's chart of accounts, seeding any SA default codes they don't already
    have (not just when the table is empty — a tenant who already has accounts from using
    expenses still needs the balance-sheet codes added in migration 121 backfilled)."""
    db = _client()
    try:
        rows = (db.table("commerce_accounts").select("*").eq("tenant_id", tenant_id)
                .order("sort").execute().data or [])
    except Exception as exc:
        log.debug("accounts read skipped (run migration 058?): %s", exc)
        return []
    have = {r["code"] for r in rows}
    missing = [{"tenant_id": tenant_id, "code": c, "name": n, "type": t,
                "vat_treatment": v, "deductible": d, "sort": s}
               for (c, n, t, v, d, s) in DEFAULT_CHART if c not in have]
    if not missing:
        return rows
    try:
        db.table("commerce_accounts").insert(missing).execute()
    except Exception as exc:
        log.debug("chart seed skipped: %s", exc)
        return rows
    return rows + missing


def _account_map(tenant_id: str) -> Dict[str, dict]:
    return {a["code"]: a for a in ensure_chart(tenant_id)}


# ── Learned categorisation (mirrors doc_filing.learn_filing_rule) ─────────────

_STOP = {"the", "and", "for", "eft", "payment", "purchase", "debit", "credit", "card", "pos",
         "transfer", "deposit", "fee", "ref", "cape", "town", "capitec", "bank"}


def _signals_from_txn(txn: dict) -> List[tuple]:
    """(signal_type, value) learning signals from a transaction: reference + description tokens."""
    out, seen = [], set()
    ref = (txn.get("reference") or "").strip().lower()
    if len(ref) >= 3:
        out.append(("reference", ref)); seen.add(ref)
    for tok in re.findall(r"[a-z][a-z0-9]{2,}", (txn.get("description") or "").lower()):
        if tok in _STOP or tok in seen:
            continue
        seen.add(tok)
        out.append(("token", tok))
    return out[:8]


def learn_category_rule(tenant_id: str, txn: dict, account_code: str) -> None:
    """Remember that a transaction with these signals → this account (grows with corrections)."""
    if not account_code:
        return
    db = _client()
    for stype, val in _signals_from_txn(txn):
        try:
            ex = (db.table("commerce_txn_rules").select("id,hits")
                  .eq("tenant_id", tenant_id).eq("signal", val).eq("account_code", account_code)
                  .limit(1).execute().data or [])
            if ex:
                db.table("commerce_txn_rules").update(
                    {"hits": (ex[0].get("hits") or 0) + 1, "last_used": "now()"}
                ).eq("id", ex[0]["id"]).execute()
            else:
                db.table("commerce_txn_rules").insert(
                    {"tenant_id": tenant_id, "signal": val, "signal_type": stype,
                     "account_code": account_code}).execute()
        except Exception as exc:
            log.debug("learn_category_rule skipped (run migration 058?): %s", exc)


def lookup_learned_category(tenant_id: str, txn: dict) -> Optional[str]:
    sigs = [v for _, v in _signals_from_txn(txn)]
    if not sigs:
        return None
    try:
        rows = (_client().table("commerce_txn_rules").select("account_code,hits")
                .eq("tenant_id", tenant_id).in_("signal", sigs)
                .order("hits", desc=True).limit(1).execute().data or [])
    except Exception:
        return None
    return rows[0]["account_code"] if rows else None


# ── AI categorisation + VAT ───────────────────────────────────────────────────

async def _ai_categorize(txn: dict, accounts: List[dict]) -> Optional[str]:
    import litellm
    from core.llm_router import resolve_generation_route
    codes = [a["code"] for a in accounts]
    listing = "\n".join(f"- {a['code']}: {a['name']} ({a['type']})" for a in accounts)
    prompt = (
        "Classify this bank transaction into ONE account code from the chart below. Reply with ONLY "
        "the account code, nothing else.\n\nChart of accounts:\n" + listing +
        f"\n\nTransaction: {txn.get('direction')} R{(txn.get('amount_cents') or 0)/100:.2f} — "
        f"{txn.get('description') or ''} (ref: {txn.get('reference') or '-'})\nAccount code:")
    try:
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route(task_type="txn_categorize")
        resp = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=256, api_key=api_key, api_base=api_base)
        raw = resp.choices[0].message.content or ""
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)   # reasoning models (deepseek-r1)
        out = raw.strip().lower()
        cleaned = re.sub(r"[^a-z_]", "", out)
        if cleaned in codes:
            return cleaned
        # Verbose model reply → find a valid code (or its account name) mentioned in the text.
        norm = re.sub(r"[^a-z_ ]", " ", out)
        for a in accounts:
            code = a["code"]
            if re.search(rf"\b{re.escape(code)}\b", norm) or re.search(rf"\b{re.escape(code.replace('_', ' '))}\b", norm):
                return code
            if a["name"].lower() in out:
                return code
        return None
    except Exception as exc:
        log.debug("ai categorize skipped: %s", exc)
        return None


async def categorize_batch(tenant_id: str, txns: List[dict],
                           accounts: Optional[List[dict]] = None) -> List[Dict[str, Any]]:
    """Allocate MANY transactions in a few calls (a monthly statement = 100+ lines; one small-model
    call per line was slow AND wrong — supplier payments landed as 'bank charges'). Learned rules
    first, then the CLOUD model in batches of 40 with direction-appropriate accounts: money in can
    only be an income code, money out an expense code (or owner_drawings for personal spend).
    Returns a list of {"account_code","source"} parallel to txns."""
    import litellm
    from core.llm_router import resolve_generation_route, escalate_to_cloud

    accounts = accounts if accounts is not None else ensure_chart(tenant_id)
    inc = [a for a in accounts if a.get("type") == "income"]
    exp = [a for a in accounts if a.get("type") in ("expense", "equity")]
    results: List[Optional[Dict[str, Any]]] = [None] * len(txns)

    pending: List[int] = []
    for i, t in enumerate(txns):
        code = lookup_learned_category(tenant_id, t)
        if code and any(a["code"] == code for a in accounts):
            results[i] = {"account_code": code, "source": "learned"}
        else:
            pending.append(i)

    if pending:
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route(task_type="txn_categorize")
        esc = escalate_to_cloud("txn_categorize_batch", task_type="txn_categorize")
        if esc:
            model, api_key, api_base = esc
        inc_listing = "\n".join(f"- {a['code']}: {a['name']}" for a in inc)
        exp_listing = "\n".join(f"- {a['code']}: {a['name']}" for a in exp)
        for start in range(0, len(pending), 40):
            batch = pending[start:start + 40]
            lines = "\n".join(
                f'{n}. [{txns[i].get("direction")}] R{(txns[i].get("amount_cents") or 0)/100:.2f} — '
                f'{(txns[i].get("description") or "")[:130]}'
                for n, i in enumerate(batch))
            prompt = (
                "You are the bookkeeper for a small South African business. Assign ONE account code "
                "to each numbered bank transaction below.\n"
                "Money [in] (credits) MUST use an income code:\n" + inc_listing + "\n"
                "Money [out] (debits) MUST use an expense code, or owner_drawings for the owner's "
                "personal/family spend from the business account:\n" + exp_listing + "\n"
                "Hints: 'Payment Received' / 'Card Machine Sales' = customers paying → sales. "
                "Payments TO suppliers of stock/ingredients → cost_of_sales. Family transfers, "
                "pharmacy, entertainment, personal shopping → owner_drawings.\n"
                "Reply with ONLY a JSON array of account-code strings, one per transaction, in order.\n\n"
                + lines)
            try:
                resp = await litellm.acompletion(
                    model=model, messages=[{"role": "user", "content": prompt}],
                    temperature=0, max_tokens=2000, api_key=api_key, api_base=api_base)
                raw = resp.choices[0].message.content or ""
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
                i0, j0 = raw.find("["), raw.rfind("]")
                arr = json.loads(raw[i0:j0 + 1]) if (i0 >= 0 and j0 > i0) else []
            except Exception as exc:
                log.warning("batch categorize failed: %s", exc)
                arr = []
            for n, i in enumerate(batch):
                t = txns[i]
                allowed = {a["code"] for a in (inc if t.get("direction") == "in" else exp)}
                code = arr[n] if n < len(arr) and isinstance(arr[n], str) else None
                if code and code.strip().lower() in allowed:
                    results[i] = {"account_code": code.strip().lower(), "source": "ai"}
                else:
                    results[i] = {"account_code": _default_code(t, accounts), "source": "default"}

    for i, t in enumerate(txns):     # anything still unset → default (flagged for the owner)
        if results[i] is None:
            results[i] = {"account_code": _default_code(t, accounts), "source": "default"}
    return results     # type: ignore[return-value]


def _default_code(txn: dict, accounts: List[dict]) -> str:
    codes = {a["code"] for a in accounts}
    if txn.get("direction") == "in":
        return "sales" if "sales" in codes else (accounts[0]["code"] if accounts else "other_income")
    return "other_expense" if "other_expense" in codes else (accounts[-1]["code"] if accounts else "other")


async def categorize(tenant_id: str, txn: dict, accounts: Optional[List[dict]] = None) -> Dict[str, Any]:
    """Allocate a transaction to an account: learned rule → AI → sensible default."""
    accounts = accounts if accounts is not None else ensure_chart(tenant_id)
    learned = lookup_learned_category(tenant_id, txn)
    if learned and any(a["code"] == learned for a in accounts):
        return {"account_code": learned, "source": "learned"}
    ai = await _ai_categorize(txn, accounts)
    if ai:
        return {"account_code": ai, "source": "ai"}
    return {"account_code": _default_code(txn, accounts), "source": "default"}


def vat_for(account: Optional[dict], amount_cents: int, vat_registered: bool) -> int:
    """Input/output VAT contained in a VAT-inclusive amount, per the account's treatment."""
    if not vat_registered or not account or account.get("vat_treatment") != "standard":
        return 0
    return int(round(amount_cents * 15 / 115))     # 15% VAT backed out of an inclusive amount


# ── Reports (cash-basis, off the reconciled ledger) ───────────────────────────

def _txns(tenant_id: str, since: Optional[str], until: Optional[str]) -> List[dict]:
    q = _client().table("commerce_bank_transactions").select(
        "txn_date,description,amount_cents,direction,vat_cents,account_code,match_status,reference,source_file"
    ).eq("tenant_id", tenant_id).neq("match_status", "ignored")
    if since:
        q = q.gte("txn_date", since)
    if until:
        q = q.lte("txn_date", until)
    try:
        return q.order("txn_date").limit(10000).execute().data or []
    except Exception as exc:
        log.debug("ledger read skipped: %s", exc)
        return []


def pnl(tenant_id: str, since: str = None, until: str = None) -> dict:
    accounts = {a["code"]: a for a in ensure_chart(tenant_id)}
    rows = _txns(tenant_id, since, until)
    income, expense = {}, {}
    for r in rows:
        acc = accounts.get(r.get("account_code") or "")
        amt = int(r.get("amount_cents") or 0)
        typ = (acc or {}).get("type", "income" if r.get("direction") == "in" else "expense")
        bucket = income if typ == "income" else expense if typ == "expense" else None
        if bucket is None:
            continue
        name = (acc or {}).get("name", r.get("account_code") or "Uncategorised")
        bucket[name] = bucket.get(name, 0) + amt
    total_income = sum(income.values())
    total_expense = sum(expense.values())
    return {"since": since, "until": until,
            "income": [{"account": k, "amount_cents": v} for k, v in sorted(income.items(), key=lambda x: -x[1])],
            "expenses": [{"account": k, "amount_cents": v} for k, v in sorted(expense.items(), key=lambda x: -x[1])],
            "total_income_cents": total_income, "total_expense_cents": total_expense,
            "net_profit_cents": total_income - total_expense}


def is_vat_registered(tenant_id: str) -> bool:
    try:
        rows = (_client().table("commerce_invoice_settings").select("vat_registered")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        return bool(rows[0].get("vat_registered", True)) if rows else True
    except Exception:
        return True


def vat_return(tenant_id: str, since: str = None, until: str = None) -> dict:
    if not is_vat_registered(tenant_id):
        return {"vat_registered": False}
    accounts = {a["code"]: a for a in ensure_chart(tenant_id)}
    rows = _txns(tenant_id, since, until)
    output_vat = input_vat = 0
    for r in rows:
        acc = accounts.get(r.get("account_code") or "")
        vat = int(r.get("vat_cents") or 0) or vat_for(acc, int(r.get("amount_cents") or 0), True)
        if (acc or {}).get("type") == "income" or r.get("direction") == "in":
            output_vat += vat
        else:
            input_vat += vat
    return {"vat_registered": True, "since": since, "until": until,
            "output_vat_cents": output_vat, "input_vat_cents": input_vat,
            "net_vat_cents": output_vat - input_vat,
            "note": "Net VAT payable to SARS (positive) or refundable (negative). Cash-basis from the bank ledger."}


def general_ledger(tenant_id: str, since: str = None, until: str = None) -> List[dict]:
    accounts = {a["code"]: a for a in ensure_chart(tenant_id)}
    out = []
    for r in _txns(tenant_id, since, until):
        acc = accounts.get(r.get("account_code") or "")
        out.append({"date": r.get("txn_date"), "description": r.get("description"),
                    "account": (acc or {}).get("name", r.get("account_code") or "Uncategorised"),
                    "account_code": r.get("account_code"), "direction": r.get("direction"),
                    "amount_cents": int(r.get("amount_cents") or 0), "vat_cents": int(r.get("vat_cents") or 0),
                    "reference": r.get("reference"), "source": r.get("source_file")})
    return out
