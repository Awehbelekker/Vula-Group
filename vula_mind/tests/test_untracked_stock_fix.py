"""Checkout of an untracked product (stock_quantity NULL) must not fail (production, 2026-09-27).

reserve_product_stock set in_stock = (NULL - qty > 0) = NULL on a NOT NULL column, so every
checkout of an untracked product raised 23502 — all 45 Off the Hook products were untracked.
The SQL itself is exercised against Postgres when the migration is applied; this guards the
migration so no stock function regresses to writing a NULL in_stock or tracking an untracked row.
"""
import pathlib
import re

SQL = (pathlib.Path(__file__).resolve().parents[1] / "migrations" / "181_untracked_stock_fix.sql").read_text()


def _body(name):
    m = re.search(rf"FUNCTION {name}\(.*?\$\$(.*?)\$\$", SQL, re.S)
    assert m, f"{name} missing from migration 181"
    return m.group(1)


def test_every_stock_function_leaves_untracked_rows_untracked():
    for fn in ("reserve_product_stock", "reserve_variant_stock",
               "decrement_product_stock", "decrement_variant_stock"):
        body = _body(fn)
        assert "WHEN stock_quantity IS NULL THEN NULL" in body, fn
        assert "WHEN stock_quantity IS NULL THEN in_stock" in body, fn
        assert "coalesce(stock_quantity, 0)" not in body.lower(), fn


def test_reservations_still_refuse_to_oversell_tracked_rows():
    for fn in ("reserve_product_stock", "reserve_variant_stock"):
        assert "stock_quantity IS NULL OR stock_quantity >= p_qty" in _body(fn)
