"""A scanned document must be filed on the right side of the ledger.

2026-09-03, real off-the-hook data: the scan-commit path hardcoded direction="inbound", so EVERY
document scanned from email became a supplier bill — including 51 of OTH's own outgoing sales
invoices totalling R32,307.97, where both `supplier` and `customer_name` came out as
"Off the Hook". That is ~29% of their reported money OUT, and it is money IN, so a cash-flow or
payables report built on it would be wrong in both directions at once.

Bulk historical imports are the harder case (Ian, 2026-09-03): a tenant uploads a folder of old
paperwork mixing their own client invoices, supplier bills and expense slips. The extractor gives
the ISSUER only — there is no bill-to field — so an unrecognised name is genuinely ambiguous and
must be asked about, not guessed.

Verified against production before writing: with each tenant's REAL display_name, 51 off-the-hook
invoices are misfiled and ZERO for digg-demo and gerflor. (An earlier run of that check used a
made-up tenant name and appeared to show R5,019,897 misfiled on DIGG — an artefact, not a
finding. Hence these tests pin the name-matching precisely.)
"""
import pytest

from vula.commerce.service import _same_business, classify_direction

OTH = "Off the Hook"


# ── is this the same business? ──────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("OfftheHook", "Off the Hook"),          # the two real spellings in OTH's data
    ("Off the Hook", "off-the-hook"),        # display name vs tenant_id
    ("OfftheHook", "OFF THE HOOK"),
    ("ACME (Pty) Ltd", "Acme Pty"),          # needs BOTH suffixes stripped
    ("Acme Limited", "ACME"),
])
def test_spelling_variants_are_the_same_business(a, b):
    assert _same_business(a, b) is True


@pytest.mark.parametrize("a,b", [
    ("Atlas Foods Distributors", "Atlantis Foods"),   # genuinely different, similar-looking
    ("Atlantis Foods", "Atlantis Seafood Distributors"),
    ("Pick n Pay", OTH),
    ("", OTH),                                        # empty must never match
    ("Ltd", OTH),                                     # suffix-only must never match
])
def test_different_businesses_are_kept_apart(a, b):
    assert _same_business(a, b) is False


# ── direction, and how sure we are ──────────────────────────────────────────────

def test_a_document_the_tenant_issued_is_outbound():
    """The real 51-invoice case: issuer IS the tenant, so it's their sale, not a bill."""
    for issuer in ("OfftheHook", "Off the Hook", "OFF THE HOOK"):
        direction, confident, reason = classify_direction(issuer, OTH, "off-the-hook")
        assert direction == "outbound"
        assert confident is True
        assert "issued by this business" in reason


def test_a_known_supplier_is_inbound_and_confident():
    direction, confident, _ = classify_direction(
        "Atlantis Seafood Distributors", OTH, "off-the-hook", supplier_known=True)
    assert (direction, confident) == ("inbound", True)


@pytest.mark.parametrize("issuer", [
    "Bloggs Architects",             # could be a client whose invoice is being imported
    "Some New Trader (Pty) Ltd",     # or an entirely new supplier
])
def test_an_unrecognised_party_is_not_guessed_at(issuer):
    """The bulk-import case: unknown issuer is a coin flip, so it must be asked about."""
    direction, confident, reason = classify_direction(issuer, OTH, "off-the-hook",
                                                      supplier_known=False)
    assert confident is False, "an unknown party must not be filed with confidence"
    assert direction == "inbound", "defaults to today's behaviour while flagged for review"
    assert "could be a new supplier or a client" in reason


def test_a_document_with_no_issuer_is_flagged_too():
    direction, confident, reason = classify_direction("", OTH, "off-the-hook")
    assert confident is False
    assert "no issuer found" in reason


def test_being_a_known_supplier_never_overrides_our_own_document():
    """If we issued it, it's ours — even if a supplier of the same name exists."""
    direction, confident, _ = classify_direction("Off the Hook", OTH, "off-the-hook",
                                                 supplier_known=True)
    assert (direction, confident) == ("outbound", True)


def test_matching_falls_back_to_the_tenant_id():
    """Some tenants have no display_name set; the id is the remaining signal."""
    direction, confident, _ = classify_direction("off-the-hook", "", "off-the-hook")
    assert (direction, confident) == ("outbound", True)


# ── the guarantee that matters most ─────────────────────────────────────────────

@pytest.mark.parametrize("issuer", [
    "Atlantis Seafood Distributors (Pty) Ltd",
    "Pick n Pay",
    "Atlas Foods Distributors",
    "Maintenance Solutions",
    "Blueline Bookkeeping",
])
def test_a_real_supplier_is_never_recorded_as_our_own_invoice(issuer):
    """Failing safe: a genuine supplier bill misfiled as our sale would invent revenue."""
    direction, _, _ = classify_direction(issuer, OTH, "off-the-hook")
    assert direction == "inbound"
