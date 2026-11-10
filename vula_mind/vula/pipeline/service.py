"""
vula/pipeline/service.py — a deals pipeline that fits every kind of business (migration 255).

One table and one set of rules; the stages a business works through come from its
business_type (STAGE_SETS), so an estate agency sees viewing → offer → negotiation → under
contract and a builder sees lead → quoted → negotiating, on the same board and the same
WhatsApp tools. Switched on per business by the `pipeline` module.

Rules kept here so the dashboard API and the WhatsApp tools share them:
  - every query is scoped by tenant_id (service-role-only table);
  - values are integer cents, parsed server-side (vula.listings.service.parse_rands_to_cents);
  - a stage must be one of the business's own stages; moving to won/lost stamps closed_at,
    moving back out clears it;
  - every write is read back before it's reported.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "vula_deals"

# Each stage: key, label, and the rough chance a deal at that stage closes (weighted value).
# "won"/"lost" end every set — the board shows them as the closed columns.
STAGE_SETS: Dict[str, List[Dict[str, Any]]] = {
    "real_estate": [
        {"key": "viewing", "label": "Viewing", "chance": 15},
        {"key": "offer", "label": "Offer", "chance": 40},
        {"key": "negotiation", "label": "Negotiation", "chance": 65},
        {"key": "under_contract", "label": "Under contract", "chance": 90},
        {"key": "won", "label": "Won", "chance": 100},
        {"key": "lost", "label": "Lost", "chance": 0},
    ],
    "default": [
        {"key": "lead", "label": "Lead", "chance": 10},
        {"key": "quoted", "label": "Quoted", "chance": 40},
        {"key": "negotiating", "label": "Negotiating", "chance": 70},
        {"key": "won", "label": "Won", "chance": 100},
        {"key": "lost", "label": "Lost", "chance": 0},
    ],
}
CLOSED = ("won", "lost")
_FIELDS = ("title", "stage", "value_cents", "contact_name", "contact_phone", "contact_email",
           "ref_type", "ref_id", "enquiry_id", "owner", "expected_close", "notes", "position")
REF_TYPES = ("property",)


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stages_for(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        from vula.api.tenants import get_config
        bt = ((get_config(tenant_id) or {}).get("business_type") or "").lower()
    except Exception:
        bt = ""
    return STAGE_SETS.get(bt, STAGE_SETS["default"])


def stage_keys(tenant_id: str) -> List[str]:
    return [s["key"] for s in stages_for(tenant_id)]


def stage_label(tenant_id: str, key: str) -> str:
    for s in stages_for(tenant_id):
        if s["key"] == key:
            return s["label"]
    return key


def clean_deal(tenant_id: str, body: Dict[str, Any], *, creating: bool) -> Dict[str, Any]:
    """Validate + normalise. Raises ValueError with a message a person can act on."""
    from vula.listings.service import parse_rands_to_cents
    row: Dict[str, Any] = {k: body[k] for k in _FIELDS if k in body}
    if "title" in row or creating:
        row["title"] = (row.get("title") or "").strip()[:160]
        if not row["title"]:
            raise ValueError("A deal needs a name, e.g. 'Thandi — 4 bed in Durbanville'.")
    keys = stage_keys(tenant_id)
    if creating and not row.get("stage"):
        row["stage"] = keys[0]
    if "stage" in row and row["stage"] not in keys:
        raise ValueError(f"Stage must be one of: {', '.join(keys)}.")
    if "value" in body and "value_cents" not in body:
        if body["value"] in (None, ""):
            row["value_cents"] = None
        else:
            cents = parse_rands_to_cents(body["value"])
            if cents is None:
                raise ValueError(f"Couldn't read the amount '{body['value']}' — try e.g. R1 850 000.")
            row["value_cents"] = cents
    elif "value_cents" in row and row["value_cents"] not in (None, ""):
        try:
            row["value_cents"] = int(row["value_cents"])
        except (TypeError, ValueError):
            raise ValueError("value_cents must be a whole number of cents.")
        if row["value_cents"] < 0:
            raise ValueError("A deal value can't be negative.")
    elif "value_cents" in row:
        row["value_cents"] = None
    if "contact_phone" in row and row["contact_phone"]:
        from vula.team_index import digits_za
        row["contact_phone"] = digits_za(row["contact_phone"]) or None
    if "ref_type" in row and row["ref_type"] not in (None, "", *REF_TYPES):
        raise ValueError("Unknown ref_type.")
    for k in ("contact_name", "contact_email", "owner", "notes", "ref_type"):
        if k in row and row[k] is not None:
            row[k] = str(row[k]).strip() or None
    for k in ("ref_id", "enquiry_id", "expected_close"):
        if k in row and not row[k]:
            row[k] = None
    if row.get("expected_close"):
        try:
            row["expected_close"] = datetime.strptime(str(row["expected_close"])[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise ValueError("Expected close must be a date like 2026-12-15.")
    if "position" in row:
        try:
            row["position"] = int(row["position"] or 0)
        except (TypeError, ValueError):
            row["position"] = 0
    return row


async def list_deals(tenant_id: str, *, stage: Optional[str] = None, open_only: bool = False,
                     ref_type: Optional[str] = None, ref_id: Optional[str] = None,
                     limit: int = 500) -> List[dict]:
    q = _client().table(TABLE).select("*").eq("tenant_id", tenant_id)
    if stage:
        q = q.eq("stage", stage)
    if ref_type and ref_id:
        q = q.eq("ref_type", ref_type).eq("ref_id", ref_id)
    try:
        rows = q.order("position").order("updated_at", desc=True).limit(min(max(limit, 1), 1000)).execute().data or []
    except Exception as exc:
        log.debug("list_deals skipped (run migration 255?): %s", exc)
        return []
    if open_only:
        rows = [r for r in rows if r.get("stage") not in CLOSED]
    return rows


async def get_deal(tenant_id: str, deal_id: str) -> Optional[dict]:
    rows = (_client().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", deal_id)
            .limit(1).execute().data or [])
    return rows[0] if rows else None


async def _check_ref(tenant_id: str, row: Dict[str, Any]) -> None:
    """A deal may only point at this business's own listing / enquiry."""
    if row.get("ref_type") == "property" and row.get("ref_id"):
        from vula.listings import service as ls
        if not await ls.get_property(tenant_id, property_id=row["ref_id"]):
            raise ValueError("That listing isn't one of this business's listings.")
    if row.get("enquiry_id"):
        got = (_client().table("vula_property_enquiries").select("id").eq("tenant_id", tenant_id)
               .eq("id", row["enquiry_id"]).limit(1).execute().data or [])
        if not got:
            raise ValueError("That enquiry isn't one of this business's enquiries.")


