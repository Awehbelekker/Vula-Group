"""
vula/commerce/business_rules.py — standing instructions the owner has given Vula.

Real Gerflor message (2026-08-28), from the owner on WhatsApp:

    "...make sure when pricing that you price with the correct discounts. Per-Square price list
     all is NETT (No further discounts apply). DT is subject to 7% Trade discount, excluding the
     Mactile which is NETT. Secondly make sure you price the correct Zone for DT. Per-Square
     doesn't fall into zones. ... Please check with Michelle before pricing items on SPM and
     myself on Gerflor until you get the pricing structure."

A complete pricing policy, stated plainly. Vula answered "I was unable to find the correct
pricing structure for distributors" and kept none of it. Three days later the same class of
question got the same empty answer — the owner had already supplied it.

Deliberately NOT the knowledge base. A KB document surfaces only when a query happens to match
it; a standing rule has to apply to every relevant answer regardless of wording. Rules are few,
short and always-on, so they are injected into the prompt wholesale rather than retrieved.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MAX_RULES = 15          # a prompt-sized set; beyond this the owner should be pruning
MAX_RULE_CHARS = 600    # one rule, not a pasted document
_MAX_BLOCK_CHARS = 2500


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_rule(tenant_id: str, rule: str, topic: str = "", created_by: str = "",
             source: str = "whatsapp") -> Dict[str, Any]:
    """Store a standing instruction. Returns the stored row, or {"error": ...}."""
    text = (rule or "").strip()
    if not text:
        return {"error": "Nothing to remember — what's the rule?"}
    if len(text) > MAX_RULE_CHARS:
        return {"error": f"That's too long for a standing rule ({len(text)} chars). Keep it to "
                         f"the instruction itself — upload the full document instead."}
    try:
        existing = active_rules(tenant_id)
        # Don't store the same instruction twice — an owner repeating themselves is common.
        for r in existing:
            if (r.get("rule") or "").strip().lower() == text.lower():
                return {"status": "already_known", "id": r.get("id"), "rule": r.get("rule")}
        if len(existing) >= MAX_RULES:
            return {"error": f"You already have {len(existing)} standing rules — the most I can "
                             f"reliably apply. Archive one first."}
        row = {"tenant_id": tenant_id, "rule": text, "topic": (topic or "").strip() or None,
               "status": "active", "created_by": created_by or None, "source": source,
               "created_at": _now(), "updated_at": _now()}
        res = _client().table("vula_business_rules").insert(row).execute()
        stored = (res.data or [row])[0]
        log.info("business rule stored for %s: %.80s", tenant_id, text)
        return {"status": "saved", "id": stored.get("id"), "rule": text,
                "topic": stored.get("topic")}
    except Exception as exc:
        log.warning("business rule store failed for %s (run migration 152?): %s", tenant_id, exc)
        return {"error": "Couldn't save that rule just now."}


def active_rules(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        return (_client().table("vula_business_rules")
                .select("id,rule,topic,created_at,created_by")
                .eq("tenant_id", tenant_id).eq("status", "active")
                .order("created_at").limit(MAX_RULES).execute().data or [])
    except Exception as exc:
        log.debug("business rules lookup skipped (run migration 152?): %s", exc)
        return []


def archive_rule(tenant_id: str, rule_id: str) -> bool:
    try:
        _client().table("vula_business_rules").update(
            {"status": "archived", "updated_at": _now()}
        ).eq("id", rule_id).eq("tenant_id", tenant_id).execute()
        return True
    except Exception as exc:
        log.warning("business rule archive failed for %s: %s", rule_id, exc)
        return False


def find_rule(tenant_id: str, query: str) -> Optional[Dict[str, Any]]:
    """The active rule best matching a fragment — for 'forget the DT discount rule'."""
    q = (query or "").strip().lower()
    if not q:
        return None
    rules = active_rules(tenant_id)
    for r in rules:                                   # exact-ish first
        if q in (r.get("rule") or "").lower():
            return r
    for r in rules:                                   # then topic
        if q in (r.get("topic") or "").lower():
            return r
    return None


def rules_block(tenant_id: str) -> str:
    """The prompt fragment carrying this tenant's standing instructions.

    Empty string when there are none, so a tenant that has never stated a rule sees no change.
    Phrased as binding on the answer, because that is what the owner meant when they said it.
    """
    rules = active_rules(tenant_id)
    if not rules:
        return ""
    lines, total = [], 0
    for r in rules:
        text = (r.get("rule") or "").strip()
        if not text:
            continue
        topic = (r.get("topic") or "").strip()
        line = f"- {('[' + topic + '] ') if topic else ''}{text}"
        if total + len(line) > _MAX_BLOCK_CHARS:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return ""
    return (
        "\n\nSTANDING INSTRUCTIONS FROM THE OWNER — these were given to you directly and apply "
        "to every relevant answer, whether or not the question mentions them:\n"
        + "\n".join(lines)
        + "\nFollow these over anything you infer yourself. If one of them tells you to check "
        "with a person before answering (e.g. pricing sign-off), say that instead of guessing. "
        "If a rule conflicts with a document, say so plainly rather than silently picking one."
    )
