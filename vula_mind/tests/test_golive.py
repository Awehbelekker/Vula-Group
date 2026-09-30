"""Go-live test per tenant: cases fit the business type; readiness at 85%."""
import asyncio
from unittest.mock import patch

from evals import golive


def _prof(**kw):
    base = dict(sells_products=False, is_rep_business=False, uses_projects=False, business_type="other")
    return {**base, **kw}


def test_cases_fit_the_business():
    with patch("vula.api.tenants.tenant_profile", lambda t: _prof(sells_products=True, business_type="food")):
        ids = {c["id"] for c in golive.cases_for("oth")}
    assert "golive-cust-how-order" in ids and "golive-rep-contact" not in ids
    with patch("vula.api.tenants.tenant_profile", lambda t: _prof(is_rep_business=True, business_type="rep")):
        cases = golive.cases_for("gerflor")
    assert {"golive-rep-contact", "golive-no-delete-all"} <= {c["id"] for c in cases}
    assert all(c["tenant"] == "gerflor" and c["route_mode"] == "knowledge" for c in cases)


def test_readiness_threshold():
    async def fake(case, judge):
        return {"id": case["id"], "component": case["component"], "ok": case["id"] != "golive-hours"}
    with patch("vula.api.tenants.tenant_profile", lambda t: _prof()), \
         patch("evals.benchmark.run_agent_case", fake):
        rep = asyncio.run(golive.run("x"))
    assert rep["total"] == len(golive._OWNER) and rep["passed"] == rep["total"] - 1
    assert rep["ready"] is (rep["pass_pct"] >= golive.PASS_PCT)
