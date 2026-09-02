"""Repair line_items that were stored as a JSON string instead of a list.

2026-09-02, real DIGG incident: the document-scan commit path wrote json.dumps(line_items) while
every other write path stored the list itself. The column holds JSON, so the dumped string was
stored as a JSON *string* — reading it back gives a string, which len() and iteration treat
character by character. A R1,599.90 Caisson invoice came back with 260 "line items":
'[', '{', '"', 'd', 'e', 's', 'c', ... and the line totals summed to R0.00 against a real
R1,599.90 invoice.

Every email-scanned invoice and expense since that path shipped is affected. The write is fixed
(service._coerce_line_items); this repairs the rows already stored.

Non-destructive: only rewrites rows whose line_items is a STRING that parses to a list, and only
ever replaces it with that parsed list. Rows already holding a list are untouched, and a string
that won't parse is reported, never guessed at. Dry-run by default.

Run:  PYTHONPATH=. railway run python scripts/repair_line_items.py [--confirm]
"""
import json
import sys

from vula.commerce import service

TABLES = ("commerce_invoices", "commerce_expenses")


def main() -> int:
    confirm = "--confirm" in sys.argv
    db = service._client()
    grand = fixed = unparseable = 0

    for table in TABLES:
        try:
            rows = (db.table(table).select("id,tenant_id,line_items")
                    .limit(5000).execute().data or [])
        except Exception as exc:
            print(f"{table}: read failed — {str(exc)[:120]}")
            continue

        broken = [r for r in rows if isinstance(r.get("line_items"), str)
                  and r["line_items"].strip()]
        print(f"\n{table}: {len(rows)} rows, {len(broken)} with a STRING line_items")
        grand += len(broken)

        for r in broken:
            try:
                parsed = json.loads(r["line_items"])
            except Exception:
                unparseable += 1
                print(f"  ! {r['id']} ({r.get('tenant_id')}): unparseable "
                      f"({len(r['line_items'])} chars) — left alone")
                continue
            if not isinstance(parsed, list):
                unparseable += 1
                print(f"  ! {r['id']}: parsed to {type(parsed).__name__}, not a list — left alone")
                continue
            items = [i for i in parsed if isinstance(i, dict)]
            if confirm:
                db.table(table).update({"line_items": items}).eq("id", r["id"]).execute()
            fixed += 1
            if fixed <= 8 or confirm:
                print(f"  {'fixed' if confirm else 'would fix'} {r['id']} "
                      f"({r.get('tenant_id')}): {len(r['line_items'])} chars -> "
                      f"{len(items)} line item(s)")

    print(f"\n{'Repaired' if confirm else 'Would repair'}: {fixed} row(s)"
          f"   left alone (unparseable): {unparseable}   total broken: {grand}")
    if not confirm and fixed:
        print("Dry run — re-run with --confirm to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
