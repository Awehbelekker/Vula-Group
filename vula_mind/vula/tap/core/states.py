"""Bill and payment-session state machines (spec §6). Pure: a transition is a table lookup.

Handlers must call `bill_next` / `session_next` and persist the result; they never assign a status
directly. An illegal (state, event) pair raises IllegalTransition — tested exhaustively.
"""
from __future__ import annotations

from itertools import product


class IllegalTransition(ValueError):
    def __init__(self, kind: str, state: str, event: str):
        super().__init__(f"{kind}: event '{event}' is not allowed in state '{state}'")
        self.kind, self.state, self.event = kind, state, event


BILL_STATES = ("open", "claimed", "paid", "cancelled", "expired", "abandoned",
               "needs_follow_up", "written_off", "paid_other")
BILL_EVENTS = ("claim", "release", "claim_idle", "pay", "cancel", "expire", "abandon",
               "reminders_exhausted", "mark_paid_other", "write_off")

_BILL: dict[tuple[str, str], str] = {
    ("open", "claim"): "claimed",
    ("open", "pay"): "paid",
    ("open", "cancel"): "cancelled",
    ("open", "expire"): "expired",
    ("claimed", "release"): "open",
    ("claimed", "claim_idle"): "open",
    ("claimed", "pay"): "paid",
    ("claimed", "cancel"): "cancelled",
    ("claimed", "abandon"): "abandoned",
    ("abandoned", "reminders_exhausted"): "needs_follow_up",
}
for _s in ("abandoned", "needs_follow_up"):
    _BILL[(_s, "pay")] = "paid"
    _BILL[(_s, "mark_paid_other")] = "paid_other"
    _BILL[(_s, "write_off")] = "written_off"
    _BILL[(_s, "cancel")] = "cancelled"

SESSION_STATES = ("claimed", "awaiting_amount", "awaiting_tip", "awaiting_confirm",
                  "awaiting_payment", "paid", "failed", "expired", "cancelled",
                  "partially_refunded", "refunded")
SESSION_EVENTS = ("need_amount", "amount_set", "tip_chosen", "change_tip", "pay_now",
                  "payment_succeeded", "payment_failed", "expire", "cancel",
                  "partial_refund", "full_refund")

_SESSION: dict[tuple[str, str], str] = {
    ("claimed", "need_amount"): "awaiting_amount",
    ("claimed", "amount_set"): "awaiting_tip",
    ("awaiting_amount", "amount_set"): "awaiting_tip",
    ("awaiting_tip", "tip_chosen"): "awaiting_confirm",
    ("awaiting_confirm", "change_tip"): "awaiting_tip",
    ("awaiting_confirm", "pay_now"): "awaiting_payment",
    ("awaiting_payment", "payment_succeeded"): "paid",
    ("awaiting_payment", "payment_failed"): "failed",
    ("paid", "partial_refund"): "partially_refunded",
    ("paid", "full_refund"): "refunded",
    ("partially_refunded", "partial_refund"): "partially_refunded",
    ("partially_refunded", "full_refund"): "refunded",
}
# Expiry/cancel apply to every pre-payment state (a release or amount edit cancels the session;
# the 10-minute TTL expires it). Beyond spec §6.2, which only lists awaiting_payment.
for _s in ("claimed", "awaiting_amount", "awaiting_tip", "awaiting_confirm", "awaiting_payment"):
    _SESSION[(_s, "expire")] = "expired"
    _SESSION[(_s, "cancel")] = "cancelled"

SESSION_TERMINAL = frozenset({"failed", "expired", "cancelled", "refunded"})
SESSION_PRE_PAYMENT = frozenset({"claimed", "awaiting_amount", "awaiting_tip",
                                 "awaiting_confirm", "awaiting_payment"})


def bill_next(state: str, event: str) -> str:
    try:
        return _BILL[(state, event)]
    except KeyError:
        raise IllegalTransition("bill", state, event) from None


def session_next(state: str, event: str) -> str:
    try:
        return _SESSION[(state, event)]
    except KeyError:
        raise IllegalTransition("session", state, event) from None


def legal_bill_pairs() -> frozenset[tuple[str, str]]:
    return frozenset(_BILL)


def legal_session_pairs() -> frozenset[tuple[str, str]]:
    return frozenset(_SESSION)


def all_bill_pairs():
    return product(BILL_STATES, BILL_EVENTS)


def all_session_pairs():
    return product(SESSION_STATES, SESSION_EVENTS)
