"""Schema-consistency guard between what the document-extraction pipeline WRITES into a filed
document's `fields` and what vula.commerce.service._document_amount READS from it.

Real incident, 2026-09-21: reasoning.py's/find_document's amount extraction only checked
`amount`/`total`/`amount_rands` while the actual money-document pipeline wrote `total_cents` —
a real invoice's amount silently came back None everywhere for days before a live WhatsApp
transcript caught it (see tests/test_service_find_filed_document.py). Fixing that surfaced a
second, live instance of the exact same class within the hour: Proof of Payment documents write
`amount_cents` instead, confirmed against real production data across multiple tenants, and
_document_amount didn't check that one either.

Both were "the writer's schema and the reader's checked keys drifted apart, and nothing noticed
until a human read a real transcript." This test makes that drift impossible to merge silently
again: it regex-scans the actual extraction sources (the LLM prompt schema in
vula.api.whatsapp._analyze_document, and the deterministic FNB parser in
vula.ingestion.payment_notice) for every money-shaped field name they declare, and asserts
_document_amount's own source checks every one of them. A future prompt/schema change that
introduces a new money key fails this test immediately, in CI, rather than in production three
days later.

Deliberately source-level, not a live-data query: CI has no real Supabase to sample, and the bug
class is a code-level contract (writer key name vs. reader key name), not a data-shape question.
"""
import inspect
import re

from vula.api import whatsapp
from vula.commerce import service
from vula.ingestion import payment_notice

# Matches a quoted key immediately followed by ':' whose name contains "amount" or "total" —
# broad enough to catch both the JSON-schema-in-a-prompt-string style (`"total_cents": integer|
# null`) and a real Python dict literal (`"amount_cents": _cents(...)`), the two writer shapes
# in play here. Narrowed to *_cents / bare amount|total below so it doesn't pick up something
# unrelated like a "total" used in prose.
_MONEY_KEY_RE = re.compile(r'["\']([a-zA-Z_]*(?:amount|total)[a-zA-Z_]*)["\']\s*:')


def _declared_money_keys(src: str) -> set:
    return {k for k in _MONEY_KEY_RE.findall(src)
            if k.endswith("_cents") or k in ("amount", "total")}


def test_extraction_sources_declare_the_expected_money_keys():
    """Sanity check on the regex itself — if this stops finding total_cents/amount_cents, the
    scan is broken, not the pipeline (fail loud rather than the main test going quietly vacuous)."""
    declared = _declared_money_keys(inspect.getsource(whatsapp._analyze_document))
    declared |= _declared_money_keys(inspect.getsource(payment_notice))
    assert "total_cents" in declared
    assert "amount_cents" in declared


def test_document_amount_covers_every_money_key_the_extraction_pipeline_declares():
    """The actual regression guard: every money-bearing field name the extraction pipeline
    declares must be one _document_amount checks, or a real filed document's amount silently
    comes back None in find_document, reasoning, and architecture_planning alike."""
    declared = set()
    declared |= _declared_money_keys(inspect.getsource(whatsapp._analyze_document))
    declared |= _declared_money_keys(inspect.getsource(payment_notice))

    checked_src = inspect.getsource(service._document_amount)
    missing = [k for k in declared
               if f'"{k}"' not in checked_src and f"'{k}'" not in checked_src]
    assert not missing, (
        f"_document_amount doesn't check {missing} — the extraction pipeline declares "
        f"{sorted(declared)} as money-bearing fields, so a real filed document using one of "
        f"{missing} will have its amount silently come back None everywhere (find_document, "
        "reasoning, architecture_planning). Add it to _document_amount (cents fields need the "
        "÷100 conversion the *_cents branch already does).")
