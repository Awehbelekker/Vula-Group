"""
vula/commerce/job_costing.py — is each project making its fee? From the bank, not from hope.

2026-09-28 (Ian, for DIGG): "Judy usually asks 10% on top of cost… Vula should see the
operational costs and labour costs based on transactions — so the project doesn't run at a
loss." Her own statement breakdown for 10 Jul – 12 Sep showed HPC001 certificates R1,514,438
received against R1,560,782 paid out: a job priced at cost + 10% running R46k negative, about
R200k short of its fee, and nothing in Vula could see it.

Per project, from commerce_bank_transactions allocated to it (statement sheet import, the
owner's picker, or learned rules — vula/commerce/allocation.py):

  received          money in on the project (payment certificates, client payments)
  cost              money out on the project, by trade
  fee_earned        received − cost
  fee_target        cost × fee_pct            (vula_project_terms; cost-plus, default 10%)
  overhead_share    the business's running costs, each month split over the projects by that
                    month's project spend (Ian's choice; drawings included)
  profit            fee_earned − overhead_share

Money is integer cents throughout; nothing here is estimated by a model. Received is "so far":
a certificate not yet paid isn't in the bank, so a gap can close when it lands — said so in
the text the agent gets.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_PROJECT_COST = {"cost_of_sales", "casual_labour"}
_NOT_SPEND = {"bank_cash", "accounts_payable", "vat_output", "vat_input"}
_UNSPECIFIED = ("unspecified", "unallocated", "general", "")
DEFAULT_FEE_PCT = 10.0


def _client():
    from vula.commerce import service
    return service._client()


def _txns(tenant_id: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages

    def make():
        q = (_client().table("commerce_bank_transactions")
             .select("id,txn_date,description,payee,amount_cents,direction,account_code,project,"
                     "trade,match_status")
             .eq("tenant_id", tenant_id))
        if since:
            q = q.gte("txn_date", since)
        return q.order("txn_date")
    try:
        rows = [r for r in _all_pages(make) if r.get("match_status") != "ignored"]
    except Exception as exc:
        log.debug("job costing read failed: %s", exc)
        return []
    return sorted(rows + _owed_claims(tenant_id, since), key=lambda r: str(r.get("txn_date") or ""))


def _owed_claims(tenant_id: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Job costs someone paid from their own pocket and hasn't been paid back for yet.

    2026-10-02 (Ian, DIGG): Judy paid R15,913 of BWH / Build It / delivery costs from her private
    account. None of it is in DIGG's bank, so the projects looked cheaper than they were. Until
    she's reimbursed the claim stands in for the bank line; once it's marked reimbursed the
    business's own payment to her carries the cost instead, so it's never counted twice.

    Only claims proven by the person's own bank statement (channel 'statement') count. DIGG had
    33 WhatsApp receipts flagged reimbursable (R81k) — pro-formas, invoices to DIGG, slips paid
    on DIGG's own FNB card — that are already in the bank; counting them doubled HPC's cost."""
    def make():
        q = (_client().table("commerce_expenses")
             .select("id,date,description,supplier,amount_cents,account_code,project,status")
             .eq("tenant_id", tenant_id).eq("reimbursable", True).is_("reimbursed_at", "null")
             .eq("paid_with", "personal").eq("channel", "statement"))
        if since:
            q = q.gte("date", since)
        return q.order("date")
    try:
        from vula.commerce.ledger import _all_pages
        rows = _all_pages(make)
    except Exception as exc:
        log.debug("owed claims read skipped: %s", exc)
        return []
    return [{"id": f"claim:{r['id']}", "txn_date": r.get("date"),
             "description": r.get("description") or r.get("supplier") or "Expense claim",
             "payee": r.get("supplier"), "amount_cents": int(r.get("amount_cents") or 0),
             "direction": "out", "account_code": r.get("account_code"), "project": r.get("project"),
             "trade": None, "match_status": "claim"}
            for r in rows if r.get("status") not in ("reimbursed", "rejected")]


