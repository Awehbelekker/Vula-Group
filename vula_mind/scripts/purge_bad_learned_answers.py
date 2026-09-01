"""One-off: remove the two learned answers found to be wrong in the 2026-09-01 audit.

Both were live and being served to real customers:

  Q: "What is in the family fish box?"
  A: "Yes I can do"                    <- the helper replying about something else entirely

  Q: "Do you deliver to Timbuktu"
  A: "Respond to Richard Downing via WhatsApp business and say delivery will be on Monday
      between 10:00 - 12:00"           <- the helper instructing Vula, naming a real customer

Probing production, "do you deliver to Milnerton" returned the Timbuktu answer.

The guards shipped alongside this (entity matching + the approved-only gate in
escalation.find_learned_answer) already stop both from being served, so this is cleanup rather
than the fix. Deletes only these two exact rows, by id, after showing them — it will never
touch an answer that isn't one of them.

Run:  railway run python scripts/purge_bad_learned_answers.py --confirm
"""
import sys

from vula.commerce import service

TARGETS = [
    ("What is in the family fish box?", "Yes I can do"),
    ("Do you deliver to Timbuktu", "Respond to Richard Downing"),
]


def main() -> int:
    db = service._client()
    rows = (db.table("vula_learned_answers").select("*")
            .eq("tenant_id", "off-the-hook").execute().data or [])
    print(f"off-the-hook learned answers on file: {len(rows)}\n")

    doomed = []
    for r in rows:
        q, a = (r.get("question") or "").strip(), (r.get("answer") or "").strip()
        for tq, ta in TARGETS:
            if q.rstrip("?").lower() == tq.rstrip("?").lower() and a.startswith(ta):
                doomed.append(r)
                break

    for r in doomed:
        print(f"  WILL DELETE {r['id']}")
        print(f"     Q: {r.get('question')}")
        print(f"     A: {str(r.get('answer'))[:100]}")
    for r in rows:
        if r not in doomed:
            print(f"  keeping    {r['id']}: {str(r.get('question'))[:60]}")

    if not doomed:
        print("\nNothing matched — already cleaned up.")
        return 0
    if "--confirm" not in sys.argv:
        print(f"\n{len(doomed)} row(s) matched. Re-run with --confirm to delete.")
        return 0

    for r in doomed:
        db.table("vula_learned_answers").delete().eq("id", r["id"]).execute()
        print(f"deleted {r['id']}")
    print(f"\nDone — {len(doomed)} row(s) removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
