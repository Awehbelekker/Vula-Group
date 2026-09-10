"""vula/ingestion/payment_notice.py — deterministic parser for bank payment-notification PDFs.

Accounts and money: an LLM reading figures off OCR text is exactly where a wrong amount slips
into the books. But these documents are machine-generated with a rigid layout, so they don't
need an LLM at all — a positional parse of the (fitz-extracted, exact) text gives every field
verbatim, with confidence 1.0, in microseconds.

FNB "NOTIFICATION OF PAYMENT" is the common one for DIGG. The structure is:

    NOTIFICATION OF PAYMENT
    ...
    Date Actioned
    : 2026/07/20
    Trace ID
    : QFBQ1YMQ
    Payment From
    *AWEH BE LEKKER (PTY) LTD
    Cur/Amount
    ZAR44000.00
    Name
    : Edison Maunganidze
    Bank
    : FIRST NATIONAL BANK
    Reference
    : HPC GEYSER

Other SA banks (ABSA, Standard Bank, Nedbank, Capitec) issue similarly rigid notices — add a
matcher per bank as their samples come in. Returns None for anything unrecognised so the LLM
path in _analyze_document takes over unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


def _cents(raw: str) -> Optional[int]:
    m = re.search(r"(\d[\d ,]*)(?:\.(\d{1,2}))?", str(raw or ""))
    if not m:
        return None
    whole = re.sub(r"[ ,]", "", m.group(1))
    frac = (m.group(2) or "0").ljust(2, "0")[:2]
    try:
        return int(whole) * 100 + int(frac)
    except ValueError:
        return None


def _iso_date(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    for pat, order in ((r"(\d{4})[/-](\d{2})[/-](\d{2})", "ymd"),
                       (r"(\d{2})[/-](\d{2})[/-](\d{4})", "dmy")):
        m = re.match(pat, raw)
        if m:
            a, b, c = m.groups()
            return f"{a}-{b}-{c}" if order == "ymd" else f"{c}-{b}-{a}"
    return None


def _labelled(lines: list[str], label: str) -> Optional[str]:
    """Value that follows a line equal to `label` — the next non-empty line, with a leading
    ':' and surrounding '*'/whitespace stripped. None if the label isn't present."""
    lab = label.lower()
    for i, ln in enumerate(lines):
        if ln.strip().lower() == lab:
            for nxt in lines[i + 1:]:
                v = nxt.strip().lstrip(":").strip().strip("*").strip()
                if v:
                    return v
            return None
    return None


def _parse_fnb(text: str) -> Optional[Dict[str, Any]]:
    if "notification of payment" not in text.lower():
        return None
    lines = text.splitlines()
    amount_raw = _labelled(lines, "Cur/Amount") or ""
    fields = {
        "payer": _labelled(lines, "Payment From"),
        "payee_name": _labelled(lines, "Name"),
        "payee_bank": _labelled(lines, "Bank"),
        "payee_branch_code": _labelled(lines, "Branch Code"),
        "payee_account_number": _labelled(lines, "Recipient/Account no"),
        "amount_cents": _cents(re.sub(r"[A-Za-z]", "", amount_raw)),
        "reference": _labelled(lines, "Reference"),
        "trace_id": _labelled(lines, "Trace ID"),
        "date": _iso_date(_labelled(lines, "Date Actioned") or ""),
    }
    # A payment notice without an amount or a payee isn't one we can trust — hand back to the LLM.
    if not fields["amount_cents"] or not fields["payee_name"]:
        return None
    currency = "ZAR"
    m = re.search(r"\b([A-Z]{3})\s*[\d ,.]+", amount_raw)
    if m:
        currency = m.group(1)
    rand = fields["amount_cents"] / 100
    summary = (f"Payment notification from First National Bank: {currency} {rand:,.2f} from "
               f"{fields['payer'] or 'the account holder'} to {fields['payee_name']}"
               + (f", reference '{fields['reference']}'" if fields["reference"] else "") + ".")
    return {"category": "Proof of Payment", "summary": summary, "fields": fields,
            "extraction": "deterministic", "issuer": "FNB"}


_MATCHERS = (_parse_fnb,)


def parse(text: str) -> Optional[Dict[str, Any]]:
    """Deterministically parse a bank payment notification from its extracted text.
    Returns the same {category, summary, fields} shape _analyze_document produces (plus
    extraction="deterministic"), or None if the text isn't a recognised payment notice."""
    if not text or len(text) < 40:
        return None
    for matcher in _MATCHERS:
        try:
            got = matcher(text)
            if got:
                log.info("payment notice parsed deterministically (%s): %s cents",
                         got.get("issuer"), got["fields"].get("amount_cents"))
                return got
        except Exception as exc:  # noqa: BLE001 — never let this break the LLM fallback
            log.debug("payment-notice matcher %s failed: %s", getattr(matcher, "__name__", "?"), exc)
    return None
