"""
vula/commerce/party.py — canonical "who is the other party on this document" resolution.

Every document-extraction path (Smart Scanner, email/WhatsApp/dashboard deep-analysis,
bank statement parsing) names the counterparty differently: supplier, vendor, payee,
counterparty, beneficiary, sometimes client, sometimes payer. Downstream code has grown
several ad hoc `f.get("payee") or f.get("supplier") or ...` chains, each with its own
(sometimes inconsistent) priority order. This module centralises the pattern without
forcing every call site onto one order — `payee` and `supplier` mean different things
depending on the document, and `payer` is frequently the tenant's OWN name rather than
the counterparty, so some callers deliberately exclude it.

Never mutates the input or overwrites an already-present `supplier` key — this is a
read-time normalization, not a data migration.
"""
from __future__ import annotations

from typing import Optional

# Default resolution order for a generic caller with no stronger opinion. Individual call
# sites may override `order` to match their own document semantics (e.g. finances.py's
# payment-notification parsing checks payee before supplier).
_DEFAULT_ORDER = ("supplier", "vendor", "payee", "counterparty", "beneficiary", "client", "payer")


def resolve_party_name(
    fields: dict, *, order: tuple = _DEFAULT_ORDER, exclude: tuple = (),
) -> Optional[str]:
    """First non-empty party name across the known key aliases, in `order`.

    2026-08-08: "General Document" extractions (whatsapp.py's _analyze_document) have no
    enforced fields shape beyond Invoice/Quote/BOQ/Business Card — the model is free to choose
    its own structure. Confirmed live on a real bank payment notification: it nested the payee
    under `payee_details: {"name": ..., "bank": ..., ...}` instead of a flat `payee` string, so
    the flat lookup below returned nothing and the payee was silently unidentifiable — no
    learned filing rule, no reimbursement-balance match, nothing. `{key}_details.name` is
    checked as a fallback for the same reason so this doesn't keep happening for every payment
    notification/EFT confirmation shaped this way.
    """
    for key in order:
        if key in exclude:
            continue
        v = (fields or {}).get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        nested = (fields or {}).get(f"{key}_details")
        if isinstance(nested, dict):
            name = nested.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def normalize_party_fields(
    fields: dict, *, order: tuple = _DEFAULT_ORDER, exclude: tuple = (),
) -> dict:
    """Return a copy of `fields` with a canonical `supplier` key set (first non-empty match
    across the alias keys) whenever `fields` didn't already have one. Never overwrites an
    existing `supplier` value, never mutates the input, never touches any other key."""
    fields = fields or {}
    if isinstance(fields.get("supplier"), str) and fields["supplier"].strip():
        return fields
    name = resolve_party_name(fields, order=order, exclude=exclude)
    if not name:
        return fields
    return {**fields, "supplier": name}
