"""Backfill Qdrant embeddings for already-approved learned answers.

vula/escalation.py::embed_learned_answer() now runs automatically the moment an owner approves
a new learned answer (Tenant Mind Phase 2, 2026-09-15) — but any answer approved BEFORE that
shipped has no embedding yet, so it's only reachable via the plain keyword-overlap fallback
until re-approved (which nobody will do — it's already approved). This is the one-time catch-up
for that existing backlog. Safe to re-run: embedding is idempotent (same doc_id = same Qdrant
point, upserted in place), so running this twice just re-embeds the same rows harmlessly.

Dry-run by default; pass --confirm to actually write.

Run:  PYTHONPATH=. railway run python scripts/backfill_learned_answer_embeddings.py [--confirm] [tenant_id ...]
"""
import asyncio
import sys

from vula.commerce import service


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    confirm = "--confirm" in sys.argv
    db = service._client()

    q = db.table("vula_learned_answers").select("id,tenant_id,question").eq("status", "approved")
    rows = q.execute().data or []
    if args:
        rows = [r for r in rows if r.get("tenant_id") in args]

    if not rows:
        print("Nothing to backfill — no approved learned answers found"
             + (f" for {args}" if args else "") + ".")
        return 0

    print(f"{len(rows)} approved learned answer(s) to embed:")
    for r in rows:
        print(f"  {r['tenant_id']}: {r['question'][:70]!r}")

    if not confirm:
        print("\nDry run — pass --confirm to actually write embeddings.")
        return 0

    from vula import escalation as esc
    ok, failed = 0, 0
    for r in rows:
        try:
            await esc.embed_learned_answer(r["tenant_id"], r["id"], r["question"])
            ok += 1
        except Exception as exc:
            failed += 1
            print(f"  FAILED {r['tenant_id']}/{r['id']}: {exc}")

    print(f"\nEmbedded {ok}, failed {failed}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
