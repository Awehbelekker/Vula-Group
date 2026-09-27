"""Customer product matching tolerates plurals/word order and suggests near misses (2026-09-25)."""
from core.skills.commerce_assistant import _match_product, _suggest_products

P = [{"name": "Hake Fillet"}, {"name": "Hake Whole"}, {"name": "Tiger Prawns 1kg"}, {"name": "Yellowtail"}]


def test_plural_and_order():
    assert _match_product("hake fillets", P)["name"] == "Hake Fillet"
    assert _match_product("prawns tiger", P)["name"] == "Tiger Prawns 1kg"
    assert _match_product("yellowtail", P)["name"] == "Yellowtail"
    assert _match_product("yelowtail", P)["name"] == "Yellowtail"   # close spelling


def test_ambiguous_or_unknown_does_not_guess():
    assert _match_product("hake", P)["name"] in ("Hake Fillet", "Hake Whole")  # substring (old rule)
    assert _match_product("salmon", P) is None
    assert "Hake Fillet" in _suggest_products("hake steaks", P)
