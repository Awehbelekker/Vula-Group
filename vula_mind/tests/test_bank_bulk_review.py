"""Bulk review for a bank backlog that cannot clear one transaction at a time.

2026-09-06, measured on production: off-the-hook had 738 unmatched transactions and digg-demo
54, spanning 2026-05-11 to 2026-08-31, reviewable only via a WhatsApp queue that asks about one
row per message. Three months of banking does not clear that way.

Auto-matching them was the wrong instinct and the data said so — see
test_supplier_bill_match_needs_name. Most of the backlog is not matchable at all: 404 of
off-the-hook's rows are money IN against zero open invoices (counter sales), and the money-out
side is categorised card spend with no bill behind it. What it needs is grouping, bulk
categorisation that teaches a rule, and a way to leave the queue.
"""
from unittest.mock import MagicMock, patch

import pytest

from vula.api import commerce as capi

TENANT = "off-the-hook"


def _rows():
    return [
        {"id": "t1", "txn_date": "2026-06-01", "amount_cents": 45000, "direction": "out",
         "description": "Engen Winelands North (Card 572)", "match_status": "unmatched"},
        {"id": "t2", "txn_date": "2026-06-14", "amount_cents": 52000, "direction": "out",
         "description": "Engen Winelands North (Card 572)", "match_status": "unmatched"},
        {"id": "t3", "txn_date": "2026-07-02", "amount_cents": 18000, "direction": "out",
         "description": "EFT PAYMENT Engen Rietvlei", "match_status": "unmatched"},
        {"id": "t4", "txn_date": "2026-05-22", "amount_cents": 11070, "direction": "in",
         "description": "Payment Received: R MC KENZIE", "match_status": "unmatched"},
    ]


def _db(rows=None, updates=None):
    rows = _rows() if rows is None else rows

    class _Q:
        def __init__(self, name):
            self.name, self.f = name, {}

        def select(self, *a, **k): return self
        def order(self, *a, **k): return self
        def limit(self, *a, **k): return self

        def in_(self, col, vals):
            self.f[col] = vals
            return self

        def eq(self, col, val):
            self.f[col] = val
            return self

        def lt(self, col, val):
            self.lt_ = (col, val)
            return self

        def update(self, patch_):
            if updates is not None:
                updates.append(patch_)
            return self

        def execute(self):
            # Filters are honoured (on keys the row carries), not ignored: dismiss-by-filter is
            # ENTIRELY a filter, so a permissive mock would report every test as passing while
            # the endpoint dismissed the wrong slice.
            out = rows
            for col, val in self.f.items():
                want = val if isinstance(val, list) else [val]
                out = [r for r in out if col not in r or r.get(col) in want]
            lt_ = getattr(self, "lt_", None)
            if lt_:
                col, val = lt_
                out = [r for r in out if col not in r or (r.get(col) or "") < val]
            return MagicMock(data=out)

    return MagicMock(table=lambda n: _Q(n))


# ── grouping ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_repeat_merchants_collapse_into_one_decision():
    with patch.object(capi.service, "_client", lambda: _db()):
        out = await capi.admin_bank_transaction_groups(TENANT)
    by_key = {g["key"]: g for g in out["groups"]}
    assert by_key["engen winelands"]["count"] == 2
    assert by_key["engen winelands"]["total_cents"] == 97000
    assert set(by_key["engen winelands"]["txn_ids"]) == {"t1", "t2"}
    assert out["total_rows"] == 4


@pytest.mark.asyncio
async def test_bank_noise_words_do_not_become_the_group():
    """'EFT PAYMENT Engen Rietvlei' must group on the merchant, not on EFT/PAYMENT — those are
    accounting._STOP words that appear on nearly every line of a South African statement."""
    with patch.object(capi.service, "_client", lambda: _db()):
        out = await capi.admin_bank_transaction_groups(TENANT)
    keys = [g["key"] for g in out["groups"]]
    assert "engen rietvlei" in keys
    assert not any(k.startswith("eft") or k.startswith("payment") for k in keys)


@pytest.mark.asyncio
async def test_groups_are_biggest_first_so_the_backlog_shrinks_fastest():
    with patch.object(capi.service, "_client", lambda: _db()):
        out = await capi.admin_bank_transaction_groups(TENANT)
    counts = [g["count"] for g in out["groups"]]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.asyncio
async def test_direction_filter_separates_money_in_from_money_out():
    with patch.object(capi.service, "_client", lambda: _db()):
        out = await capi.admin_bank_transaction_groups(TENANT, direction="in")
    assert out["total_rows"] == 1
    assert out["groups"][0]["direction"] == "in"


