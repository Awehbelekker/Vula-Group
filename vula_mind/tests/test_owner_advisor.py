"""Weekly owner advisor: only real figures, nothing sent when there's nothing to say, off by default."""
import asyncio
from datetime import date

from config import settings
from vula import owner_advisor as oa


def test_render_uses_only_the_given_figures():
    d = {"name": "Off the Hook", "overdue": {"count": 2, "total_cents": 1_250_000, "top": [
            {"who": "Sea Point Deli", "cents": 900_000, "days": 12, "ref": "INV-104"},
            {"who": "Bob", "cents": 350_000, "days": 3, "ref": None}]},
         "spending": [{"category": "fuel", "now_cents": 900_000, "usual_cents": 500_000, "pct": 80}],
         "low_stock": [{"name": "Hake fillets", "qty": 2, "at": 5}],
         "unanswered": {"count": 3, "unanswered": 1, "examples": ["Do you deliver to Paarl?"]},
         "waiting_docs": 4, "profile_gaps": 2}
    text = oa.render(d)
    for s in ("Off the Hook", "R12,500", "Sea Point Deli", "12 days overdue", "INV-104", "fuel",
              "+80%", "Hake fillets: 2 left", "Paarl", "4 document(s)", "2 unanswered question(s)"):
        assert s in text


def test_nothing_to_say_sends_nothing():
    assert oa.render({"name": "X", "overdue": {}, "spending": [], "low_stock": [], "unanswered": {},
                      "waiting_docs": 0, "profile_gaps": 0}) is None


def test_spending_jump_needs_history_and_size(monkeypatch):
    rows = ([{"txn_date": "2026-09-20", "amount_cents": -900_000, "account_code": "fuel"}]
            + [{"txn_date": d, "amount_cents": -500_000, "account_code": "fuel"}
               for d in ("2026-06-15", "2026-07-15", "2026-08-15")]
            + [{"txn_date": "2026-09-21", "amount_cents": -30_000, "account_code": "bank_fees"},
               {"txn_date": "2026-07-01", "amount_cents": -10_000, "account_code": "bank_fees"}])

    class _Q:
        def __getattr__(self, n): return lambda *a, **k: self
        def execute(self):
            class R: data = rows
            return R()

    monkeypatch.setattr(oa, "_client", lambda: type("C", (), {"table": lambda s, n: _Q()})())
    out = oa.spending_jumps("t", date(2026, 9, 30))
    assert [x["category"] for x in out] == ["fuel"] and out[0]["pct"] == 80   # bank fees too small


def test_off_by_default(monkeypatch):
    monkeypatch.setattr(settings, "owner_advisor_enabled", False)
    assert asyncio.run(oa.send_all())["sent"] == 0
