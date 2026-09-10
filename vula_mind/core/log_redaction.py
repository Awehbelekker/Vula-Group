"""core/log_redaction.py — keep customer PII out of the (Railway-captured, retained) logs.

POPIA: the WhatsApp code paths log phone numbers liberally at INFO across ~40 call sites, and
Railway persists stdout. Rewriting every call site is fragile and misses future ones — a single
logging.Filter on the root logger masks the pattern centrally instead.

Phone numbers only. Message CONTENT must not be logged in the first place (a regex can't tell a
transcript from a fault string) — those few call sites log length/hash instead. A no-op when
DEBUG is on, so local development still sees real numbers.
"""
from __future__ import annotations

import logging
import re

# SA mobile shapes: +27XXXXXXXXX / 27XXXXXXXXX / 0XXXXXXXXX, tolerating spaces/dashes. Kept
# deliberately tight so it doesn't eat invoice totals or IDs — it wants 9+ digits after a
# 27/0 prefix, which an amount or a short id won't have.
_PHONE_RE = re.compile(r"(?<!\d)(\+?27|0)[\s-]?(\d[\s-]?){8,10}\d(?!\d)")


def _mask(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return digits[:4] + "…" + digits[-2:] if len(digits) > 6 else "…"


class PhoneRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if _PHONE_RE.search(msg):
                record.msg = _PHONE_RE.sub(_mask, msg)
                record.args = ()
        except Exception:
            pass
        return True


def install(debug: bool = False) -> None:
    """Attach the filter to the root logger's handlers. Idempotent; no-op under DEBUG."""
    if debug:
        return
    root = logging.getLogger()
    for h in root.handlers:
        if not any(isinstance(f, PhoneRedactingFilter) for f in h.filters):
            h.addFilter(PhoneRedactingFilter())
