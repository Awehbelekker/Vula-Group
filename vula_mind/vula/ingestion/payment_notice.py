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

Other SA banks (ABSA, Standard Bank, Nedbank, Capitec) issue similarly rigid notices, but
without a real sample of each one's actual PDF, a "positional" parser for them would just be a
guess dressed up as certainty. So every matcher here carries a `verified` flag: True only for
_parse_fnb, which was checked field-by-field against 7 real FNB PDFs. `_parse_generic_sa_eft`
below is a best-effort, unverified fallback for the other banks — looser keyword-proximity
matching instead of FNB's strict line-by-line positions, `verified=False`, and
_analyze_document treats its output as a HINT it hands to the LLM (which still has to re-derive
every figure from the actual text and pass the same grounding check), never as a fast path that
skips the LLM the way a verified match does. Replace it with a real _parse_<bank>() the moment
a genuine sample confirms that bank's layout.

Returns None for anything unrecognised so the LLM path in _analyze_document takes over
unchanged.
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
            "extraction": "deterministic", "issuer": "FNB", "verified": True}


def _labelled_flexible(text: str, *labels: str) -> Optional[str]:
    """Looser cousin of _labelled: a label doesn't have to sit alone on its own line — matches
    `label` followed (same line, optionally after a ':') by the rest of that line. Used only by
    the UNVERIFIED matcher below, where the real line-by-line layout is unknown and a strict
    positional match like FNB's would just silently match nothing."""
    for label in labels:
        m = re.search(rf"{re.escape(label)}\s*:?\s*([^\n]+)", text, re.IGNORECASE)
        if m:
            v = m.group(1).strip().strip("*").strip()
            if v and v.lower() not in (lab.lower() for lab in labels):
                return v
    return None


def _amount_near(text: str, *labels: str) -> Optional[str]:
    """The R/ZAR amount within ~80 chars after one of `labels`, else the one standalone Rand
    figure on the page if there's exactly one — a payment notice usually has just the one
    amount that matters, wherever on the page it sits."""
    money = r"(R\s?[\d][\d ,]*\.\d{2}|ZAR\s?[\d][\d ,]*\.\d{2})"
    for label in labels:
        m = re.search(rf"{re.escape(label)}.{{0,80}}?{money}", text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    amounts = re.findall(money, text, re.IGNORECASE)
    return amounts[0] if len(amounts) == 1 else None


def _parse_generic_sa_eft(text: str) -> Optional[Dict[str, Any]]:
    """UNVERIFIED heuristic matcher for a non-FNB South African bank's payment/EFT
    confirmation (ABSA, Standard Bank, Nedbank, Capitec, or an unidentified one). Built from
    general SA-banking terminology, not a confirmed real sample the way FNB's parser is —
    verified=False always, and _analyze_document treats the result as an LLM hint, never a
    fast path. See the module docstring."""
    low = text.lower()
    if not any(sig in low for sig in (
        "proof of payment", "eft confirmation", "payment confirmation",
        "notification of payment", "eft notification",
    )):
        return None
    issuer = "Unknown bank"
    for name in ("absa", "standard bank", "nedbank", "capitec", "fnb", "first national bank"):
        if name in low:
            issuer = name.upper() if len(name) <= 5 else name.title()
            break

    amount_raw = _amount_near(text, "amount", "total") or ""
    fields = {
        "payer": None,
        "payee_name": _labelled_flexible(text, "beneficiary name", "beneficiary", "recipient", "payee"),
        "payee_bank": None,
        "payee_branch_code": None,
        "payee_account_number": None,
        "amount_cents": _cents(re.sub(r"[A-Za-z]", "", amount_raw)) if amount_raw else None,
        "reference": _labelled_flexible(text, "beneficiary reference", "your reference", "reference"),
        "trace_id": None,
        "date": _iso_date(_labelled_flexible(text, "payment date", "date actioned",
                                             "transaction date", "date") or ""),
    }
    if not fields["amount_cents"]:
        return None
    rand = fields["amount_cents"] / 100
    summary = (f"Payment confirmation ({issuer}, UNVERIFIED layout — confirm against the "
               f"document): ZAR {rand:,.2f}"
               + (f" to {fields['payee_name']}" if fields["payee_name"] else "")
               + (f", reference '{fields['reference']}'" if fields["reference"] else "") + ".")
    return {"category": "Proof of Payment", "summary": summary, "fields": fields,
            "extraction": "heuristic", "issuer": issuer, "verified": False}


_MATCHERS = (_parse_fnb, _parse_generic_sa_eft)


def parse(text: str) -> Optional[Dict[str, Any]]:
    """Parse a bank payment notification from its extracted text. Returns the same
    {category, summary, fields} shape _analyze_document produces, plus `verified` (True only
    for a matcher checked against a real sample of that bank's PDF — the caller must NOT use
    an unverified match as a fast path, only as a hint for the LLM to re-derive and verify), or
    None if the text isn't a recognised payment notice at all."""
    if not text or len(text) < 40:
        return None
    for matcher in _MATCHERS:
        try:
            got = matcher(text)
            if got:
                log.info("payment notice matched (%s, verified=%s): %s cents",
                         got.get("issuer"), got.get("verified"), got["fields"].get("amount_cents"))
                return got
        except Exception as exc:  # noqa: BLE001 — never let this break the LLM fallback
            log.debug("payment-notice matcher %s failed: %s", getattr(matcher, "__name__", "?"), exc)
    return None