async def save_deal(tenant_id: str, body: Dict[str, Any]) -> dict:
    """Create (no id) or update a deal; returns the row read back from the database."""
    deal_id = body.get("id")
    row = clean_deal(tenant_id, body, creating=not deal_id)
    await _check_ref(tenant_id, row)
    current = await get_deal(tenant_id, deal_id) if deal_id else None
    if deal_id and not current:
        raise LookupError("deal not found")
    if "stage" in row:
        was_closed = (current or {}).get("stage") in CLOSED
        if row["stage"] in CLOSED and not was_closed:
            row["closed_at"] = _now()
        elif row["stage"] not in CLOSED and was_closed:
            row["closed_at"] = None
    row["updated_at"] = _now()
    if deal_id:
        res = _client().table(TABLE).update(row).eq("tenant_id", tenant_id).eq("id", deal_id).execute()
        if not res.data:
            raise LookupError("deal not found")
    else:
        res = _client().table(TABLE).insert({**row, "tenant_id": tenant_id}).execute()
        if not res.data:
            raise RuntimeError("deal was not saved")
        deal_id = res.data[0]["id"]
    saved = await get_deal(tenant_id, deal_id)
    if not saved:
        raise RuntimeError("deal was not saved")
    return saved


async def delete_deal(tenant_id: str, deal_id: str) -> None:
    _client().table(TABLE).delete().eq("tenant_id", tenant_id).eq("id", deal_id).execute()


def summary(tenant_id: str, deals: List[dict]) -> Dict[str, Any]:
    """Board header numbers, computed here from integer cents (never in the browser)."""
    stages = stages_for(tenant_id)
    chance = {s["key"]: s["chance"] for s in stages}
    open_deals = [d for d in deals if d.get("stage") not in CLOSED]
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    won_month = [d for d in deals if d.get("stage") == "won" and (d.get("closed_at") or "")[:7] == month]
    closed = [d for d in deals if d.get("stage") in CLOSED]
    by_stage = {}
    for s in stages:
        rows = [d for d in deals if d.get("stage") == s["key"]]
        by_stage[s["key"]] = {"count": len(rows), "value_cents": sum(int(d.get("value_cents") or 0) for d in rows)}
    return {
        "open_count": len(open_deals),
        "open_value_cents": sum(int(d.get("value_cents") or 0) for d in open_deals),
        "weighted_value_cents": sum(int(d.get("value_cents") or 0) * chance.get(d.get("stage"), 0) // 100
                                    for d in open_deals),
        "won_this_month_count": len(won_month),
        "won_this_month_cents": sum(int(d.get("value_cents") or 0) for d in won_month),
        "win_rate": (round(100 * sum(1 for d in closed if d["stage"] == "won") / len(closed))
                     if closed else None),
        "by_stage": by_stage,
    }


def format_rands(cents: Optional[int]) -> str:
    if cents is None:
        return "no value yet"
    return "R" + f"{int(cents) // 100:,}".replace(",", " ")
