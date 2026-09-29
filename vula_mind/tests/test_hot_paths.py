"""PR B (2026-09-29): the per-message hot path reads the team once, and the Learn card's polled
price-book summary doesn't load every observation."""
from unittest.mock import MagicMock, patch

import pytest

from vula import team_index

TEAM = [
    {"id": "m1", "name": "Judy", "whatsapp": "0827077080", "role": "owner", "access": [], "notify": []},
    {"id": "m2", "name": "Sipho", "whatsapp": "27645755210", "role": "sales_rep", "access": ["crm"],
     "notify": []},
    {"id": "m3", "name": "Anna", "whatsapp": "27710000000", "role": "staff", "access": ["orders"],
     "notify": ["new_order"]},
]


def _db(rows, calls):
    table = MagicMock()

    def execute():
        calls["n"] += 1
        return MagicMock(data=[dict(r) for r in rows])
    table.select.return_value.eq.return_value.eq.return_value.execute.side_effect = execute
    db = MagicMock()
    db.table.return_value = table
    return db


@pytest.mark.asyncio
async def test_every_hot_path_check_shares_one_team_read():
    from vula.api import whatsapp as wa
    from vula.api import yoco
    from core.skills.commerce_admin import _member_access
    calls = {"n": 0}
    with patch("vula.commerce.service._client", return_value=_db(TEAM, calls)):
        assert wa._caller_identity("digg-demo", "+27 82 707 7080") == ("Judy", "owner")
        assert wa._is_tenant_owner("digg-demo", "27827077080")
        assert await wa._sender_is_sales_rep("0645755210", "digg-demo")
        assert not await wa._sender_is_sales_rep("27710000000", "digg-demo")
        assert _member_access("digg-demo", "27710000000") == ["orders"]
        assert _member_access("digg-demo", "27827077080") is None           # owner: full access
        assert [p for _n, p, _r in yoco._tenant_team("digg-demo")] == ["0827077080", "27710000000"]
    assert calls["n"] == 1


def test_a_team_edit_is_seen_on_the_next_message():
    calls = {"n": 0}
    rows = [dict(TEAM[0])]
    with patch("vula.commerce.service._client", return_value=_db(rows, calls)):
        assert team_index.member_for_phone("gerflor", "27645755210") is None
        rows.append(dict(TEAM[1]))
        team_index.invalidate("gerflor")                                   # what team.py does
        assert team_index.member_for_phone("gerflor", "27645755210")["role"] == "sales_rep"
    assert calls["n"] == 2


def test_a_failed_team_read_is_not_cached():
    class Boom:
        def table(self, name):
            raise RuntimeError("supabase blip")
    with patch("vula.commerce.service._client", return_value=Boom()):
        with pytest.raises(RuntimeError):
            team_index.active_members("off-the-hook")
    assert "off-the-hook" not in team_index._CACHE
    calls = {"n": 0}
    with patch("vula.commerce.service._client", return_value=_db(TEAM, calls)):
        assert len(team_index.active_members("off-the-hook")) == 3


def test_owner_check_still_falls_back_to_the_static_team_when_the_db_is_down():
    from vula.api import whatsapp as wa

    class Boom:
        def table(self, name):
            raise RuntimeError("down")
    with patch("vula.commerce.service._client", return_value=Boom()):
        assert wa._is_tenant_owner("digg-demo", "27827077080")             # yoco._TENANT_TEAM


def test_cached_rows_cannot_be_mutated_by_a_caller():
    calls = {"n": 0}
    with patch("vula.commerce.service._client", return_value=_db(TEAM, calls)):
        team_index.active_members("digg-demo")[0]["role"] = "customer"
        assert team_index.active_members("digg-demo")[0]["role"] == "owner"


def test_price_book_summary_reads_only_what_it_counts_and_is_reused(monkeypatch):
    from vula.commerce import price_book
    rows = [
        {"norm_key": "solid 12mm board", "unit": "each", "kind": "material", "supplier": "Solid Cape",
         "project": "HPC Bokaap"},
        {"norm_key": "solid 12mm board", "unit": "each", "kind": "material", "supplier": "SOLID CAPE",
         "project": "Porterfield"},
        {"norm_key": "tiling labour", "unit": "m2", "kind": "labour", "supplier": None, "project": None},
    ]
    selects = []

    class Q:
        def __init__(self):
            self.cols = None

        def select(self, cols):
            selects.append(cols)
            return self

        def eq(self, *a):
            return self

    monkeypatch.setattr(price_book, "_client", lambda: MagicMock(table=lambda _t: Q()))
    monkeypatch.setattr("vula.commerce.ledger._all_pages", lambda make: (make(), rows)[1])
    price_book._summary_cache.clear()
    out = price_book.summary("digg-demo")
    assert (out["priced_lines"], out["items"], out["labour_rates"], out["suppliers"], out["projects"]) \
        == (3, 2, 1, 1, 2)
    assert selects == ["norm_key,unit,kind,supplier,project"]
    price_book.summary("digg-demo")                                        # polled again: cached
    assert len(selects) == 1
    price_book.summary("digg-demo", fresh=True)
    assert len(selects) == 2
    price_book._summary_cache.clear()
