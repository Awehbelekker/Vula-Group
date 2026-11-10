"""vula/commerce/supplier_identity.py — one supplier, one record (10 Nov 2026).

A building client's supplier list had the same business under two or three records — "Pudlo" and
"Pudlo S.A (Pty) Ltd", "Dyi" / "DIY" / "DO IT YOURSELF HARDWARE", "Isoboard" and "Isoboard SA
(Pty) Ltd", "ICES ELIGHTING ELECTRICAL" and even "elighting01@gmail.com" next to "Elighting
Electrical Services". Each was created automatically from a slip, proof of payment or quote whose
name didn't quite match, and each split that supplier's bills, totals and expense account.

  same_supplier(a, b)     the reason two supplier records are the same business, or None:
                          the VAT number, the bank account (a masked "..812444" matches its full
                          number), the contact email, or the name — equal once "SA (Pty) Ltd" is
                          dropped, one name the start of the other ("Pudlo" / "Pudlo S.A"), or
                          initials ("DIY" / "Do It Yourself Hardware"). Names are only trusted
                          when the shared word is distinctive: "Builders" never swallows
                          "Builders Warehouse", nor "Cape Glass" "Cape Steel".
  find_existing(...)      the record a new supplier would duplicate — upsert_supplier asks this
                          before it creates anything, so every path (WhatsApp, document commit,
                          the dashboard, the chat skill) is covered by one rule.
  find_duplicates(...)    groups of records already on file that are one business.
  merge(...)              fold duplicates into one: every bill, expense, document, order and
                          product that pointed at a duplicate points at the kept record; their
                          names become its aliases; its blanks are filled from them; then they
                          are deleted — only once nothing points at them (read back).

Every read and write is tenant-scoped.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)

# Dropped before names are compared: legal form, country, filler.
_FORM = frozenset({"pty", "ltd", "cc", "inc", "limited", "proprietary", "the", "sa", "s", "a", "t", "co",
                   "company", "south", "africa", "holdings", "group", "trading", "ta", "and", "of"})
# Too common to identify a business on their own: a name that is only these never matches another
# by the start-of-name rule.
_TRADE = frozenset({"builders", "building", "build", "construction", "hardware", "glass", "electrical",
                    "electric", "plumbing", "plumbers", "services", "service", "supplies", "supply",
                    "solutions", "engineering", "engineers", "consulting", "consultants", "design",
                    "designs", "steel", "timber", "tiles", "tile", "paint", "paints", "cape", "town",
                    "city", "national", "first", "standard", "african", "general", "centre", "center",
                    "store", "stores", "shop", "warehouse", "hire", "rental", "rentals", "projects",
                    "contractors", "trade", "traders", "industries", "enterprises", "investments",
                    "properties", "property", "development", "developments", "manufacturing"})

# (table, column, column holds a uuid) — every place a supplier id is stored.
REFERENCES: List[Tuple[str, str, bool]] = [
    ("commerce_invoices", "supplier_id", False),
    ("commerce_expenses", "supplier_id", False),
    ("vula_filed_documents", "supplier_id", False),
    ("vula_buying_shops", "supplier_id", False),
    ("vula_takeoff_orders", "supplier_id", False),
    ("commerce_purchase_orders", "supplier_id", True),
    ("commerce_products", "default_supplier_id", True),
    ("commerce_product_variants", "default_supplier_id", True),
]
# Filled on the kept record from a duplicate when the kept one has none.
_FILL = ("tax_id", "contact_email", "contact_phone", "account_number", "bank_name", "bank_account_name",
         "bank_account_number", "bank_branch_code", "layout_signature")


def _client():
    from vula.commerce.service import _client as c
    return c()


# ── identity ─────────────────────────────────────────────────────────────────────────────────

def is_email(text: Optional[str]) -> bool:
    return bool(_EMAIL.match((text or "").strip()))


def name_from_email(email: str) -> str:
    """'elighting01@gmail.com' → 'Elighting'; 'accounts@solidcape.co.za' → 'Solidcape'. The mailbox
    for a free-mail address, else the domain — never the address itself as a supplier's name."""
    local, _, domain = (email or "").strip().lower().partition("@")
    free = domain.split(".")[0] in {"gmail", "yahoo", "hotmail", "outlook", "icloud", "live", "webmail",
                                    "mweb", "telkomsa", "vodamail", "absamail"}
    base = local if free or local not in {"info", "accounts", "admin", "sales", "office", "quotes",
                                          "orders", "hello", "mail", "contact"} else domain.split(".")[0]
    base = re.sub(r"[\d_.\-+]+", " ", base).strip()
    return base.title() or email


def _tokens(name: Optional[str]) -> List[str]:
    return re.findall(r"[a-z0-9]+", (name or "").lower())


def _core(name: Optional[str]) -> List[str]:
    return [t for t in _tokens(name) if t not in _FORM]


