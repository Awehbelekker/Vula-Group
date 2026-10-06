"""Tax-invoice rules (pure): VAT split of a VAT-inclusive bill, buyer-detail validation and parsing.

A tax invoice covers the BILL only. Tips are a gratuity, not payment for a supply; their VAT treatment
is an open accountant question, so the invoice excludes them and says so.
"""
from __future__ import annotations

import re
from typing import Optional

VAT_RATE_PCT = 15
_VAT_RE = re.compile(r"(?<!\d)(4\d{9})(?!\d)")
_VAT_ANY = re.compile(r"(?<!\d)(\d[\d ]{8,12}\d)(?!\d)")
_NAME_MAX = 120
_ADDR_MAX = 200


def vat_split(bill_cents: int) -> tuple[int, int]:
    """(excl_vat, vat) of a VAT-inclusive amount at 15%; vat rounds half up, excl + vat == bill."""
    vat = (int(bill_cents) * VAT_RATE_PCT + (100 + VAT_RATE_PCT) // 2) // (100 + VAT_RATE_PCT)
    return int(bill_cents) - vat, vat


def normalise_vat(raw: str) -> Optional[str]:
    """A SARS VAT number is 10 digits starting with 4 (spaces tolerated). None when not."""
    digits = re.sub(r"[\s-]", "", raw or "")
    return digits if re.fullmatch(r"4\d{9}", digits) else None


def clean_text(raw: str, limit: int) -> str:
    s = re.sub(r"[\x00-\x1f\x7f<>{}]", " ", raw or "")
    return re.sub(r"\s+", " ", s).strip(" ,;:-")[:limit]


def parse_details(text: str) -> tuple[Optional[str], Optional[str]]:
    """Pull (company_name, vat_number) out of one free-text message such as
    'Acme Trading (Pty) Ltd, VAT 4123456789'. Either part may come back None."""
    t = text or ""
    vat = None
    m = _VAT_RE.search(re.sub(r"(?<=\d)[ -](?=\d)", "", t))
    if m:
        vat = m.group(1)
        t = re.sub(r"(?<=\d)[ -](?=\d)", "", t).replace(vat, " ")
    t = re.sub(r"(?i)\b(vat\s*(no\.?|number|nr|#)?|company|name|tax\s*invoice)\b\s*[:#-]?", " ", t)
    name = clean_text(t, _NAME_MAX)
    return (name if len(name) >= 2 else None), vat


def format_number(n: int) -> str:
    return f"TI-{int(n):06d}"
