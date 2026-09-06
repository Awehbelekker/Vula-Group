"""Delete learned categorisation rules that key on a word instead of a merchant.

2026-09-06. learn_category_rule used to explode every owner correction into up to 8 single-token
rules, and lookup_learned_category picked the highest-`hits` match with no signal-type weighting.
off-the-hook's live table therefore contained:

    2 hits  token 'received'      -> owner_drawings   ("Payment Received: ..." = 404 money-IN rows)
    1 hits  token 'received'      -> bank_cash        (contradicting the above, same signal)
    1 hits  token 'milnerton'     -> cost_of_sales    (a suburb)
    1 hits  token 'table'/'bay'/'city'/'mall' -> sales
    1 hits  token 'uncategorised' -> cost_of_sales    (a placeholder word)

One generic word could outrank every good allocation sharing it. The write path is fixed; this
clears what it already wrote.

Deletes a rule ONLY when:
  * its signal is a known generic/place/channel word, or
  * two rules share a signal but disagree on the account (unresolvable — the owner re-teaches
    it next time they correct one).

An earlier draft also deleted every single-token rule, on the theory that the new keying always
writes two. Dry-running that against production would have removed 134 of 137 rules including
real ones — 'atlantis' -> cost_of_sales, 'boxshop' -> packaging, 'edison' -> cost_of_sales (a
subcontractor DIGG pays). A one-word merchant name is perfectly legitimate; only the meaningless
signals go.

Dry run by default, like scripts/repair_invoice_direction.py. Pass --apply to delete.
"""
import argparse
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from vula.commerce import service  # noqa: E402

# Words that describe a transaction's SHAPE or WHERE it happened, never who it was with.
# Kept in sync in spirit with merchants._CHANNEL / _NON_IDENTITY, but this list is only ever
# used to DELETE, so it stays conservative: anything that could plausibly be a trading name
# (however generic-sounding) is left alone for the owner to correct.
GENERIC = {
    "received", "external", "internal", "immediate", "payment", "payments", "transfer",
    "uncategorised", "uncategorized", "purchase", "debit", "credit", "order", "primed",
    "app", "online", "mobile", "atm", "cash", "money", "account", "acc", "settlement",
    "pmt", "rtc", "eft", "payshap", "fnb", "absa", "nedbank", "capitec", "banking",
    "table", "bay", "city", "mall", "centre", "center", "square", "street", "road", "north",
    "south", "east", "west", "milnerton", "tableview", "blouberg", "parklands", "edgemead",
    "sandown", "century", "sunningdale", "melkbos", "durbanville", "claremont", "gardens",
    "proof", "whatsapp", "misc", "general", "tip",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", help="limit to one tenant")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = ap.parse_args()

    db = service._client()
    q = db.table("commerce_txn_rules").select("*")
    if args.tenant:
        q = q.eq("tenant_id", args.tenant)
    rows = q.limit(5000).execute().data or []
    print(f"{len(rows)} rules loaded")

    by_sig = defaultdict(set)
    for r in rows:
        by_sig[(r.get("tenant_id"), r.get("signal"))].add(r.get("account_code"))
    contradictory = {k for k, v in by_sig.items() if len(v) > 1}

    doomed = []
    for r in rows:
        sig = (r.get("signal") or "").strip().lower()
        why = None
        if (r.get("tenant_id"), r.get("signal")) in contradictory:
            why = "contradictory rules on the same signal"
        elif sig in GENERIC:
            why = "generic/place/channel word"
        if why:
            doomed.append((r, why))

    print(f"\n{len(doomed)} rules would be deleted:")
    for r, why in doomed:
        print(f"  {r.get('tenant_id'):<14} {str(r.get('signal_type')):<10} "
              f"{str(r.get('signal'))!r:<26} -> {r.get('account_code'):<16} [{why}]")

    keep = len(rows) - len(doomed)
    print(f"\n{keep} rules would remain.")
    if not args.apply:
        print("\nDRY RUN — pass --apply to delete.")
        return 0
    deleted = 0
    for r, _ in doomed:
        try:
            db.table("commerce_txn_rules").delete().eq("id", r["id"]).execute()
            deleted += 1
        except Exception as exc:
            print(f"  failed to delete {r.get('id')}: {exc}")
    print(f"\ndeleted {deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
