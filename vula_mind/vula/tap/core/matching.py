"""Bill matching on a verified tap (spec §5) and the 4-digit bill-code gate. Pure.

`resolve_tap` decides WHAT should happen; the caller performs it with an atomic DB update (the
claim itself must be a conditional UPDATE ... WHERE status='open' so two simultaneous taps yield
exactly one winner — this function only picks the candidate).
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

HELD_STATUSES = ("claimed", "abandoned", "needs_follow_up")   # someone has this bill
LIVE_STATUSES = ("open",) + HELD_STATUSES
MAX_CODE_ATTEMPTS = 3
CODE_LOCK = timedelta(minutes=15)

# decision kinds
QUICK_TIP = "quick_tip"
CLAIM = "claim"              # use/claim exactly this bill
CHOOSE = "choose"            # several bills addressed to this number: send a list
LOCKED = "locked"            # claimed by another number
CODE_NEEDED = "code_needed"  # addressed to another number: ask for the 4-digit code
CODE_LOCKED = "code_locked"  # too many wrong codes; try again later
ASK_AMOUNT = "ask_amount"    # open-amount tag, no bill: customer types the amount
NO_OPEN_BILL = "no_open_bill"


@dataclass(frozen=True)
class BillView:
    id: str
    status: str                       # open | claimed | ...
    customer_hash: str | None = None  # HMAC of the number the bill is addressed to
    claimed_by_hash: str | None = None


@dataclass(frozen=True)
class Decision:
    kind: str
    bill_id: str | None = None
    bill_ids: tuple[str, ...] = ()


def resolve_tap(*, tag_mode: str, payer_hash: str, bills: Sequence[BillView],
                tag_allows_open_amount: bool = False, code_locked: bool = False) -> Decision:
    """`bills` = this tag's live (open or claimed) bills."""
    if tag_mode == "quick_tip":
        return Decision(QUICK_TIP)

    live = [b for b in bills if b.status in LIVE_STATUSES]

    # a tap from the number that already holds a claim re-uses that claim (double tap), and a customer
    # who left an unpaid bill (abandoned / needs follow-up) picks it up again
    mine_claimed = [b for b in live if b.status in HELD_STATUSES and b.claimed_by_hash == payer_hash]
    if mine_claimed:
        return Decision(CLAIM, mine_claimed[0].id)

    addressed = [b for b in live if b.status == "open" and b.customer_hash == payer_hash]
    if len(addressed) == 1:
        return Decision(CLAIM, addressed[0].id)
    if len(addressed) > 1:
        return Decision(CHOOSE, bill_ids=tuple(sorted(b.id for b in addressed)))

    claimable = [b for b in live if b.status == "open" and b.customer_hash is None]
    if claimable:
        # at most one per tag (partial unique index); oldest-id first if data is ever dirty
        return Decision(CLAIM, sorted(claimable, key=lambda b: b.id)[0].id)

    if any(b.status in HELD_STATUSES for b in live):
        return Decision(LOCKED)

    if any(b.status == "open" and b.customer_hash for b in live):
        return Decision(CODE_LOCKED if code_locked else CODE_NEEDED)

    if tag_allows_open_amount:
        return Decision(ASK_AMOUNT)
    return Decision(NO_OPEN_BILL)


@dataclass(frozen=True)
class CodeGate:
    attempts: int = 0
    locked_until: datetime | None = None


def check_bill_code(expected: str, given: str, gate: CodeGate, now: datetime) -> tuple[bool, CodeGate]:
    """Constant-time compare with a 3-attempt limit and a 15-minute lock afterwards."""
    if gate.locked_until and now < gate.locked_until:
        return False, gate
    if gate.locked_until and now >= gate.locked_until:
        gate = CodeGate()
    if hmac.compare_digest(expected.encode(), (given or "").strip().encode()):
        return True, CodeGate()
    attempts = gate.attempts + 1
    if attempts >= MAX_CODE_ATTEMPTS:
        return False, CodeGate(attempts, now + CODE_LOCK)
    return False, CodeGate(attempts)
