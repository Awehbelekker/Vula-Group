"""Apply pending tone suggestions: persona_prompt_suggested -> persona_prompt.

vula/commerce/voice_profile.py learns a tenant's real writing tone and stores it as a SUGGESTION
only, deliberately never auto-applying it. Audited 2026-09-01: every tenant had suggestions
sitting unaccepted (gerflor's for a month), so every tenant was answering customers in Vula's
generic default voice. This is the accept step, normally a dashboard click.

Only fills an EMPTY persona_prompt — a tone someone has already written or edited by hand is
never overwritten. Dry-run by default; pass --confirm to write.

Run:  PYTHONPATH=. railway run python scripts/apply_voice_profiles.py [--confirm] [tenant_id ...]
"""
import sys

from vula.commerce import service


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    confirm = "--confirm" in sys.argv
    db = service._client()

    rows = (db.table("vula_tenant_config")
            .select("tenant_id,persona_prompt,persona_prompt_suggested,persona_prompt_suggested_at")
            .execute().data or [])
    if args:
        rows = [r for r in rows if r.get("tenant_id") in args]

    to_apply, skipped = [], []
    for r in rows:
        tid = r.get("tenant_id")
        applied = (r.get("persona_prompt") or "").strip()
        sugg = (r.get("persona_prompt_suggested") or "").strip()
        if not sugg:
            skipped.append((tid, "no suggestion on file"))
        elif applied:
            skipped.append((tid, "already has a tone applied — not overwriting"))
        else:
            to_apply.append((tid, sugg))

    for tid, why in skipped:
        print(f"  skip  {tid}: {why}")
    print()
    for tid, sugg in to_apply:
        print(f"  APPLY {tid}:")
        print(f"        {sugg.encode('ascii', 'replace').decode('ascii')[:400]}")
        print()

    if not to_apply:
        print("Nothing to apply.")
        return 0
    if not confirm:
        print(f"{len(to_apply)} tenant(s) ready. Re-run with --confirm to apply.")
        return 0

    for tid, sugg in to_apply:
        db.table("vula_tenant_config").update(
            {"persona_prompt": sugg}).eq("tenant_id", tid).execute()
        print(f"applied -> {tid}")
    print(f"\nDone — {len(to_apply)} tenant(s) now answering in their own voice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
