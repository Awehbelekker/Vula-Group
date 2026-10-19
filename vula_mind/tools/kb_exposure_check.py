"""Did past customer questions reach staff/owner documents? (KB retrieval 1b, 2026-10-17.)

Until C5 the customer assistant — and, on a knowledge-mode line, any sender not on the team —
searched the whole tenant KB, which holds supplier invoices, bank statements and wage rosters.
Which chunks a past turn used was never logged, so this RECONSTRUCTS it: each past customer
question is re-run through the retrieval those paths used then (no audience filter, the customer
assistant's top 4, no LLM rewrites), the documents that come back are classified with the
backfill's rules, and the reply actually sent is checked for a rand figure that appears in one of
those private chunks — the strongest sign something was disclosed.

    python tools/kb_exposure_check.py --tenant off-the-hook --since 2026-08-01
    python tools/kb_exposure_check.py --tenant digg-demo --since 2026-08-01 --show-text

Approximate by nature: the KB today is not the KB on the day. Read-only. Prints counts, document
filenames and turn ids; customer phones are never printed, and question/reply text only with
--show-text (for reading on your own machine — don't paste it anywhere). Not legal advice: this
gives the facts a POPIA breach assessment needs, it doesn't make the assessment.

Sources: commerce_conversation_messages (customer sessions — session_key not "admin:…" (WhatsApp
admin agent) or "webadmin:…" (the dashboard's admin assistant, also staff)) and
vula_turns (every WhatsApp message since 2026-10-06; senders not on the team).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_RAND = re.compile(r"R\s?(\d{1,3}(?:[ ,]\d{3})+|\d{2,})(?:[.,](\d{2}))?\b")


def rand_figures(text: str) -> set:
    """Rand amounts as integer cents, R10 and up (smaller figures are counts/units, not money)."""
    out = set()
    for m in _RAND.finditer(text or ""):
        cents = int(re.sub(r"[ ,]", "", m.group(1))) * 100 + int(m.group(2) or 0)
        if cents >= 1000:
            out.add(cents)
    return out


def is_staff_session(session_key) -> bool:
    """admin:<phone> is the WhatsApp admin agent; webadmin:<id> the dashboard's admin assistant
    (commerce.admin_assistant). Both are staff — counting webadmin as customers (2026-10-17)
    inflated the exposure numbers."""
    return str(session_key or "").startswith(("admin:", "webadmin:"))


def _hash(phone: str) -> str:
    return hashlib.sha256((phone or "").encode()).hexdigest()[:10]


def customer_turns(tenant_id: str, since: str, limit: int) -> List[Dict[str, Any]]:
    """[{id, source, phone_hash, question, reply}] — customers only, oldest first."""
    from vula.commerce import service
    db = service._client()
    out: List[Dict[str, Any]] = []
    try:
        sessions = (db.table("commerce_conversation_sessions").select("id,session_key")
                    .eq("tenant_id", tenant_id).execute().data or [])
        cust = {s["id"]: s["session_key"] for s in sessions
                if not is_staff_session(s.get("session_key"))}
        for sid, key in cust.items():
            msgs = (db.table("commerce_conversation_messages").select("id,role,content,created_at")
                    .eq("session_id", sid).gte("created_at", since).order("created_at")
                    .execute().data or [])
            for i, m in enumerate(msgs):
                if m["role"] != "user":
                    continue
                reply = next((n["content"] for n in msgs[i + 1:] if n["role"] == "assistant"), "")
                out.append({"id": m["id"], "source": "commerce", "phone_hash": _hash(key),
                            "question": m["content"], "reply": reply, "at": m["created_at"]})
    except Exception as exc:  # noqa: BLE001
        print(f"  (commerce conversations unavailable: {type(exc).__name__}: {str(exc)[:120]})")
    try:
        from vula.api.whatsapp import _is_tenant_owner
        rows = (db.table("vula_turns").select("id,phone,kind,text,replies,started_at")
                .eq("tenant_id", tenant_id).eq("kind", "text").gte("started_at", since)
                .order("started_at").limit(limit * 3).execute().data or [])
        team: Dict[str, bool] = {}
        for t in rows:
            ph = t.get("phone") or ""
            if ph not in team:
                team[ph] = _is_tenant_owner(tenant_id, ph)
            if team[ph] or not (t.get("text") or "").strip():
                continue
            reply = " ".join(str(r.get("text") or "") for r in (t.get("replies") or []) if isinstance(r, dict))
            out.append({"id": t["id"], "source": "turns", "phone_hash": _hash(ph),
                        "question": t["text"], "reply": reply, "at": t["started_at"]})
    except Exception as exc:  # noqa: BLE001
        print(f"  (vula_turns unavailable: {type(exc).__name__}: {str(exc)[:120]})")
    seen, uniq = set(), []
    for t in sorted(out, key=lambda x: x["at"] or ""):
        k = (t["phone_hash"], t["question"].strip().lower())
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq[:limit]


async def check(tenant_id: str, turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    from tools.kb_audience_backfill import _categories, classify
    from vula.ingestion.pipeline import VulaIngestionPipeline, kb_audience
    p = VulaIngestionPipeline(tenant_id=tenant_id)
    rep: Dict[str, Any] = {"tenant": tenant_id, "questions": len(turns), "reached_private": 0,
                           "figure_overlap": 0, "documents": {}, "turns": []}
    cat_cache: Dict[str, str] = {}
    for t in turns:
        with kb_audience(None):   # the pre-fix behaviour, deliberately: no audience filter
            chunks = await p.query(t["question"], top_k=4, expand=False)
        missing = [str(c.get("doc_id")) for c in chunks if c.get("doc_id") and str(c.get("doc_id")) not in cat_cache]
        if missing:
            cat_cache.update(_categories(tenant_id, missing))
        private = []
        for c in chunks:
            aud = c.get("audience") or classify(c, cat_cache.get(str(c.get("doc_id"))))[0]
            if aud in ("staff", "owner"):
                private.append((c, aud))
        if not private:
            continue
        rep["reached_private"] += 1
        overlap = rand_figures(t["reply"]) & set().union(*(rand_figures(c.get("text") or "") for c, _ in private))
        if overlap:
            rep["figure_overlap"] += 1
        for c, aud in private:
            f = c.get("filename") or "?"
            d = rep["documents"].setdefault(f, {"audience": aud, "times": 0})
            d["times"] += 1
        rep["turns"].append({"id": t["id"], "source": t["source"], "at": t["at"],
                             "customer": t["phone_hash"], "docs": sorted({c.get("filename") for c, _ in private}),
                             "figures_in_reply": sorted(overlap), "question": t["question"],
                             "reply": t["reply"]})
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--show-text", action="store_true")
    a = ap.parse_args()
    from core.llm_router import install_ollama_auth
    install_ollama_auth()
    turns = customer_turns(a.tenant, a.since, a.limit)
    rep = asyncio.run(check(a.tenant, turns))
    print(f"{a.tenant} since {a.since}: {rep['questions']} customer questions re-run; "
          f"{rep['reached_private']} would have retrieved a staff/owner document; "
          f"{rep['figure_overlap']} replies contain a rand figure from such a document")
    for f, d in sorted(rep["documents"].items(), key=lambda x: -x[1]["times"]):
        print(f"  {d['times']:4}x  {d['audience']:5}  {f}")
    for t in rep["turns"]:
        flag = f"  figures {['R%.2f' % (c / 100) for c in t['figures_in_reply']]}" if t["figures_in_reply"] else ""
        print(f"  turn {t['id']} ({t['source']}, {t['at']}, customer {t['customer']}): {', '.join(t['docs'])}{flag}")
        if a.show_text:
            print(f"      Q: {t['question'][:300]}\n      A: {t['reply'][:500]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