@pytest.mark.asyncio
async def test_the_account_view_collapses_what_merchant_grouping_fragments():
    """Merchant grouping splits every "Payment Received: <person>" into its own group — 739 real
    off-the-hook rows became 273. Grouping by direction+account collapses the same rows to a
    handful, which is what makes a three-month backlog one decision instead of hundreds."""
    rows = [
        {"id": f"r{i}", "txn_date": "2026-06-01", "amount_cents": 1000, "direction": "in",
         "description": f"Payment Received: PERSON{i}", "account_code": "sales",
         "match_status": "unmatched"}
        for i in range(12)
    ]
    with patch.object(capi.service, "_client", lambda: _db(rows=rows)):
        merchant = await capi.admin_bank_transaction_groups(TENANT, group_by="merchant")
        account = await capi.admin_bank_transaction_groups(TENANT, group_by="account")
    assert merchant["group_count"] == 12, "one group per payer — the fragmentation problem"
    assert account["group_count"] == 1
    assert account["groups"][0]["count"] == 12
    assert account["groups"][0]["key"] == "in · sales"


# ── dismiss by filter ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dismiss_by_filter_previews_before_it_touches_anything():
    updates = []
    with patch.object(capi.service, "_client", lambda: _db(updates=updates)):
        out = await capi.admin_dismiss_by_filter(
            TENANT, capi.DismissByFilterIn(direction="out"))
    assert out["preview"] is True
    assert out["would_dismiss"] == 3
    assert updates == [], "a preview must not write"


@pytest.mark.asyncio
async def test_dismiss_by_filter_applies_only_on_confirm():
    updates = []
    with patch.object(capi.service, "_client", lambda: _db(updates=updates)):
        out = await capi.admin_dismiss_by_filter(
            TENANT, capi.DismissByFilterIn(direction="in", confirm=True))
    assert out["dismissed"] == 1
    assert updates == [{"match_status": "ignored"}]


@pytest.mark.asyncio
async def test_dismiss_by_filter_respects_a_date_cutoff():
    """"Clear everything before July" must not sweep up August."""
    with patch.object(capi.service, "_client", lambda: _db()):
        out = await capi.admin_dismiss_by_filter(
            TENANT, capi.DismissByFilterIn(before_date="2026-06-15"))
    assert out["would_dismiss"] == 3          # 2026-05-22, 2026-06-01, 2026-06-14
    assert "2026-07-02" not in out.get("message", "")


@pytest.mark.asyncio
async def test_dismiss_by_filter_narrows_to_one_account():
    rows = [
        {"id": "a", "txn_date": "2026-06-01", "amount_cents": 100, "direction": "in",
         "account_code": "sales", "match_status": "unmatched", "description": "x"},
        {"id": "b", "txn_date": "2026-06-01", "amount_cents": 200, "direction": "in",
         "account_code": "other_income", "match_status": "unmatched", "description": "y"},
    ]
    with patch.object(capi.service, "_client", lambda: _db(rows=rows)):
        out = await capi.admin_dismiss_by_filter(
            TENANT, capi.DismissByFilterIn(account_code="sales"))
    assert out["would_dismiss"] == 1
    assert out["total_cents"] == 100


@pytest.mark.asyncio
async def test_dismiss_by_filter_reports_the_value_leaving_the_queue():
    """The owner should see what they are waving through, not just a row count."""
    with patch.object(capi.service, "_client", lambda: _db()):
        out = await capi.admin_dismiss_by_filter(
            TENANT, capi.DismissByFilterIn(direction="out"))
    assert out["total_cents"] == 45000 + 52000 + 18000
    assert "R1,150.00" in out["message"]


# ── bulk categorize ─────────────────────────────────────────────────────────────

def _accounting(vat_cents=0):
    m = MagicMock()
    m.ensure_chart.return_value = [{"code": "fuel", "vat_treatment": "standard"}]
    m.is_vat_registered.return_value = True
    m.vat_for.return_value = vat_cents
    return m


