"""The dashboard's open chat, "Ask anything" (13 Nov 2026).

Ian: "Can't we have open chats with local AI and our cloud?" A staff member can brainstorm,
write and plan with Vula like a general chat, answered by the reasoning skill on Vula's own AI
(local first; the cloud only for the router's logged reasons; the business's daily spend cap is
a hard stop — core/llm_router.CAP_HARD_TASKS). What needs the business's own records — what a
client owes, an invoice to send, stock to change — still goes to the business assistant
(commerce_admin) with its tools, permissions, confirm-before-send and read-back.

On for a business with features.on(tenant, "open_chat"). A per-person limit per hour keeps one
person from spending the day's thinking.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Deque, Dict

# Asking Vula to DO something with a record.
_ACTION = re.compile(
    r"\b(create|make|send|issue|raise|record|log|add|update|change|cancel|refund|delete|remove|"
    r"book|schedule|remind|mark|approve|pay|chase|follow\s+up)\b[^.?!\n]{0,40}"
    r"\b(invoices?|quotes?|quotations?|orders?|payments?|stock|products?|prices?|customers?|clients?|"
    r"suppliers?|expenses?|bookings?|reminders?|broadcasts?|deliver(y|ies)|purchase\s+orders?)\b", re.I)
# Asking about the business's own figures and people.
_RECORDS = re.compile(
    r"\b(our|my|we|us|we've|we're)\b[^.?!\n]{0,30}\b(orders?|invoices?|quotes?|sales|stock|customers?|"
    r"clients?|suppliers?|expenses?|payments?|bookings?|outstanding|overdue|revenue|turnover|profit|"
    r"debtors?|creditors?|cash\s*flow|projects?|timesheets?|budgets?|products?|catalogue|"
    r"price\s*list|bank)\b|"
    r"\bwho\s+(still\s+)?owes\b|\b(what|how\s+much)\s+(do|does|did)\s+[\w' ]{1,30}\s+owe\b|"
    r"\bowes?\s+us\b", re.I)


def needs_records(text: str) -> bool:
    """True when the message asks about, or for an action on, this business's own records — the
    business assistant answers it. False for a general question, an idea, a draft or a plan."""
    from core.skills.base import looks_like_tenant_data_question
    t = text or ""
    return bool(_ACTION.search(t) or _RECORDS.search(t)
                or looks_like_tenant_data_question(t, require_possessive=True))


_SENT: Dict[str, Deque[float]] = {}
_LOCK = threading.Lock()
WINDOW_S = 3600.0


def allow(key: str, per_hour: int) -> bool:
    """Count one open-chat message for this person; False once they've sent `per_hour` in the
    last hour. In this worker's memory: a limit, not an account."""
    if per_hour <= 0:
        return True
    now = time.time()
    with _LOCK:
        q = _SENT.setdefault(key, deque())
        while q and now - q[0] > WINDOW_S:
            q.popleft()
        if len(q) >= per_hour:
            return False
        q.append(now)
        return True


def clear() -> None:
    """Forget the counts (tests)."""
    with _LOCK:
        _SENT.clear()
