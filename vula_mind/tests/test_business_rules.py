"""Standing instructions the owner gives must actually be kept (migration 152).

Real Gerflor message, 2026-08-28 07:00, owner to Vula on WhatsApp:

  "...make sure when pricing that you price with the correct discounts. Per-Square price list all
   is NETT (No further discounts apply). DT is subject to 7% Trade discount, excluding the
   Mactile which is NETT. Secondly make sure you price the correct Zone for DT. Per-Square
   doesn't fall into zones. ... Please check with Michelle before pricing items on SPM and myself
   on Gerflor until you get the pricing structure."

A complete pricing policy, stated plainly. Vula replied "I was unable to find the correct pricing
structure for distributors" and retained nothing. On 2026-08-31 the same class of question got
the same empty answer — the owner had already given it.
"""
from unittest.mock import patch

import pytest

from vula.commerce import business_rules as br
from core.skills.commerce_admin import CommerceAdminSkill, KNOWLEDGE_TOOLS

TENANT = "gerflor"

REAL_RULE = ("Per-Square price list all is NETT (No further discounts apply). DT is subject to "
             "7% Trade discount, excluding the Mactile which is NETT.")


class _Tbl:
    def __init__(self, sink, rows):
        self.sink, self._rows = sink, rows

    def insert(self, row):
        self.sink.setdefault("inserted", []).append(row)
        self._rows = [dict(row, id="r-new")]
        return self

    def update(self, patch_):
        self.sink.setdefault("updated", []).append(patch_)
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _db(sink, rows=()):
    return type("C", (), {"table": lambda self, n: _Tbl(sink, list(rows))})()


# ── storing ─────────────────────────────────────────────────────────────────────

def test_the_real_pricing_policy_is_stored_verbatim():
    sink = {}
    with patch.object(br, "_client", lambda: _db(sink)):
        out = br.add_rule(TENANT, REAL_RULE, topic="pricing", created_by="27739852984")
    assert out["status"] == "saved"
    stored = sink["inserted"][0]
    assert stored["rule"] == REAL_RULE, "the owner's own words, not a paraphrase"
    assert stored["topic"] == "pricing"
    assert stored["status"] == "active"


def test_an_empty_rule_is_refused():
    with patch.object(br, "_client", lambda: _db({})):
        assert "error" in br.add_rule(TENANT, "   ")


def test_a_pasted_document_is_refused_as_a_rule():
    """A rule is an instruction; a document belongs in the KB."""
    with patch.object(br, "_client", lambda: _db({})):
        out = br.add_rule(TENANT, "x" * (br.MAX_RULE_CHARS + 1))
    assert "too long" in out["error"]


def test_repeating_the_same_rule_does_not_duplicate_it():
    existing = [{"id": "r1", "rule": REAL_RULE, "topic": "pricing"}]
    sink = {}
    with patch.object(br, "_client", lambda: _db(sink, existing)):
        out = br.add_rule(TENANT, REAL_RULE.upper(), topic="pricing")
    assert out["status"] == "already_known"
    assert "inserted" not in sink


def test_there_is_a_ceiling_on_how_many_rules_are_kept():
    many = [{"id": f"r{i}", "rule": f"rule {i}"} for i in range(br.MAX_RULES)]
    with patch.object(br, "_client", lambda: _db({}, many)):
        out = br.add_rule(TENANT, "one more")
    assert "Archive one first" in out["error"]


def test_a_missing_table_degrades_quietly():
    class _Boom:
        def table(self, n):
            raise Exception("relation vula_business_rules does not exist")
    with patch.object(br, "_client", lambda: _Boom()):
        assert br.active_rules(TENANT) == []
        assert "error" in br.add_rule(TENANT, "something")


# ── the prompt block ────────────────────────────────────────────────────────────

def test_rules_block_is_empty_when_nothing_is_stored():
    with patch.object(br, "_client", lambda: _db({}, [])):
        assert br.rules_block(TENANT) == ""


