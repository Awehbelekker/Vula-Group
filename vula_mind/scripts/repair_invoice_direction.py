"""Re-file invoices the tenant ISSUED that were recorded as supplier bills.

2026-09-03: the scan-commit path hardcoded direction="inbound", so every document scanned from
email became a supplier bill — including the tenant's own outgoing sales invoices when a copy
passed through the mailbox. On real data this is 51 off-the-hook invoices totalling R32,307.97:
both `supplier` and `customer_name` read "Off the Hook".

The effect is wrong in BOTH directions at once — money out overstated by R32,307.97 and money in
understated by the same — which is exactly the error a cash-flow or payables report must not
inherit. The write is fixed (service.classify_direction); this corrects the rows already stored.

Deliberately narrow and conservative:
  • Only rows where direction='inbound' AND the issuer IS this tenant (same-business match).
  • Verified against production before writing: 51 rows on off-the-hook, ZERO on digg-demo and
    gerflor. An earlier check that appeared to show R5,019,897 on DIGG was an artefact of using
    a made-up tenant name; this script always reads the tenant's real display_name.
  • Invoice NUMBERS are left exactly as they are. Those documents may already have been sent or
    referenced, and a number that changes after the fact is worse than a number in the wrong
    series (same reasoning as the DIG-INV numbering repair).
  • Dry-run by default.

Run:  PYTHONPATH=. railway run python scripts/repair_invoice_direction.py [--confirm] [tenant...]
"""
import sys

from vula.commerce import service
from vula.api.tenants import get_config


def main() -> int:
    confirm = "--confirm" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    db = service._client()

    try:
        tenants = [r["tenant_id"] for r in
                   (db.table("vula_tenant_config").select("tenant_id").execute().data or [])]
    except Exception as exc:
        print("couldn't list tenants:", str(exc)[:120])
        return 1
    if only:
        tenants = [t for t in tenants if t in only]

    grand, grand_amt = 0, 0
    for tid in tenants:
        tname = (get_config(tid) or {}).get("display_name") or tid
        try:
            rows = (db.table("commerce_invoices")
                    .select("id,invoice_number,supplier,customer_name,total_cents,issue_date")
                    .eq("tenant_id", tid).eq("direction", "inbound")
                    .limit(2000).execute().data or [])
        except Exception as exc:
            print(f"{tid}: read failed — {str(exc)[:100]}")
            continue

        misfiled = [r for r in rows
                    if service._same_business(r.get("supplier") or "", tname)
                    or service._same_business(r.get("supplier") or "", tid)]
        if not misfiled:
            print(f"{tid:<16} ({tname}) — nothing to re-file")
            continue

        amt = sum((r.get("total_cents") or 0) for r in misfiled)
        grand += len(misfiled)
        grand_amt += amt
        print(f"\n{tid:<16} ({tname})")
        print(f"  {len(misfiled)} invoice(s), R{amt/100:,.2f} — issued by this business, "
              f"currently filed as supplier bills")
        for r in misfiled[:6]:
            print(f"    {str(r.get('issue_date'))[:10]}  {r.get('invoice_number')}  "
                  f"R{(r.get('total_cents') or 0)/100:>10,.2f}  issuer={r.get('supplier')!r}")
        if len(misfiled) > 6:
            print(f"    ... and {len(misfiled) - 6} more")

        if confirm:
            for r in misfiled:
                db.table("commerce_invoices").update({"direction": "outbound"}) \
                    .eq("id", r["id"]).execute()
            print(f"  re-filed {len(misfiled)} as outbound")

    print(f"\n{'Re-filed' if confirm else 'Would re-file'}: {grand} invoice(s), "
          f"R{grand_amt/100:,.2f}")
    if not confirm and grand:
        print("Dry run — re-run with --confirm to write. Invoice numbers are never changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