def _digits(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _same_account(a: str, b: str) -> bool:
    """Equal, or a masked/short number that is the end of the full one ("..812444")."""
    if len(a) < 6 or len(b) < 6:
        return False
    if a == b:
        return True
    short, full = sorted((a, b), key=len)
    return len(short) < 9 and full.endswith(short)


def _accounts(s: Dict[str, Any]) -> List[str]:
    return [d for d in (_digits(s.get("bank_account_number")), _digits(s.get("account_number"))) if d]


def _names(s: Dict[str, Any]) -> List[str]:
    out = [s.get("name") or ""] + list(s.get("aliases") or [])
    return [n for n in out if n and not is_email(n)]


def _initials(short: List[str], long: List[str]) -> bool:
    if len(short) != 1 or not (3 <= len(short[0]) <= 5) or not short[0].isalpha() or len(long) < len(short[0]):
        return False
    return "".join(t[0] for t in long[:len(short[0])]) == short[0]


def same_name(a: str, b: str) -> bool:
    ca, cb = _core(a), _core(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short, long = sorted((ca, cb), key=len)
    if (short[0] == long[0] and len(short[0]) >= 4 and short[0] not in _TRADE
            and all(t in long for t in short)):
        return True
    # initials are read from the full name ("do it yourself"), legal words and all
    ta, tb = _tokens(a), _tokens(b)
    s2, l2 = sorted((ta, tb), key=len)
    return _initials(s2, l2)


def same_supplier(a: Dict[str, Any], b: Dict[str, Any], own_accounts: frozenset = frozenset()) -> Optional[str]:
    """Why records a and b are one business ("tax_id", "bank_account", "email", "name"), or None.
    `own_accounts`: the business's own bank accounts — a proof of payment can carry the payer's
    account where the payee's should be, and that must never make two suppliers one."""
    from vula.commerce.service import _norm_tax
    ta, tb = _norm_tax(a.get("tax_id") or ""), _norm_tax(b.get("tax_id") or "")
    if len(ta) >= 6 and ta == tb:
        return "tax_id"
    acc_a = [x for x in _accounts(a) if not any(_same_account(x, o) for o in own_accounts)]
    acc_b = [y for y in _accounts(b) if not any(_same_account(y, o) for o in own_accounts)]
    if any(_same_account(x, y) for x in acc_a for y in acc_b):
        return "bank_account"
    ea = {e.strip().lower() for e in [a.get("contact_email")] + [n for n in [a.get("name")] + list(a.get("aliases") or [])
                                                               if is_email(n)] if e and is_email(e)}
    eb = {e.strip().lower() for e in [b.get("contact_email")] + [n for n in [b.get("name")] + list(b.get("aliases") or [])
                                                               if is_email(n)] if e and is_email(e)}
    if ea & eb:
        return "email"
    if any(same_name(x, y) for x in _names(a) for y in _names(b)):
        return "name"
    return None


def split_own(suppliers: List[Dict[str, Any]], own_names: List[str],
              own_numbers: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], frozenset]:
    """(the real suppliers, the business's own bank accounts). Records that are the business itself
    ("DIGG", "Digg Demo" for DIGG) are never merged or matched — and any account on them is the
    business's own, so it never makes two suppliers one."""
    from vula.commerce.service import is_own_name
    others, own_acc = [], set()
    for s in suppliers:
        if own_names and is_own_name(s.get("name") or "", own_names, own_numbers, s.get("tax_id")):
            own_acc.update(_accounts(s))
        else:
            others.append(s)
    return others, frozenset(own_acc)


def find_existing(suppliers: List[Dict[str, Any]], candidate: Dict[str, Any],
                  own_accounts: frozenset = frozenset()) -> Optional[Tuple[Dict[str, Any], str]]:
    """The supplier on file that `candidate` (a would-be new record) duplicates, and why."""
    for s in suppliers:
        if candidate.get("id") and str(s.get("id")) == str(candidate["id"]):
            continue
        why = same_supplier(candidate, s, own_accounts)
        if why:
            return s, why
    return None


def find_duplicates(suppliers: List[Dict[str, Any]], own_accounts: frozenset = frozenset()) -> List[Dict[str, Any]]:
    """Groups of records that are one business: [{"keep": record, "duplicates": [records], "why": [..]}].
    The record kept is the one with the most details on it (then the oldest)."""
    n = len(suppliers)
    parent = list(range(n))
    why: Dict[Tuple[int, int], str] = {}

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            r = same_supplier(suppliers[i], suppliers[j], own_accounts)
            if r:
                why[(i, j)] = r
                parent[root(j)] = root(i)
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)

    def richness(s: Dict[str, Any]) -> tuple:
        filled = sum(1 for k in _FILL if s.get(k))
        return (-filled, -len(s.get("name") or ""), str(s.get("created_at") or ""))

    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        rows = sorted((suppliers[i] for i in members), key=richness)
        reasons = sorted({r for (i, j), r in why.items() if i in members and j in members})
        out.append({"keep": rows[0], "duplicates": rows[1:], "why": reasons})
    return sorted(out, key=lambda g: (g["keep"].get("name") or "").lower())


