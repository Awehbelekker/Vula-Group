"""The dry run of rebuilding one business's books into the one trusted book. Reads only; writes nothing.

    python tools/books_rebuild.py digg-demo --from 2026-03-01          # plain-text summary
    python tools/books_rebuild.py digg-demo --from 2026-03-01 --json   # the full report

Committing is a Master route (POST /v1/master/books/{tenant}/rebuild with commit=true and the dry
run's plan_hash), for a business whose books audit is signed off. See vula/commerce/books_rebuild.py.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _r(c) -> str:
    return "—" if c is None else f"R{c / 100:,.2f}"


def text(rep: dict) -> str:
    a, tb, ctl = rep["actions"], rep["trial_balance"], rep["control"]
    out = [f"Rebuild dry run for {rep['tenant_id']} (accrual from {rep.get('accrual_from') or 'the start'})",
           f"  would post {a['new']} new, correct {a['changed']}, take out {a['remove']}, leave {a['unchanged']} as they are",
           "  by source: " + ", ".join(f"{k} {v['count']}" for k, v in sorted(a["by_source"].items())),
           "", "Trial balance after (accrual view):"]
    for code, net in tb["after"]["accrual"]["accounts"].items():
        out.append(f"  {code:<26} {'Dr' if net > 0 else 'Cr'} {_r(abs(net))}")
    out.append("  balanced: " + ", ".join(f"{v} {'yes' if tb['after'][v]['balanced'] else 'NO'}" for v in tb["after"]))
    out += ["", f"Debtors: books {_r(ctl['debtors_books_cents'])}, still owed {_r(ctl['debtors_owed_cents'])}"
                f" — {'tie' if ctl['debtors_tie'] else 'DO NOT TIE'}",
            f"Creditors: books {_r(ctl['creditors_books_cents'])}, still owed {_r(ctl['creditors_owed_cents'])}"
            f" — {'tie' if ctl['creditors_tie'] else 'DO NOT TIE'}",
            f"Suspense (to review): {rep['suspense']['lines']} lines, {_r(rep['suspense']['cents'])}",
            f"Money in transit (payments with no bank line, or the other way): {_r(rep['in_transit_cents'])}", "",
            "Bank reconciliation:"]
    rec = rep["bank_reconciliation"]
    for acct in rec["accounts"]:
        out.append(f"  {acct['account']} {acct['from']}–{acct['until']}: statement {_r(acct['statement_cents'])}, "
                   f"books {_r(acct['books_cents'])}, difference {_r(acct['difference_cents'])}")
    un = rec["unassigned"]
    if un["lines"]:
        out.append(f"  {un['lines']} lines on no account number (in {_r(un['in_cents'])}, out {_r(un['out_cents'])}) — "
                   "can't be reconciled until the official statements tag them")
    if rep.get("audit", {}).get("problems"):
        out += ["", "Books audit — still to fix:"] + [f"  {p['headline']}" for p in rep["audit"]["problems"]]
    out += ["", f"plan_hash {rep['plan_hash']}"]
    return "\n".join(out)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    start = None
    if "--from" in sys.argv:
        start = sys.argv[sys.argv.index("--from") + 1]
        args = [a for a in args if a != start]
    from vula.commerce import books_rebuild
    rep = books_rebuild.public(books_rebuild.plan(args[0], start))
    print(json.dumps(rep, indent=1, default=str) if "--json" in sys.argv else text(rep))


if __name__ == "__main__":
    main()