@pytest.mark.asyncio
async def test_bulk_categorize_updates_every_row_it_was_given():
    updates = []
    acc = _accounting(vat_cents=5870)
    with patch.object(capi.service, "_client", lambda: _db(updates=updates)), \
         patch.dict("sys.modules", {}), \
         patch("vula.commerce.accounting.ensure_chart", acc.ensure_chart), \
         patch("vula.commerce.accounting.is_vat_registered", acc.is_vat_registered), \
         patch("vula.commerce.accounting.vat_for", acc.vat_for), \
         patch("vula.commerce.accounting.learn_category_rule") as learn:
        out = await capi.admin_bulk_categorize(
            TENANT, capi.BulkCategorizeIn(txn_ids=["t1", "t2"], account_code="fuel"))
    assert out["updated"] == 2
    assert all(u["account_code"] == "fuel" for u in updates)
    assert all(u["categorized_by"] == "owner" for u in updates)
    assert learn.call_count == 1, "learn once, not once per row"


@pytest.mark.asyncio
async def test_the_rule_is_learned_from_one_row_not_replayed_hundreds_of_times():
    """learn_category_rule increments a per-signal hit count. Replaying 400 identical rows
    would drown every other learned rule for that tenant."""
    acc = _accounting()
    with patch.object(capi.service, "_client", lambda: _db()), \
         patch("vula.commerce.accounting.ensure_chart", acc.ensure_chart), \
         patch("vula.commerce.accounting.is_vat_registered", acc.is_vat_registered), \
         patch("vula.commerce.accounting.vat_for", acc.vat_for), \
         patch("vula.commerce.accounting.learn_category_rule") as learn:
        await capi.admin_bulk_categorize(
            TENANT, capi.BulkCategorizeIn(txn_ids=["t1", "t2", "t3"], account_code="fuel"))
    learn.assert_called_once()
    assert learn.call_args[0][1]["id"] == "t1"


@pytest.mark.asyncio
async def test_an_unknown_account_is_refused():
    acc = _accounting()
    with patch.object(capi.service, "_client", lambda: _db()), \
         patch("vula.commerce.accounting.ensure_chart", acc.ensure_chart):
        with pytest.raises(capi.HTTPException) as e:
            await capi.admin_bulk_categorize(
                TENANT, capi.BulkCategorizeIn(txn_ids=["t1"], account_code="not_a_real_account"))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_batch_beyond_the_limit_is_refused_rather_than_half_applied():
    acc = _accounting()
    with patch.object(capi.service, "_client", lambda: _db()), \
         patch("vula.commerce.accounting.ensure_chart", acc.ensure_chart):
        with pytest.raises(capi.HTTPException) as e:
            await capi.admin_bulk_categorize(
                TENANT, capi.BulkCategorizeIn(
                    txn_ids=[f"x{i}" for i in range(capi._BULK_LIMIT + 1)],
                    account_code="fuel"))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_ids_from_another_tenant_are_not_touched():
    """The update is driven by rows re-read under this tenant_id, never by the raw id list."""
    updates = []
    acc = _accounting()
    with patch.object(capi.service, "_client", lambda: _db(updates=updates)), \
         patch("vula.commerce.accounting.ensure_chart", acc.ensure_chart), \
         patch("vula.commerce.accounting.is_vat_registered", acc.is_vat_registered), \
         patch("vula.commerce.accounting.vat_for", acc.vat_for), \
         patch("vula.commerce.accounting.learn_category_rule"):
        out = await capi.admin_bulk_categorize(
            TENANT, capi.BulkCategorizeIn(txn_ids=["t1", "someone-elses-row"],
                                          account_code="fuel"))
    assert out["updated"] == 1
    assert out["requested"] == 2


# ── bulk dismiss ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dismiss_takes_rows_out_of_the_queue():
    updates = []
    with patch.object(capi.service, "_client", lambda: _db(updates=updates)):
        out = await capi.admin_bulk_dismiss(TENANT, capi.BulkDismissIn(txn_ids=["t1", "t4"]))
    assert out["dismissed"] == 2
    assert updates == [{"match_status": "ignored"}], \
        "'ignored' is the status the per-transaction flow already uses"


@pytest.mark.asyncio
async def test_dismissing_nothing_is_an_error_not_a_silent_no_op():
    with patch.object(capi.service, "_client", lambda: _db()):
        with pytest.raises(capi.HTTPException) as e:
            await capi.admin_bulk_dismiss(TENANT, capi.BulkDismissIn(txn_ids=[]))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_dismissing_unknown_ids_reports_404_rather_than_claiming_success():
    with patch.object(capi.service, "_client", lambda: _db(rows=[])):
        with pytest.raises(capi.HTTPException) as e:
            await capi.admin_bulk_dismiss(TENANT, capi.BulkDismissIn(txn_ids=["nope"]))
    assert e.value.status_code == 404