# "Tax_Invoice_-_INV0000216.pdf": underscores are word characters, so no \b before INV
_INV_NO = re.compile(r"(?<![A-Za-z])(INV|IN|INVOICE)[\s_-]*0*(\d{3,})", re.IGNORECASE)


def direct_costs(tenant_id: str) -> Dict[str, Dict[str, Any]]:
    """project → {"cents", "documents"} for invoices from DIRECT suppliers: in the project's BOQ, but
    contracted and paid on the project directly, not through DIGG's bank.

    2026-10-03 (Ian, DIGG): Teck Flooring, Extra Air and Storeplay "were suppliers but direct… a cost
    in the BOQ", and Judy's 10% doesn't apply to directs. So they count against the BOQ (what the
    project has spent) and never toward DIGG's cost, profit or fee target. Flagged per document
    (fields.direct_supplier). A revised invoice (same supplier and invoice number filed again)
    replaces the earlier one rather than adding to it."""
    from vula.commerce.ledger import _all_pages
    from vula.commerce.service import project_key

    def make():
        return (_client().table("vula_filed_documents")
                .select("id,project,filename,fields,created_at")
                .eq("tenant_id", tenant_id).eq("status", "filed").eq("category", "Invoice")
                .eq("fields->>direct_supplier", "true").order("created_at"))
    try:
        rows = _all_pages(make)
    except Exception as exc:
        log.debug("direct supplier read skipped: %s", exc)
        return {}
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        f = r.get("fields") or {}
        try:
            cents = int(f.get("total_cents") or 0)
        except (TypeError, ValueError):
            cents = 0
        if not r.get("project") or cents <= 0:
            continue
        m = _INV_NO.search(r.get("filename") or "")
        supplier = "".join(ch for ch in str(f.get("supplier") or "").lower() if ch.isalnum())
        key = (project_key(r["project"]), supplier, m.group(2) if m else r["id"])
        latest[key] = {"project": r["project"], "cents": cents}       # rows are oldest first
    out: Dict[str, Dict[str, Any]] = {}
    for (pk, _s, _n), v in latest.items():
        d = out.setdefault(pk, {"project": v["project"], "cents": 0, "documents": 0})
        d["cents"] += v["cents"]
        d["documents"] += 1
    return out


def terms(tenant_id: str) -> Dict[str, float]:
    """project → fee %; '*' is the tenant default."""
    out = {"*": DEFAULT_FEE_PCT}
    try:
        for r in (_client().table("vula_project_terms").select("project,fee_pct")
                  .eq("tenant_id", tenant_id).limit(500).execute().data or []):
            if r.get("fee_pct") is not None:
                out[r["project"]] = float(r["fee_pct"])
    except Exception as exc:
        log.debug("project terms read skipped (run migration 185?): %s", exc)
    return out


