"""Money, tip and split maths. Integer cents only — no floats anywhere near money.

Rounding rules (spec §7/§8.2):
  * a percentage tip is rounded half-up to the cent, on the bill amount only;
  * every non-remainder party's share is the FLOOR of amount x percent; the remainder goes to the
    merchant, so the lines always sum exactly to the payment.
Percentages are carried as integer basis points (1% = 100 bp) so no Decimal/float is needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

BP = 10_000

TIP_DIRECT = "direct"
TIP_POOL = "pool"
TIP_HOUSE_CUT = "house_cut"


class MoneyError(ValueError):
    """Customer-facing input problem (bad amount). Callers re-ask rather than crash."""


_RANDS_RE = re.compile(r"^\s*(?:R|r)?\s*(\d{1,7})(?:[.,](\d{1,2}))?\s*$")


def parse_rands(text: str) -> int:
    """'15' / 'R15,50' / '15.5' -> cents. Raises MoneyError on anything else (no floats used)."""
    m = _RANDS_RE.match(text or "")
    if not m:
        raise MoneyError("not a rand amount")
    whole, frac = m.group(1), (m.group(2) or "")
    return int(whole) * 100 + int(frac.ljust(2, "0") or 0)


def format_rands(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}R {cents // 100:,}.{cents % 100:02d}".replace(",", " ")


def pct_tip(bill_cents: int, percent_bp: int) -> int:
    """Percent tip on the bill amount, rounded half up to the cent."""
    if bill_cents < 0 or percent_bp < 0:
        raise MoneyError("negative amount")
    return (bill_cents * percent_bp + BP // 2) // BP


def validate_custom_tip(text: str, bill_cents: int, *, max_pct_bp: int = BP,
                        min_cents: int = 100) -> int:
    """Custom tip in rands: 0 allowed, otherwise >= min (R1.00) and <= max_pct of the bill."""
    cents = parse_rands(text)
    if cents == 0:
        return 0
    if cents < min_cents:
        raise MoneyError("tip below minimum")
    if cents > bill_cents * max_pct_bp // BP:
        raise MoneyError("tip above cap")
    return cents


def validate_quick_tip(text: str, *, min_cents: int = 200, max_cents: int = 100_000) -> int:
    cents = parse_rands(text)
    if not (min_cents <= cents <= max_cents):
        raise MoneyError("amount outside allowed range")
    return cents


def validate_open_amount(text: str, *, min_cents: int = 100, max_cents: int = 10_000_000) -> int:
    cents = parse_rands(text)
    if not (min_cents <= cents <= max_cents):
        raise MoneyError("amount outside allowed range")
    return cents


def decimal_to_bp(percent: Decimal | str | int) -> int:
    """Config helper: '12.5' (percent) -> 1250 bp. Rejects more than 2 decimals of a percent."""
    try:
        bp = Decimal(str(percent)) * 100
    except InvalidOperation as e:
        raise MoneyError("bad percent") from e
    if bp != bp.to_integral_value() or bp < 0 or bp > BP:
        raise MoneyError("bad percent")
    return int(bp)


# ── splits ────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Line:
    """One ledger line. Credits are > 0; fee deductions are < 0 and attributed to the party that
    bears them. `kind` is bill | tip | provider_fee | platform_fee."""
    kind: str
    party: str
    cents: int


@dataclass(frozen=True)
class Allocation:
    gross_cents: int
    provider_fee_cents: int
    platform_fee_cents: int
    lines: tuple[Line, ...]

    def credits(self) -> int:
        return sum(l.cents for l in self.lines if l.cents > 0)

    def deductions(self) -> int:
        return -sum(l.cents for l in self.lines if l.cents < 0)

    def net_by_party(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for l in self.lines:
            out[l.party] = out.get(l.party, 0) + l.cents
        return out


def split_floor(amount: int, shares_bp: Mapping[str, int], remainder_party: str) -> dict[str, int]:
    """Each party in `shares_bp` gets floor(amount x bp / 10000); `remainder_party` gets the rest."""
    if amount < 0:
        raise MoneyError("negative amount")
    if sum(shares_bp.values()) > BP or any(v < 0 for v in shares_bp.values()):
        raise MoneyError("shares exceed 100%")
    out = {p: amount * bp // BP for p, bp in shares_bp.items() if p != remainder_party}
    out[remainder_party] = amount - sum(out.values())
    return out


def split_pool(amount: int, members: Sequence[str], hours: Mapping[str, int] | None = None) -> dict[str, int]:
    """Equal split (or by hours worked). Floors each share; leftover cents go one each to members in
    sorted order, so the result is deterministic and sums exactly to `amount`."""
    if amount < 0:
        raise MoneyError("negative amount")
    ordered = sorted(set(members))
    if not ordered:
        raise MoneyError("empty pool")
    weights = {m: (hours or {}).get(m, 0) for m in ordered}
    if not hours or sum(weights.values()) <= 0:
        weights = {m: 1 for m in ordered}
    total_w = sum(weights.values())
    out = {m: amount * weights[m] // total_w for m in ordered}
    left = amount - sum(out.values())
    for m in ordered:
        if left <= 0:
            break
        out[m] += 1
        left -= 1
    return out


def _add(acc: dict[tuple[str, str], int], kind: str, party: str, cents: int) -> None:
    if cents:
        acc[(kind, party)] = acc.get((kind, party), 0) + cents


def allocate_payment(
    *,
    bill_cents: int,
    tip_cents: int,
    merchant: str,
    staff: str | None,
    staff_share_bp: int = 0,
    tip_rule: str = TIP_DIRECT,
    house_cut_bp: int = 0,
    house_cut_then: str = TIP_DIRECT,
    pool_members: Sequence[str] = (),
    pool_hours: Mapping[str, int] | None = None,
    provider_fee_cents: int = 0,
    platform_fee_cents: int = 0,
    merchant_absorbs_provider_fee: bool = False,
) -> Allocation:
    """Split one customer payment (bill + tip) into per-party ledger lines.

    Invariants (property-tested): sum of credit lines == bill + tip; sum of deduction lines ==
    provider fee + platform fee; so sum(all lines) + fees == gross, to the cent.

    Bill: the staff member serving gets `staff_share_bp` of the bill (floored), merchant the rest.
    Tip: `direct` -> staff tied to the bill; `pool` -> shift members; `house_cut` -> `house_cut_bp`
    to the merchant, the rest by `house_cut_then` (direct|pool). No staff tied / empty pool: the
    unallocatable tip is held on the merchant line (never silently dropped) for the manager to
    distribute.
    Fees: provider fee is shared across parties in proportion to their gross credit (floored, the
    merchant takes the remainder) unless `merchant_absorbs_provider_fee`. The platform fee is
    always borne by the merchant.
    """
    for v in (bill_cents, tip_cents, provider_fee_cents, platform_fee_cents):
        if v < 0:
            raise MoneyError("negative amount")
    if not 0 <= staff_share_bp <= BP or not 0 <= house_cut_bp <= BP:
        raise MoneyError("bad share")
    gross = bill_cents + tip_cents
    if provider_fee_cents + platform_fee_cents > gross:
        raise MoneyError("fees exceed payment")

    acc: dict[tuple[str, str], int] = {}

    # bill
    if staff and staff_share_bp:
        for p, c in split_floor(bill_cents, {staff: staff_share_bp}, merchant).items():
            _add(acc, "bill", p, c)
    else:
        _add(acc, "bill", merchant, bill_cents)

    # tip
    def _tip_to(rule: str, amount: int) -> None:
        if rule == TIP_POOL and pool_members:
            for p, c in split_pool(amount, pool_members, pool_hours).items():
                _add(acc, "tip", p, c)
        elif rule == TIP_DIRECT and staff:
            _add(acc, "tip", staff, amount)
        elif staff and not pool_members:
            _add(acc, "tip", staff, amount)
        else:
            _add(acc, "tip", merchant, amount)  # held for the manager

    if tip_rule == TIP_HOUSE_CUT:
        cut = tip_cents * house_cut_bp // BP
        _add(acc, "tip", merchant, cut)
        _tip_to(house_cut_then, tip_cents - cut)
    else:
        _tip_to(tip_rule, tip_cents)

    # fees
    credit_by_party: dict[str, int] = {}
    for (_, party), c in acc.items():
        credit_by_party[party] = credit_by_party.get(party, 0) + c
    if merchant_absorbs_provider_fee:
        fee_by_party = {merchant: provider_fee_cents} if provider_fee_cents else {}
    else:
        fee_by_party = {}
        if gross and provider_fee_cents:
            others = {p: provider_fee_cents * c // gross for p, c in credit_by_party.items() if p != merchant}
            others[merchant] = provider_fee_cents - sum(others.values())
            fee_by_party = {p: v for p, v in others.items() if v}
    lines = [Line(k, p, c) for (k, p), c in sorted(acc.items())]
    lines += [Line("provider_fee", p, -v) for p, v in sorted(fee_by_party.items())]
    if platform_fee_cents:
        lines.append(Line("platform_fee", merchant, -platform_fee_cents))
    return Allocation(gross, provider_fee_cents, platform_fee_cents, tuple(lines))


def reverse_lines(lines: Sequence[Line], refund_cents: int, gross_cents: int) -> tuple[Line, ...]:
    """Proportional reversal for a (partial) refund: each line is reversed by floor(line x refund /
    gross); the largest-magnitude line absorbs the rounding remainder so a full refund reverses every
    line exactly and a partial one reverses exactly `refund_cents` of credit."""
    if not 0 < refund_cents <= gross_cents:
        raise MoneyError("bad refund amount")
    if refund_cents == gross_cents:
        return tuple(Line(l.kind, l.party, -l.cents) for l in lines)
    credits = [l for l in lines if l.cents > 0]
    credit_total = sum(l.cents for l in credits)
    out = [Line(l.kind, l.party, -(l.cents * refund_cents // credit_total)) for l in credits]
    drift = refund_cents + sum(l.cents for l in out)
    if drift and out:
        i = max(range(len(credits)), key=lambda k: credits[k].cents)
        out[i] = Line(out[i].kind, out[i].party, out[i].cents - drift)
    # fees come back proportionally as well (the provider refunds its share pro rata)
    for l in lines:
        if l.cents < 0:
            out.append(Line(l.kind, l.party, -(l.cents * refund_cents // gross_cents)))
    return tuple(out)
