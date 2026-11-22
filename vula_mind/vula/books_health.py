"""Books health — the one trusted book checked every day, loudly (18 Nov 2026, Phase 1 PR 3).

Ian: "Vula does not drop the ball." For every business on books_v2, on the scheduler leader:
  * hourly: a posting that failed (journal_post_failures) is tried again from what was kept, until it
    goes in (resolved_at) or has been tried MAX_ATTEMPTS times;
  * daily: a rebuild dry run (books_rebuild.plan, which writes nothing) must show
      - both views balanced,
      - nothing missing or different (every document and live bank line has its entry),
      - each bank account reconciled to its last statement,
      - debtors and creditors tied to what's still owed on open invoices and bills,
      - no posting left failed;
    and lines in suspense are counted (a warning to review, not a failure).
One vula_health_events row per check (kind 'books_check', the business's own tenant_id). A check going
from ok to failing, or back, alerts the operator like AI health; Master › Health shows `status()`.
Only names, counts and rand totals are recorded or sent — never what a line says.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

log = logging.getLogger(__name__)

KIND = "books_check"
RETRY_EVERY_S = 3600
CHECK_EVERY_S = 24 * 3600
MAX_ATTEMPTS = 10
LABELS = {"balanced": "both views of the books balance",
          "complete": "every document and bank line is in the books",
          "bank": "each bank account reconciles to its statement",
          "debtors": "debtors tie to what customers still owe",
          "creditors": "creditors tie to what is still owed to suppliers",
          "failures": "no posting left failed"}

_state: Dict[str, Dict[str, Any]] = {}
_restored = False


def _client():
    from vula.commerce import service
    return service._client()


def _money(c: int) -> str:
    return f"R{c / 100:,.2f}"


def businesses() -> List[str]:
    """The businesses keeping the one trusted book."""
    from vula import features
    try:
        rows = _client().table("vula_tenant_config").select("tenant_id,features").limit(2000).execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("books health: businesses not listed: %s", exc)
        return []
    everyone = features._global("books_v2")
    return sorted(r["tenant_id"] for r in rows if r.get("tenant_id") and
                  (everyone or bool((r.get("features") or {}).get("books_v2"))))


def retry_failures(tenant_id: str) -> Dict[str, int]:
    """Try each open failed posting again, exactly as it was asked for."""
    out = {"retried": 0, "resolved": 0}
    db = _client()
    try:
        rows = (db.table("journal_post_failures").select("id,payload,attempts").eq("tenant_id", tenant_id)
                .is_("resolved_at", "null").order("created_at").limit(200).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("books health: failures not read for %s: %s", tenant_id, exc)
        return out
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        if int(r.get("attempts") or 1) >= MAX_ATTEMPTS or not isinstance(r.get("payload"), dict):
            continue
        out["retried"] += 1
        try:
            from vula.commerce import ledger_v2 as lv
            lv.check(r["payload"].get("p_lines") or [])
            db.rpc("post_journal_entry_v2", r["payload"]).execute()
            db.table("journal_post_failures").update({"resolved_at": now, "attempts": int(r.get("attempts") or 1) + 1}) \
                .eq("tenant_id", tenant_id).eq("id", r["id"]).execute()
            out["resolved"] += 1
        except Exception as exc:  # noqa: BLE001
            db.table("journal_post_failures").update({"attempts": int(r.get("attempts") or 1) + 1,
                                                      "error": str(exc)[:500]}) \
                .eq("tenant_id", tenant_id).eq("id", r["id"]).execute()
    return out


def checks(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce import books_rebuild, ledger_v2 as lv
    rep = books_rebuild.plan(tenant_id, audit=False)
    a, tb, ctl, rec = rep["actions"], rep["trial_balance"]["after"], rep["control"], rep["bank_reconciliation"]
    left = a["new"] + a["changed"] + a["remove"]
    off = [x for x in rec["accounts"] if not x["reconciles"]]
    fails = len(lv.open_failures(tenant_id))
    out = [
        {"name": "balanced", "ok": all(tb[v]["balanced"] for v in lv.VIEWS),
         "why": ", ".join(v for v in lv.VIEWS if not tb[v]["balanced"]) and "unbalanced: " +
                ", ".join(v for v in lv.VIEWS if not tb[v]["balanced"])},
        {"name": "complete", "ok": left == 0,
         "why": f"{a['new']} missing, {a['changed']} different, {a['remove']} no longer standing" if left else ""},
        {"name": "bank", "ok": not off,
         "why": "; ".join(f"{x['account']} off by {_money(x['difference_cents'] or 0)}" if x["difference_cents"] is not None
                          else f"{x['account']} has no statement balance" for x in off)},
        {"name": "debtors", "ok": ctl["debtors_tie"],
         "why": "" if ctl["debtors_tie"] else f"books {_money(ctl['debtors_books_cents'])}, owed {_money(ctl['debtors_owed_cents'])}"},
        {"name": "creditors", "ok": ctl["creditors_tie"],
         "why": "" if ctl["creditors_tie"] else f"books {_money(ctl['creditors_books_cents'])}, owed {_money(ctl['creditors_owed_cents'])}"},
        {"name": "failures", "ok": fails == 0, "why": f"{fails} postings failed" if fails else ""},
    ]
    for c in out:
        c["tenant_id"] = tenant_id
    out[0]["warnings"] = {"suspense_lines": rep["suspense"]["lines"], "suspense_cents": rep["suspense"]["cents"],
                          "in_transit_cents": rep["in_transit_cents"],
                          "lines_on_no_account": rec["unassigned"]["lines"]}
    return out


def _restore() -> None:
    global _restored
    _restored = True
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        rows = (_client().table("vula_health_events").select("tenant_id,detail,created_at").eq("kind", KIND)
                .gte("created_at", since).order("created_at", desc=True).limit(500).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("books health state not restored: %s", exc)
        return
    for r in reversed(rows):
        d = r.get("detail") or {}
        if d.get("name"):
            _state[f"{r['tenant_id']}:{d['name']}"] = {"ok": bool(d.get("ok")), "since": d.get("since") or r["created_at"]}


async def _alert(text: str) -> None:
    from vula.ai_health import _alert as send
    sent = await send(text)
    log.warning("%s (whatsapp sent=%s, email sent=%s)", text, sent.get("whatsapp"), sent.get("email"))


async def tick(run_checks: bool = True) -> List[Dict[str, Any]]:
    """One run: retry failures for every business; with run_checks, the daily checks. Returns the
    checks that changed state (each alerted)."""
    from core.mass_mind.health import record_event
    if not _restored:
        await asyncio.to_thread(_restore)
    changed: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    for tid in await asyncio.to_thread(businesses):
        await asyncio.to_thread(retry_failures, tid)
        if not run_checks:
            continue
        try:
            results = await asyncio.to_thread(checks, tid)
        except Exception as exc:  # noqa: BLE001
            results = [{"tenant_id": tid, "name": "complete", "ok": False, "why": f"couldn't check: {type(exc).__name__}"}]
        for c in results:
            key = f"{tid}:{c['name']}"
            prev = _state.get(key)
            flipped = (prev is None and not c["ok"]) or (prev is not None and prev["ok"] != c["ok"])
            since = now if (prev is None or flipped) else prev.get("since") or now
            _state[key] = {"ok": c["ok"], "since": since}
            await asyncio.to_thread(record_event, tid, KIND, {"name": c["name"], "ok": c["ok"], "why": c["why"],
                                                              "since": since, **({"warnings": c["warnings"]} if c.get("warnings") else {})})
            if flipped:
                changed.append(c)
    if changed:
        down = [c for c in changed if not c["ok"]]
        back = [c for c in changed if c["ok"]]
        parts = []
        if down:
            parts.append("FAILING: " + "; ".join(f"{c['tenant_id']}: {LABELS[c['name']]} — {c['why']}" for c in down))
        if back:
            parts.append("fixed: " + "; ".join(f"{c['tenant_id']}: {LABELS[c['name']]}" for c in back))
        await _alert("Vula books health — " + ". ".join(parts) + ".")
    return changed


def status() -> Dict[str, Any]:
    """For Master › Health: the latest result per business and check from the last 2 days."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        rows = (_client().table("vula_health_events").select("tenant_id,detail,created_at").eq("kind", KIND)
                .gte("created_at", since).order("created_at", desc=True).limit(500).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "checks": [], "failing": []}
    latest: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = r.get("detail") or {}
        key = f"{r['tenant_id']}:{d.get('name')}"
        if d.get("name") and key not in latest:
            latest[key] = {"tenant_id": r["tenant_id"], "name": d["name"], "label": LABELS.get(d["name"], d["name"]),
                           "ok": bool(d.get("ok")), "why": d.get("why") or "", "since": d.get("since"),
                           "checked_at": r["created_at"], "warnings": d.get("warnings")}
    checks_ = sorted(latest.values(), key=lambda c: (c["ok"], c["tenant_id"], c["name"]))
    return {"checks": checks_, "failing": [c for c in checks_ if not c["ok"]],
            "last_run": rows[0]["created_at"] if rows else None}


async def run_forever() -> None:
    await asyncio.sleep(600)   # settle on boot
    last_checks = 0.0
    while True:
        started = time.monotonic()
        try:
            due = started - last_checks >= CHECK_EVERY_S
            await tick(run_checks=due)
            if due:
                last_checks = started
        except Exception as exc:  # noqa: BLE001
            log.warning("books health tick failed: %s", exc)
        await asyncio.sleep(max(60.0, RETRY_EVERY_S - (time.monotonic() - started)))