def test_rules_block_carries_the_policy_and_binds_the_answer():
    rows = [{"id": "r1", "rule": REAL_RULE, "topic": "pricing"},
            {"id": "r2", "rule": "Check with Michelle before pricing items on SPM.",
             "topic": "approvals"}]
    with patch.object(br, "_client", lambda: _db({}, rows)):
        block = br.rules_block(TENANT)
    assert "7% Trade discount" in block
    assert "Michelle" in block
    assert "[pricing]" in block and "[approvals]" in block
    assert "apply to every relevant answer" in block
    assert "check with a person before answering" in block


def test_rules_block_is_bounded():
    rows = [{"id": f"r{i}", "rule": "y" * 500, "topic": "t"} for i in range(10)]
    with patch.object(br, "_client", lambda: _db({}, rows)):
        assert len(br.rules_block(TENANT)) < 3200


def test_the_block_reaches_the_real_commerce_admin_prompt():
    rows = [{"id": "r1", "rule": REAL_RULE, "topic": "pricing"}]
    with patch.object(br, "_client", lambda: _db({}, rows)):
        prompt = CommerceAdminSkill()._system_prompt(TENANT)
    assert "7% Trade discount" in prompt
    assert "STANDING INSTRUCTIONS FROM THE OWNER" in prompt


def test_a_tenant_with_no_rules_sees_an_unchanged_prompt():
    with patch.object(br, "_client", lambda: _db({}, [])):
        prompt = CommerceAdminSkill()._system_prompt(TENANT)
    assert "STANDING INSTRUCTIONS" not in prompt


# ── the tools ───────────────────────────────────────────────────────────────────

def test_the_rule_tools_are_registered():
    names = [t["function"]["name"] for t in KNOWLEDGE_TOOLS]
    for n in ("remember_rule", "list_rules", "forget_rule"):
        assert n in names


def test_remember_rule_tool_distinguishes_a_rule_from_a_one_off():
    d = next(t for t in KNOWLEDGE_TOOLS
             if t["function"]["name"] == "remember_rule")["function"]["description"].lower()
    assert "not use this for a one-off request" in d


@pytest.mark.asyncio
async def test_remember_rule_saves_and_records_who_said_it():
    sink = {}
    with patch.object(br, "_client", lambda: _db(sink)):
        out = CommerceAdminSkill()._rule_tool(
            "remember_rule", TENANT, {"rule": REAL_RULE, "topic": "pricing"},
            {"phone": "27739852984"})
    assert out["status"] == "saved"
    assert sink["inserted"][0]["created_by"] == "27739852984"


@pytest.mark.asyncio
async def test_list_rules_says_so_when_empty():
    with patch.object(br, "_client", lambda: _db({}, [])):
        out = CommerceAdminSkill()._rule_tool("list_rules", TENANT, {}, {})
    assert out["count"] == 0
    assert "Nothing saved yet" in out["message"]


@pytest.mark.asyncio
async def test_forget_rule_matches_on_a_fragment():
    rows = [{"id": "r1", "rule": REAL_RULE, "topic": "pricing"}]
    sink = {}
    with patch.object(br, "_client", lambda: _db(sink, rows)):
        out = CommerceAdminSkill()._rule_tool("forget_rule", TENANT,
                                              {"match": "7% Trade"}, {})
    assert out["status"] == "forgotten"
    assert sink["updated"][0]["status"] == "archived"


@pytest.mark.asyncio
async def test_forget_rule_that_matches_nothing_does_not_archive_something_else():
    rows = [{"id": "r1", "rule": REAL_RULE, "topic": "pricing"}]
    sink = {}
    with patch.object(br, "_client", lambda: _db(sink, rows)):
        out = CommerceAdminSkill()._rule_tool("forget_rule", TENANT,
                                              {"match": "delivery windows"}, {})
    assert "error" in out
    assert "updated" not in sink