def set_fee(tenant_id: str, project: str, fee_pct: float) -> None:
    from datetime import datetime, timezone
    _client().table("vula_project_terms").upsert(
        {"tenant_id": tenant_id, "project": project or "*", "pricing": "cost_plus",
         "fee_pct": float(fee_pct), "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="tenant_id,project").execute()


def _own_wages(tenant_id: str, t: Dict[str, Any]) -> bool:
    try:
        from vula.commerce.allocation import is_own_wages
        return is_own_wages(tenant_id, t.get("description"), t.get("payee"))
    except Exception:
        return False


def _is_overhead(t: Dict[str, Any]) -> bool:
    code = t.get("account_code") or "other_expense"
    return (t.get("direction") == "out" and not t.get("project")
            and code not in _PROJECT_COST and code not in _NOT_SPEND)


def _trade(t: Dict[str, Any]) -> str:
    tr = (t.get("trade") or "").strip()
    if tr.lower().split(" - ")[-1].strip() in _UNSPECIFIED or "unspecified" in tr.lower():
        return "Not yet allocated to a trade"
    return tr or ("Labour" if t.get("account_code") == "casual_labour" else "Not yet allocated to a trade")


def costing(tenant_id: str, since: Optional[str] = None, txns: Optional[List[Dict[str, Any]]] = None
            ) -> Dict[str, Any]:
    rows = txns if txns is not None else _txns(tenant_id, since)
    fees = terms(tenant_id)
    proj: Dict[str, Dict[str, Any]] = {}
    proj_out_m: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    overhead_m: Dict[str, int] = defaultdict(int)
    unallocated = 0
    unallocated_lines = 0
    for t in rows:
        cents = int(t.get("amount_cents") or 0)
        month = str(t.get("txn_date") or "")[:7]
        p = t.get("project")
        if p:
            d = proj.setdefault(p, {"received": 0, "cost": 0, "trades": defaultdict(int),
                                    "first": t.get("txn_date"), "last": t.get("txn_date")})
            d["last"] = t.get("txn_date") or d["last"]
            if t.get("direction") == "in":
                d["received"] += cents
            else:
                d["cost"] += cents
                d["trades"][_trade(t)] += cents
                proj_out_m[month][p] += cents
        elif (t.get("direction") == "out" and t.get("account_code") in _PROJECT_COST
              and _own_wages(tenant_id, t)):
            overhead_m[month] += cents    # "DIGG WAGE": the business's own people — shared
        elif t.get("direction") == "out" and (t.get("account_code") in _PROJECT_COST):
            unallocated += cents          # bought for "a job" — which one isn't known yet
            unallocated_lines += 1
        elif _is_overhead(t):
            overhead_m[month] += cents

    share: Dict[str, int] = defaultdict(int)
    unshared = 0
    for month, oh in overhead_m.items():
        spend = proj_out_m.get(month) or {}
        total = sum(spend.values())
        if not total:
            unshared += oh
            continue
        for p, c in spend.items():
            share[p] += int(round(oh * c / total))

    projects = []
    for p, d in proj.items():
        fee_pct = fees.get(p, fees["*"])
        cost, received = d["cost"], d["received"]
        fee_target = int(round(cost * fee_pct / 100))
        fee_earned = received - cost
        profit = fee_earned - share[p]
        if profit < 0:
            status = "loss"
        elif fee_earned < 0.95 * fee_target:
            status = "below target"
        else:
            status = "on track"
        trades = sorted(({"trade": k, "cents": v} for k, v in d["trades"].items()),
                        key=lambda x: -x["cents"])
        projects.append({
            "project": p, "received_cents": received, "cost_cents": cost, "fee_pct": fee_pct,
            "target_received_cents": cost + fee_target, "fee_target_cents": fee_target,
            "fee_earned_cents": fee_earned, "fee_shortfall_cents": max(0, fee_target - fee_earned),
            "overhead_share_cents": share[p], "profit_cents": profit, "status": status,
            "unallocated_trade_cents": d["trades"].get("Not yet allocated to a trade", 0),
            "trades": trades, "first": d["first"], "last": d["last"],
        })
    variations = _variations(tenant_id)
    from vula.commerce.service import project_key
    directs = direct_costs(tenant_id) if txns is None else {}
    for p in projects:
        v = variations.get(project_key(p["project"]))
        if v:
            p["variations"] = v
        d = directs.get(project_key(p["project"]))
        if d:
            p["direct_cost_cents"] = d["cents"]       # BOQ only — never cost, profit or fee
    projects.sort(key=lambda x: -x["cost_cents"])
    overhead_total = sum(overhead_m.values())
    project_spend = sum(p["cost_cents"] for p in projects)
    return {
        "projects": projects,
        "overheads_cents": overhead_total,
        "overheads_by_month": dict(sorted(overhead_m.items())),
        "overheads_unshared_cents": unshared,
        "overhead_rate_pct": round(100 * overhead_total / project_spend, 1) if project_spend else None,
        "unallocated_project_spend_cents": unallocated,
        "unallocated_project_lines": unallocated_lines,
        "fees_earned_cents": sum(p["fee_earned_cents"] for p in projects),
        "fees_target_cents": sum(p["fee_target_cents"] for p in projects),
        "business_result_cents": sum(p["fee_earned_cents"] for p in projects) - overhead_total,
        "since": since,
    }


import re as _re

# 2026-09-29, Judy (digg-demo) at 09:03: "What data are you using to reference cost" reached the
# general knowledge skill, which can't see her costing and could only guess. Where the numbers
# come from is a fact about her data, so it's answered from the data.
COST_BASIS_RE = _re.compile(
    r"\b(what|which)\s+(data|info\w*|figures|numbers|sources?|documents?)\b[^?]{0,50}\bcosts?\b|"
    r"\breferenc\w*\s+(the\s+)?costs?\b|"
    r"\bwhere\b[^?]{0,30}\b(costs?|cost\s+figures|numbers)\b[^?]{0,25}\bfrom\b", _re.IGNORECASE)


def looks_like_cost_basis_question(text: str) -> bool:
    return bool(COST_BASIS_RE.search(text or ""))


def cost_basis(tenant_id: str) -> str:
    """What the project costs are measured from, with the real counts — no model."""
    all_rows = _txns(tenant_id)
    res = costing(tenant_id, txns=all_rows)
    rows = [t for t in all_rows if t.get("match_status") != "claim"]
    claims = [t for t in all_rows if t.get("match_status") == "claim"]
    tagged = [t for t in rows if t.get("project")]
    lines = ["💡 *Where your project costs come from*", ""]
    if rows:
        first = min(str(t.get("txn_date") or "") for t in rows if t.get("txn_date"))
        last = max(str(t.get("txn_date") or "") for t in rows if t.get("txn_date"))
        lines.append(f"1. *Bank statements* — {len(rows)} lines from {first} to {last}. "
                     f"{len(tagged)} are on a project: money out is the project's cost (by trade), "
                     "money in is what the client has paid.")
    else:
        lines.append("1. *Bank statements* — none imported yet, so there are no project costs to "
                     "measure. Send a statement (PDF or the Excel breakdown).")
    if claims:
        lines.append(f"   Plus {len(claims)} costs paid personally and not yet paid back "
                     f"({_r(sum(t['amount_cents'] for t in claims))}) — counted until reimbursed.")
    if res.get("unallocated_project_lines"):
        lines.append(f"   {res['unallocated_project_lines']} job-cost lines "
                     f"({_r(res['unallocated_project_spend_cents'])}) aren't on a project yet — "
                     "they're left out until you say which job they're for.")
    for p in res.get("projects", [])[:6]:
        lines.append(f"   • {p['project']}: cost {_r(p['cost_cents'])}, received {_r(p['received_cents'])}")
    fees = terms(tenant_id)
    lines.append(f"2. *Your fee* — cost + {fees['*']:g}% unless a project has its own %. Running "
                 "costs (overheads, drawings) are shared across the month's active projects.")
    lines.append("3. *Signed baselines* — each project's cost is checked against its signed "
                 "cost document; anything over it is flagged for a variation order.")
    try:
        boq = (_client().table("vula_project_boq")
               .select("project,total_cents,baseline_locked")
               .eq("tenant_id", tenant_id).limit(100).execute().data or [])
    except Exception as exc:
        log.debug("cost basis: baselines skipped: %s", exc)
        boq = []
    locked = {b["project"]: b for b in boq if b.get("baseline_locked") is True}
    for b in locked.values():
        lines.append(f"   • {b['project']}: signed baseline {_r(b.get('total_cents'))}")
    try:
        from vula.commerce.project_programme import running_projects
        missing = [p for p in running_projects(tenant_id) if p not in locked]
    except Exception:
        missing = []
    if missing:
        lines.append("   No signed baseline yet for: " + ", ".join(missing) + ". Tell me which "
                     "document it is, e.g. *set up " + missing[0] + " — costs against the signed "
                     "Rev10*, and I'll lock it.")
    elif not locked:
        lines.append("   None set yet — tell me the project and its signed cost document.")
    lines += ["", "Filed invoices are kept as the evidence behind the bank lines, not counted a "
              "second time."]
    return "\n".join(lines)


def _variations(tenant_id: str) -> Dict[str, Dict[str, Any]]:
    """Per project: documents labelled "Variation — over BOQ" (vula/integrations/doc_labels.py) —
    what the business claims from its client for extra work, and extra costs suppliers charged
    it. Ian, 2026-09-28: these are "over and above the projected BOQ"."""
    from vula.commerce.ledger import _all_pages
    from vula.commerce.service import project_key
    from vula.integrations.doc_filing import _is_own, _own_names
    from vula.integrations.doc_labels import is_variation

    def make():
        return (_client().table("vula_filed_documents").select("id,project,fields,filename")
                .eq("tenant_id", tenant_id).in_("category", ["Invoice", "Quote / Estimate"]))
    try:
        rows = _all_pages(make)
    except Exception as exc:
        log.debug("variations read skipped: %s", exc)
        return {}
    own = _own_names(tenant_id)
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        f = r.get("fields") or {}
        if not r.get("project") or not is_variation(f):
            continue
        try:
            cents = int(f.get("total_cents") or 0)
        except (TypeError, ValueError):
            cents = 0
        v = out.setdefault(project_key(r["project"]), {"documents": 0, "claimed_cents": 0, "extra_cost_cents": 0})
        v["documents"] += 1
        sup = f.get("supplier")
        if not sup or _is_own(sup, own):
            v["claimed_cents"] += cents        # the business's own claim to its client
        else:
            v["extra_cost_cents"] += cents     # a supplier charging for extra work
    return out


def _r(cents: Optional[int]) -> str:
    return "—" if cents is None else f"R{cents / 100:,.2f}"


def find_project(result: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    from vula.commerce.service import project_key
    key = project_key(name)
    if not key:
        return None
    for p in result["projects"]:
        pk = project_key(p["project"])
        if pk == key or pk.startswith(key) or key.startswith(pk) or key.split()[0] == pk.split()[0]:
            return p
    return None


def project_profit(tenant_id: str, project: Optional[str] = None) -> Dict[str, Any]:
    """The agent's view: one project's figures (or every project's headline) plus a
    deterministic text the reply can quote."""
    res = costing(tenant_id)
    if not res["projects"]:
        return {"message": "No bank lines are allocated to a project yet — import a categorised "
                           "statement (Bank › Import sheet) or allocate lines to projects first."}
    if project:
        p = find_project(res, project)
        if not p:
            return {"message": f"No bank lines allocated to a project like '{project}'.",
                    "projects": [x["project"] for x in res["projects"]]}
        top = ", ".join(f"{t['trade']} {_r(t['cents'])}" for t in p["trades"][:5])
        text = (f"{p['project']} ({p['first']} – {p['last']}): received {_r(p['received_cents'])}, "
                f"cost {_r(p['cost_cents'])}. At cost + {p['fee_pct']:g}% it should have brought in "
                f"{_r(p['target_received_cents'])} — fee earned so far {_r(p['fee_earned_cents'])} "
                f"of {_r(p['fee_target_cents'])}"
                + (f" (short {_r(p['fee_shortfall_cents'])})" if p["fee_shortfall_cents"] else "")
                + f". Its share of overheads is {_r(p['overhead_share_cents'])}, so profit is "
                f"{_r(p['profit_cents'])} — {p['status']}. Biggest costs: {top}."
                + (f" {_r(p['unallocated_trade_cents'])} of its cost isn't allocated to a trade yet."
                   if p["unallocated_trade_cents"] else "")
                + (f" Direct suppliers paid on the project (in the BOQ, not DIGG's cost or fee): "
                   f"{_r(p['direct_cost_cents'])}." if p.get("direct_cost_cents") else "")
                + (f" Variations over the BOQ: {p['variations']['documents']} document(s) — claimed from the "
                   f"client {_r(p['variations']['claimed_cents'])}, extra costs from suppliers "
                   f"{_r(p['variations']['extra_cost_cents'])}." if p.get("variations") else "")
                + " Received counts money in the bank so far — a certificate not yet paid isn't in it.")
        return {"project": p, "text": text}
    lines = [f"• {p['project']}: received {_r(p['received_cents'])}, cost {_r(p['cost_cents'])}, "
             f"profit after overheads {_r(p['profit_cents'])} ({p['status']})" for p in res["projects"]]
    text = ("\n".join(lines)
            + f"\nOverheads {_r(res['overheads_cents'])}"
            + (f" ({res['overhead_rate_pct']}% of project spend)" if res["overhead_rate_pct"] is not None else "")
            + f". Fees earned {_r(res['fees_earned_cents'])} against a target of {_r(res['fees_target_cents'])}."
            + (f" {_r(res['unallocated_project_spend_cents'])} of materials/labour isn't allocated to a project yet."
               if res["unallocated_project_spend_cents"] else ""))
    return {"summary": {k: v for k, v in res.items() if k != "projects"},
            "projects": res["projects"], "text": text}


def overhead_rate(tenant_id: str, days: int = 90) -> Optional[float]:
    since = (date.today() - timedelta(days=days)).isoformat()
    res = costing(tenant_id, since=since)
    return res["overhead_rate_pct"]


def price_advice(tenant_id: str, item: str, quantity: Optional[float] = None,
                 unit: Optional[str] = None, project: Optional[str] = None) -> Dict[str, Any]:
    """What to charge for an item so the job makes its fee: what the business has actually
    PAID for it (price book), plus overheads as a share of project spend, plus the fee.
    Every step is returned; the price is advice — the quote stays the owner's call."""
    from vula.api.qs import _manual_rates
    from vula.commerce import price_book
    learned = price_book.rates(tenant_id, item, limit=10)
    if unit:
        same = [r for r in learned if (r.get("unit") or "each") == unit]
        learned = same or learned
    paid = [r for r in learned if r.get("basis") == "paid"]
    basis_row = (paid or learned or [None])[0]
    own = []
    try:
        own = _manual_rates(tenant_id, item)
    except Exception:
        pass
    if basis_row:
        cost = max(int(basis_row["rate_cents"]), int(basis_row.get("latest_cents") or 0))
        cost_note = (f"{basis_row['description']}: {'paid' if basis_row['basis'] == 'paid' else 'quoted'} "
                     f"median {_r(basis_row['rate_cents'])}, latest {_r(basis_row.get('latest_cents'))} "
                     f"per {basis_row.get('unit') or 'each'} ({basis_row.get('source')}) — using the higher")
        unit = unit or basis_row.get("unit") or "each"
    elif own:
        cost = int(round(float(own[0]["rate"]) * 100))
        cost_note = f"your own rate for {own[0]['description']}: {_r(cost)} per {own[0].get('unit')}"
        unit = unit or own[0].get("unit") or "each"
    else:
        return {"message": f"No price on file for '{item}' — not on any invoice, quote or BOQ, and "
                           "not in your rates. Add a rate (QS Rates) or file a supplier invoice for it."}
    fees = terms(tenant_id)
    fee_pct = fees.get(project, fees["*"]) if project else fees["*"]
    oh_pct = overhead_rate(tenant_id) or 0.0
    cost_plus = int(round(cost * (1 + fee_pct / 100)))
    covering = int(round(cost * (1 + oh_pct / 100) * (1 + fee_pct / 100)))
    out = {
        "item": item, "unit": unit, "cost_cents": cost, "fee_pct": fee_pct,
        "overhead_pct": oh_pct, "price_cost_plus_cents": cost_plus,
        "price_covering_overheads_cents": covering, "basis": cost_note,
    }
    steps = [f"Cost: {cost_note}.",
             f"Cost + {fee_pct:g}% fee: {_r(cost_plus)} per {unit}."]
    if oh_pct:
        steps.append(f"Your running costs are {oh_pct:g}% of project spend (last 90 days), so the "
                     f"{fee_pct:g}% fee leaves about {fee_pct - oh_pct:.1f}% as profit. To keep the full "
                     f"{fee_pct:g}% after overheads: {_r(covering)} per {unit}.")
    if basis_row and basis_row.get("quoted_cents") and basis_row["basis"] == "paid":
        q, pd = basis_row["quoted_cents"], basis_row["rate_cents"]
        if pd > q * 1.03:
            steps.append(f"Past quotes/BOQs priced this at {_r(q)} — {round(100 * (pd - q) / q)}% under "
                         f"what it actually cost ({_r(pd)}).")
            out["under_quoted_pct"] = round(100 * (pd - q) / q, 1)
    if own and basis_row:
        mine = int(round(float(own[0]["rate"]) * 100))
        if mine < cost_plus:
            steps.append(f"Your rate book has {_r(mine)} for {own[0]['description']} — below cost + fee.")
    if quantity:
        out["quantity"] = quantity
        out["total_cost_plus_cents"] = int(round(cost_plus * quantity))
        out["total_covering_overheads_cents"] = int(round(covering * quantity))
        steps.append(f"For {quantity:g} {unit}: {_r(out['total_cost_plus_cents'])} at cost + fee, "
                     f"{_r(out['total_covering_overheads_cents'])} covering overheads too.")
    out["text"] = " ".join(steps)
    return out


# ── Weekly early warning ─────────────────────────────────────────────────────

_last_alert: Dict[str, str] = {}


async def weekly_alert(tenant_id: str, force: bool = False) -> Optional[str]:
    """Once a week, tell the team which projects are below their fee or at a loss, and how much
    project spend still isn't allocated. Silent when everything is on track."""
    week = date.today().isocalendar()
    key = f"{week[0]}-{week[1]}"
    if not force and _last_alert.get(tenant_id) == key:
        return None
    from vula.api.tenants import uses_projects
    if not uses_projects(tenant_id):
        return None           # a shop has no projects to check
    res = costing(tenant_id, since=(date.today() - timedelta(days=180)).isoformat())
    bad = [p for p in res["projects"] if p["status"] != "on track"]
    unalloc = res["unallocated_project_spend_cents"]
    if not bad and unalloc < 1_000_000:
        _last_alert[tenant_id] = key
        return None
    lines = [f"• {p['project']}: {p['status']} — fee earned {_r(p['fee_earned_cents'])} of "
             f"{_r(p['fee_target_cents'])}, profit after overheads {_r(p['profit_cents'])}" for p in bad]
    if unalloc >= 1_000_000:
        lines.append(f"• {_r(unalloc)} of materials/labour isn't allocated to a project — "
                     "allocate it in Bank so the job costs are complete.")
    text = "📊 Project check (last 6 months, from the bank):\n" + "\n".join(lines)
    # 2 Oct (Ian: "Judy is getting WhatsApps about a costing recoup"): _last_alert is per process,
    # so every deploy/restart re-sent this — 13 times in 4 days. The DB-backed send key makes it
    # once per ISO week per person whatever restarts. force (the manual trigger) still sends.
    idem = None if force else f"project-check:{key}"
    try:
        from vula.integrations import notify
        members = notify._members(tenant_id)
        targeted = [m for m in members
                    if "project_margin" in (m.get("notify") or []) and m.get("whatsapp")]
        if targeted or not members:          # no team at all → notify_team's fallback phone
            await notify.notify_team(tenant_id, "project_margin", text, idem_key=idem)
        else:
            # nobody subscribed yet — the owner/manager still needs to hear a job is losing money
            from vula.api.whatsapp import _send_reply
            for m in members:
                if (m.get("role") or "").lower() in ("owner", "manager") and m.get("whatsapp"):
                    to = notify._digits(m["whatsapp"])
                    await _send_reply(to, text, tenant_id=tenant_id,
                                      idem_key=f"{idem}:{to}" if idem else None)
        _last_alert[tenant_id] = key
    except Exception as exc:
        log.debug("project alert skipped: %s", exc)
    return text
