"""
core/skills/pipeline_tools.py — WhatsApp tools for the deals pipeline (the `pipeline` module,
vula/pipeline/service.py, migration 258). Works for every business type: the stages come from
the business's own stage set (estate agency: viewing → offer → …; others: lead → quoted → …).

Agent/staff side only: see the pipeline, and add / move / update a deal ("record Priya's offer of
R1.24m on Hollis", "move Thandi to under contract"). Adding and changing a deal shows a preview
and runs only after the person confirms; the saved row is read back before Vula says it's done.
Registered as an add-on in commerce_admin._ADDONS, so businesses without the module never see it.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from vula.pipeline import service as ps

log = logging.getLogger(__name__)

MODULE = "pipeline"

ADMIN_TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "pipeline_summary",
        "description": ("The deals pipeline: open deals per stage with their values, what closed "
                        "this month, and the deals in one stage if asked."),
        "parameters": {"type": "object", "properties": {
            "stage": {"type": "string", "description": "Only this stage's deals (its name or key)."},
        }},
    }},
    {"type": "function", "function": {
        "name": "save_deal",
        "description": (
            "Add a deal to the pipeline, or update one: move it to another stage, record an offer "
            "or quote amount, change who's on it or the expected close date. To update, name the "
            "deal in 'deal'. Shows a preview; saved only once the person confirms."),
        "parameters": {"type": "object", "properties": {
            "deal": {"type": "string", "description": "Which existing deal (words from its name or the contact's name). Leave out to add a new one."},
            "title": {"type": "string", "description": "Name for a new deal, e.g. 'Priya — Hollis Modern House'."},
            "stage": {"type": "string", "description": "Stage name or key, e.g. 'offer', 'under contract', 'won', 'lost'."},
            "value": {"type": "string", "description": "Amount as said: 'R1.24m', '185 000'."},
            "contact_name": {"type": "string"}, "contact_phone": {"type": "string"},
            "owner": {"type": "string", "description": "Who's working the deal."},
            "listing": {"type": "string", "description": "Property listing it's about (real estate)."},
            "expected_close": {"type": "string", "description": "YYYY-MM-DD"},
            "notes": {"type": "string"},
            "confirm": {"type": "boolean", "description": "Only true after the person confirmed the preview."},
        }},
    }},
]
ADMIN_TOOL_NAMES = {t["function"]["name"] for t in ADMIN_TOOL_SPECS}
ADMIN_PREVIEW_FIRST = frozenset({"save_deal"})


def _stage_key(tenant_id: str, said: Optional[str]) -> Optional[str]:
    """'under contract' / 'Under Contract' / 'under_contract' → 'under_contract'; None if unknown."""
    if not said:
        return None
    norm = re.sub(r"[^a-z]", "", said.lower())
    for s in ps.stages_for(tenant_id):
        if norm in (re.sub(r"[^a-z]", "", s["key"]), re.sub(r"[^a-z]", "", s["label"].lower())):
            return s["key"]
    return None


def _brief(tenant_id: str, d: Dict[str, Any]) -> Dict[str, Any]:
    out = {"deal": d.get("title"), "stage": ps.stage_label(tenant_id, d.get("stage")),
           "value": ps.format_rands(d.get("value_cents")) if d.get("value_cents") is not None else None,
           "contact": d.get("contact_name"), "owner": d.get("owner"),
           "expected_close": d.get("expected_close")}
    return {k: v for k, v in out.items() if v}


def _match(deals: List[dict], ref: str) -> List[dict]:
    from core.skills.listing_tools import _tokens
    want = _tokens(ref)
    if not want:
        return []
    scored = []
    for d in deals:
        have = _tokens(" ".join(str(d.get(k) or "") for k in ("title", "contact_name")))
        hit = len(want & have)
        if hit:
            scored.append((hit, d))
    if not scored:
        return []
    best = max(h for h, _ in scored)
    return [d for h, d in scored if h == best]


async def run_admin_tool(name: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    tid = ctx["tenant_id"]
    try:
        if name == "pipeline_summary":
            deals = await ps.list_deals(tid)
            s = ps.summary(tid, deals)
            out: Dict[str, Any] = {
                "open_deals": s["open_count"], "open_value": ps.format_rands(s["open_value_cents"]),
                "weighted_value": ps.format_rands(s["weighted_value_cents"]),
                "won_this_month": f"{s['won_this_month_count']} ({ps.format_rands(s['won_this_month_cents'])})",
                "stages": [{"stage": st["label"], "deals": s["by_stage"][st["key"]]["count"],
                            "value": ps.format_rands(s["by_stage"][st["key"]]["value_cents"])}
                           for st in ps.stages_for(tid) if st["key"] not in ps.CLOSED],
            }
            if args.get("stage"):
                key = _stage_key(tid, args["stage"])
                if not key:
                    return {"error": f"No stage called '{args['stage']}'. Stages: "
                                     + ", ".join(st["label"] for st in ps.stages_for(tid))}
                out["deals_in_stage"] = [_brief(tid, d) for d in deals if d.get("stage") == key][:20]
            return out

        if name == "save_deal":
            confirm = bool(args.get("confirm"))
            body: Dict[str, Any] = {}
            for k in ("title", "contact_name", "contact_phone", "owner", "expected_close", "notes"):
                if args.get(k) not in (None, ""):
                    body[k] = args[k]
            if args.get("value") not in (None, ""):
                body["value"] = args["value"]
            if args.get("stage"):
                key = _stage_key(tid, args["stage"])
                if not key:
                    return {"error": f"No stage called '{args['stage']}'. Stages: "
                                     + ", ".join(st["label"] for st in ps.stages_for(tid))}
                body["stage"] = key
            if args.get("listing"):
                from core.skills import listing_tools as lt
                from vula.listings import service as ls
                found = lt.match_listings(await ls.list_properties(tid, limit=500), args["listing"])
                if len(found) != 1:
                    return {"error": "Which listing? " + (", ".join(p["title"] for p in found[:6])
                                                          if found else "No listing matches that.")}
                body.update({"ref_type": "property", "ref_id": found[0]["id"]})
            current = None
            if args.get("deal"):
                hits = _match(await ps.list_deals(tid), args["deal"])
                if not hits:
                    return {"error": "No deal matches that.", "hint": "Call pipeline_summary to see the deals."}
                if len(hits) > 1:
                    return {"which_one": [_brief(tid, d) for d in hits[:6]], "hint": "Ask which deal they mean."}
                current = hits[0]
                body["id"] = current["id"]
                if len(body) == 1:
                    return {"error": "Nothing to change — say the new stage, amount or detail."}
            elif not body.get("title"):
                return {"error": "What should the deal be called? e.g. 'Priya — Hollis Modern House'."}
            clean = ps.clean_deal(tid, body, creating=current is None)       # validates before preview
            if not confirm:
                after = _brief(tid, {**(current or {}), **clean})
                if current:
                    before = _brief(tid, current)
                    changes = {k: f"{before.get(k, '—')} → {v}" for k, v in after.items()
                               if before.get(k) != v and k != "deal"}
                    return {"preview": True, "deal": current.get("title"), **changes}
                return {"preview": True, "new_deal": after.pop("deal", None), **after}
            saved = await ps.save_deal(tid, body)
            ok = all(saved.get(k) == clean[k] for k in ("stage", "value_cents") if k in clean)
            try:
                from core.skills.commerce_admin import _readback_gate
                _readback_gate(tid, "save_deal", ok, {"id": saved.get("id"), "stage": clean.get("stage")},
                               {"id": saved.get("id"), "stage": saved.get("stage")})
            except Exception:
                pass
            if not ok:
                return {"error": "The deal didn't save as intended.",
                        "reply_verbatim": "⚠️ The deal didn't save as intended — please check the pipeline."}
            b = _brief(tid, saved)
            return {"verified": True, "reply_verbatim":
                    f"✅ *{saved['title']}* — {b.get('stage')}" + (f", {b['value']}" if b.get("value") else "") + "."}
    except ValueError as exc:
        return {"error": str(exc)}
    except LookupError:
        return {"error": "Not found — it may have been removed."}
    except Exception as exc:
        log.warning("pipeline tool %s failed (%s): %s", name, tid, exc)
        return {"error": "Couldn't do that just now."}
    return {"error": f"unknown tool {name}"}