# ── merging ──────────────────────────────────────────────────────────────────────────────────

def merged_fields(keep: Dict[str, Any], dups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the kept record gains: the duplicates' names as aliases, its blanks filled, and a real
    expense account if it only had "general"."""
    names = {n.lower() for n in [keep.get("name") or ""]}
    aliases: List[str] = []
    for n in list(keep.get("aliases") or []) + [x for d in dups for x in [d.get("name")] + list(d.get("aliases") or [])]:
        if n and n.strip() and n.strip().lower() not in names:
            names.add(n.strip().lower())
            aliases.append(n.strip())
    up: Dict[str, Any] = {}
    if aliases != list(keep.get("aliases") or []):
        up["aliases"] = aliases
    for k in _FILL:
        if not keep.get(k):
            v = next((d.get(k) for d in dups if d.get(k)), None)
            if v:
                up[k] = v
    if (keep.get("category") or "general") == "general":
        cat = next((d.get("category") for d in dups if d.get("category") and d["category"] != "general"), None)
        if cat:
            up["category"] = cat
    return up


def merge(tenant_id: str, keep_id: str, duplicate_ids: List[str]) -> Dict[str, Any]:
    """Fold `duplicate_ids` into `keep_id` (all the tenant's). Returns {"ok", "kept", "merged",
    "moved": {table: n}, "aliases"} or {"ok": False, "error"}."""
    dup_ids = [str(d) for d in duplicate_ids if d and str(d) != str(keep_id)]
    if not dup_ids:
        return {"ok": False, "error": "Nothing to merge."}
    db = _client()
    rows = (db.table("commerce_suppliers").select("*").eq("tenant_id", tenant_id)
            .in_("id", [str(keep_id)] + dup_ids).execute().data or [])
    by_id = {str(r["id"]): r for r in rows}
    if str(keep_id) not in by_id or any(d not in by_id for d in dup_ids):
        return {"ok": False, "error": "Supplier not found."}
    keep, dups = by_id[str(keep_id)], [by_id[d] for d in dup_ids]

    moved: Dict[str, int] = {}
    for table, col, _uuid in REFERENCES:
        try:
            res = (db.table(table).update({col: str(keep_id)}).eq("tenant_id", tenant_id)
                   .in_(col, dup_ids).execute())
            if res.data:
                moved[table] = len(res.data)
        except Exception as exc:  # noqa: BLE001 — a table missing in this install is not a failure
            log.debug("supplier merge: %s.%s skipped: %s", table, col, exc)

    up = merged_fields(keep, dups)
    if up:
        db.table("commerce_suppliers").update(up).eq("tenant_id", tenant_id).eq("id", str(keep_id)).execute()

    # Delete a duplicate only once nothing points at it any more.
    left = still_referenced(tenant_id, dup_ids)
    gone = [d for d in dup_ids if d not in left]
    if gone:
        db.table("commerce_suppliers").delete().eq("tenant_id", tenant_id).in_("id", gone).execute()

    back = (db.table("commerce_suppliers").select("id,name,aliases").eq("tenant_id", tenant_id)
            .in_("id", [str(keep_id)] + dup_ids).execute().data or [])
    ids_back = {str(r["id"]) for r in back}
    kept_row = next((r for r in back if str(r["id"]) == str(keep_id)), {})
    ok = str(keep_id) in ids_back and not any(d in ids_back for d in gone) and not left
    return {"ok": ok, "kept": keep.get("name"), "merged": [by_id[d].get("name") for d in gone],
            "moved": moved, "aliases": kept_row.get("aliases") or [],
            **({} if ok else {"error": "Some duplicates are still in use and were kept: "
                              + ", ".join(by_id[d].get("name") or d for d in left) if left
                              else "The merge didn't read back — please check the supplier list."})}


def still_referenced(tenant_id: str, ids: List[str]) -> set:
    """The ids that some bill, expense, document, order or product still points at."""
    db = _client()
    left: set = set()
    for table, col, _uuid in REFERENCES:
        try:
            rows = (db.table(table).select(col).eq("tenant_id", tenant_id).in_(col, ids)
                    .limit(1000).execute().data or [])
            left |= {str(r.get(col)) for r in rows if r.get(col)}
        except Exception as exc:  # noqa: BLE001
            log.debug("supplier reference check: %s.%s skipped: %s", table, col, exc)
    return left


def merge_summary(res: Dict[str, Any]) -> str:
    if not res.get("ok") and not res.get("merged"):
        return res.get("error") or "Nothing was merged."
    moved = sum((res.get("moved") or {}).values())
    text = (f"Merged {', '.join(res['merged'])} into {res['kept']}"
            + (f" — {moved} record{'s' if moved != 1 else ''} moved over." if moved else "."))
    return text + (f" {res['error']}" if res.get("error") else "")
