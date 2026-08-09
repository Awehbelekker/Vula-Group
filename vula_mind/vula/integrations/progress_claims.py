"""
vula/integrations/progress_claims.py — structured progress claims / interim payment
certificates for construction projects (JBCC-style), linked into a project's unified
financials (vula/integrations/finances.py::project_financials).

Distinct from vula/api/draft.py's payment_certificate template, which only drafts prose —
this is the persisted, calculated record: cumulative value of work done, retention held,
previous-vs-this payment. A claim can be converted into a real, sendable invoice once
certified, closing the loop from certificate to something the client can actually pay.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def list_claims(tenant_id: str, project: str) -> list:
    try:
        rows = (_client().table("vula_project_claims").select("*")
                .eq("tenant_id", tenant_id).eq("project", project)
                .order("claim_number").execute().data or [])
    except Exception as exc:
        logger.debug("list_claims skipped (run migration 125?): %s", exc)
        rows = []
    return rows


def create_claim(tenant_id: str, project: str, cumulative_value_cents: int,
                 retention_pct: float = 5.0, claim_date: Optional[str] = None,
                 notes: Optional[str] = None) -> dict:
    """Create the next sequential progress claim for a project. cumulative_value_cents is
    the QS-assessed value of ALL work completed to date (not just this period) — standard
    JBCC interim-certificate convention. Retention and this-payment are always computed
    here, never trusted from the caller."""
    if cumulative_value_cents <= 0:
        raise ValueError("Cumulative value of work done must be positive.")
    existing = list_claims(tenant_id, project)
    prev = existing[-1] if existing else None
    prev_certified = int(prev["certified_to_date_cents"]) if prev else 0
    if prev and cumulative_value_cents < int(prev["cumulative_value_cents"]):
        raise ValueError(
            f"Cumulative value (R{cumulative_value_cents/100:,.2f}) is less than the previous "
            f"claim's (R{int(prev['cumulative_value_cents'])/100:,.2f}) — a later claim can't "
            f"show less work done than an earlier one.")
    retention_cents = round(cumulative_value_cents * (retention_pct / 100.0))
    certified_to_date = cumulative_value_cents - retention_cents
    this_payment = certified_to_date - prev_certified
    row = {
        "tenant_id": tenant_id, "project": project,
        "claim_number": (prev["claim_number"] + 1) if prev else 1,
        "claim_date": claim_date or date.today().isoformat(),
        "cumulative_value_cents": int(cumulative_value_cents),
        "retention_pct": retention_pct,
        "retention_cents": retention_cents,
        "certified_to_date_cents": certified_to_date,
        "previous_certified_cents": prev_certified,
        "this_payment_cents": this_payment,
        "status": "draft", "notes": notes,
    }
    res = _client().table("vula_project_claims").insert(row).execute()
    return res.data[0] if res.data else row


def certify_claim(tenant_id: str, claim_id: str) -> dict:
    from vula.commerce import service
    res = (_client().table("vula_project_claims")
           .update({"status": "certified", "certified_at": service._now()})
           .eq("id", claim_id).eq("tenant_id", tenant_id).execute())
    if not res.data:
        raise ValueError("Claim not found")
    return res.data[0]


async def convert_claim_to_invoice(tenant_id: str, claim_id: str, customer: dict,
                                   vat_rate: float = 15.0) -> dict:
    """Generate a real, sendable invoice for exactly this claim's net payment amount, and
    link it back. `customer` needs at least a name (+ phone/email/address as available)."""
    rows = (_client().table("vula_project_claims").select("*")
            .eq("tenant_id", tenant_id).eq("id", claim_id).limit(1).execute().data or [])
    if not rows:
        raise ValueError("Claim not found")
    claim = rows[0]
    if claim.get("linked_invoice_id"):
        raise ValueError("This claim already has an invoice.")
    if int(claim.get("this_payment_cents") or 0) <= 0:
        raise ValueError("Nothing to invoice for this claim.")
    if not (customer or {}).get("name"):
        raise ValueError("Customer name is required.")
    from vula.commerce import service as commerce_service
    invoice = await commerce_service.create_invoice(tenant_id, {
        "doc_type": "invoice",
        "customer_name": customer["name"], "customer_phone": customer.get("phone"),
        "customer_email": customer.get("email"), "customer_address": customer.get("address"),
        "line_items": [{
            "description": (f"Progress claim #{claim['claim_number']} — {claim['project']} "
                            f"(work to date R{int(claim['cumulative_value_cents'])/100:,.2f}, "
                            f"retention {claim['retention_pct']}%)"),
            "quantity": 1, "unit_price_cents": int(claim["this_payment_cents"]),
        }],
        "vat_rate": vat_rate, "project": claim["project"], "status": "draft",
        "issue_date": date.today().isoformat(),
        "notes": f"Progress claim #{claim['claim_number']} for {claim['project']}.",
    })
    _client().table("vula_project_claims").update(
        {"linked_invoice_id": invoice["id"], "status": "invoiced"}
    ).eq("id", claim_id).eq("tenant_id", tenant_id).execute()
    return invoice
