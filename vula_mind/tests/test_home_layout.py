"""Home cards each business chooses (2026-09-29, Ian: tenants had no way to "customise what
they want to view"). Home was built for online orders, so DIGG — a project business — saw mostly
zeros. Default by business type; a saved layout is cleaned of unknown ids; [] resets."""
import pytest
from fastapi import HTTPException

from vula.api import tenants

CFG = {
    "digg-demo": {"tenant_id": "digg-demo", "modules": ["projects", "commerce"]},
    "off-the-hook": {"tenant_id": "off-the-hook", "modules": ["commerce"]},
}


class _Q:
    def __init__(self, saved):
        self.saved, self.patch = saved, None

    def update(self, patch):
        self.patch = patch
        return self

    def eq(self, col, val):
        self.tid = val
        return self

    def execute(self):
        CFG[self.tid] = {**CFG[self.tid], **self.patch}
        self.saved.append((self.tid, self.patch))


class _DB:
    def __init__(self):
        self.saved = []

    def table(self, name):
        assert name == "vula_tenant_config"
        return _Q(self.saved)


@pytest.fixture()
def db(monkeypatch):
    fake = _DB()
    monkeypatch.setattr(tenants, "_client", lambda: fake)
    monkeypatch.setattr(tenants, "get_config", lambda tid, fresh=False: CFG.get(tid, {}))
    monkeypatch.setattr(tenants, "_effective_modules", lambda cfg: cfg.get("modules") or [])
    return fake


@pytest.mark.asyncio
async def test_default_home_follows_the_business(db):
    digg = await tenants.get_home_layout("digg-demo")
    oth = await tenants.get_home_layout("off-the-hook")
    assert "jobcosting" in digg["cards"] and "sales" not in digg["cards"] and not digg["custom"]
    assert "sales" in oth["cards"] and "jobcosting" not in oth["cards"]
    assert set(digg["available"]) >= set(digg["cards"]) | set(oth["cards"])


@pytest.mark.asyncio
async def test_a_saved_home_is_cleaned_ordered_and_resettable(db):
    out = await tenants.put_home_layout(
        "off-the-hook", tenants.HomeLayoutIn(cards=["crosscheck", "sales", "nonsense", "sales", 5]))
    assert out["cards"] == ["crosscheck", "sales"] and out["custom"]
    assert CFG["off-the-hook"]["home_layout"] == {"cards": ["crosscheck", "sales"]}
    reset = await tenants.put_home_layout("off-the-hook", tenants.HomeLayoutIn(cards=[]))
    assert CFG["off-the-hook"]["home_layout"] is None and not reset["custom"]
    assert reset["cards"] == reset["default"]


@pytest.mark.asyncio
async def test_saving_before_migration_186_says_so(monkeypatch):
    class Broken:
        def table(self, name):
            raise RuntimeError('column "home_layout" does not exist')
    monkeypatch.setattr(tenants, "_client", lambda: Broken())
    with pytest.raises(HTTPException) as e:
        await tenants.put_home_layout("digg-demo", tenants.HomeLayoutIn(cards=["checklist"]))
    assert e.value.status_code == 503 and "186" in e.value.detail
