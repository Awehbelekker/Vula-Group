"""core/mass_mind/patterns.py — Mass Mind Phase 1 pattern library (migration 162).

rollup() turns fenced, per-tenant reflections (migration 159) into an anonymized
(business_type, skill, model_tier) -> win rate table with NO tenant identifier at all — the
deliberate aggregation the Mass Mind design doc calls for. suggest_tier() is the read side,
consulted by core/hrm/orchestrator.py::_select_model only as a cold-start fallback, never
overriding a tenant's own signal (covered separately in tests/test_orchestrator.py).
"""
import pytest

from core.mass_mind import patterns


# ── fake Supabase client ─────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self._filtered = list(rows)
        self._limit_n = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self._filtered = [r for r in self._filtered if r.get(key) == val]
        return self

    def gte(self, key, val):
        self._filtered = [r for r in self._filtered if (r.get(key) if r.get(key) is not None
                                                          else "") >= val]
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def execute(self):
        rows = self._filtered[: self._limit_n] if self._limit_n is not None else self._filtered
        return _FakeResult(rows)


class _FakeUpsert:
    def __init__(self, table, row, on_conflict):
        self._table = table
        self._row = dict(row)
        self._keys = tuple(on_conflict.split(","))

    def execute(self):
        match = next((r for r in self._table
                      if all(r.get(k) == self._row.get(k) for k in self._keys)), None)
        if match is not None:
            match.update(self._row)
        else:
            self._table.append(self._row)
        return _FakeResult([self._row])


class _FakeTable:
    def __init__(self, store):
        self._store = store

    def select(self, *a, **kw):
        return _FakeQuery(self._store).select(*a, **kw)

    def upsert(self, row, on_conflict):
        return _FakeUpsert(self._store, row, on_conflict)


class _FakeClient:
    def __init__(self):
        self._tables: dict[str, list] = {}

    def table(self, name):
        return _FakeTable(self._tables.setdefault(name, []))


class _BrokenClient:
    def table(self, name):
        raise Exception(f'relation "{name}" does not exist')


def _reflection_row(tenant_id, skill, tier, score):
    return {"tenant_id": tenant_id, "primary_skill": skill, "winning_tier": tier,
            "outcome_score": score, "created_at": "2026-09-15T00:00:00+00:00"}


def _business_types(monkeypatch, mapping: dict):
    """Patch vula.api.tenants.get_config so each tenant resolves to a fixed business_type."""
    def _fake_get_config(tenant_id, fresh=False):
        return {"business_type": mapping.get(tenant_id, "other")}
    monkeypatch.setattr("vula.api.tenants.get_config", _fake_get_config)


# ── rollup ────────────────────────────────────────────────────────────────────────────

def test_rollup_writes_a_row_once_min_sample_is_reached(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {"oth1": "food", "oth2": "food", "oth3": "food"})
    fake._tables["vula_reflections"] = [
        _reflection_row("oth1", "commerce_assistant", "7b", 0.9),
        _reflection_row("oth2", "commerce_assistant", "7b", 0.8),
        _reflection_row("oth3", "commerce_assistant", "7b", 0.85),
        _reflection_row("oth1", "commerce_assistant", "7b", 0.7),
        _reflection_row("oth2", "commerce_assistant", "7b", 0.95),
    ]
    written = patterns.rollup(min_sample=5)
    assert written == 1
    rows = fake._tables["vula_mass_mind_patterns"]
    assert len(rows) == 1
    assert rows[0]["business_type"] == "food"
    assert rows[0]["skill"] == "commerce_assistant"
    assert rows[0]["model_tier"] == "7b"
    assert rows[0]["sample_count"] == 5
    assert rows[0]["win_rate"] == 1.0  # all 5 scores > 0.6


def test_rollup_skips_groups_below_min_sample(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {"oth1": "food"})
    fake._tables["vula_reflections"] = [
        _reflection_row("oth1", "commerce_assistant", "7b", 0.9),
        _reflection_row("oth1", "commerce_assistant", "7b", 0.8),
    ]
    assert patterns.rollup(min_sample=5) == 0
    assert fake._tables.get("vula_mass_mind_patterns", []) == []


def test_rollup_never_writes_a_tenant_identifier(monkeypatch):
    """The whole point of this table — confirm no row carries tenant_id anywhere."""
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {f"t{i}": "retail" for i in range(6)})
    fake._tables["vula_reflections"] = [
        _reflection_row(f"t{i}", "finance_admin", "14b", 0.7) for i in range(6)
    ]
    patterns.rollup(min_sample=5)
    row = fake._tables["vula_mass_mind_patterns"][0]
    assert "tenant_id" not in row
    assert "tenant_ids" not in row


def test_rollup_separates_by_business_type(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {"oth": "food", "digg": "services"})
    fake._tables["vula_reflections"] = (
        [_reflection_row("oth", "commerce_assistant", "7b", 0.9)] * 5
        + [_reflection_row("digg", "commerce_assistant", "7b", 0.3)] * 5  # different outcome
    )
    written = patterns.rollup(min_sample=5)
    assert written == 2
    rows = fake._tables["vula_mass_mind_patterns"]
    food_row = next(r for r in rows if r["business_type"] == "food")
    services_row = next(r for r in rows if r["business_type"] == "services")
    assert food_row["win_rate"] == 1.0
    assert services_row["win_rate"] == 0.0


def test_rollup_skips_rows_with_missing_skill_or_tier(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {"oth": "food"})
    fake._tables["vula_reflections"] = [
        {"tenant_id": "oth", "primary_skill": None, "winning_tier": "7b", "outcome_score": 0.9},
        {"tenant_id": "oth", "primary_skill": "commerce_assistant", "winning_tier": None,
         "outcome_score": 0.9},
        {"tenant_id": "oth", "primary_skill": "commerce_assistant", "winning_tier": "7b",
         "outcome_score": None},
    ]
    assert patterns.rollup(min_sample=1) == 0


def test_rollup_re_running_upserts_not_duplicates(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {"oth": "food"})
    fake._tables["vula_reflections"] = [
        _reflection_row("oth", "commerce_assistant", "7b", 0.9) for _ in range(5)
    ]
    patterns.rollup(min_sample=5)
    patterns.rollup(min_sample=5)  # a second tick
    assert len(fake._tables["vula_mass_mind_patterns"]) == 1


def test_rollup_fails_open_when_reflections_table_is_missing(monkeypatch):
    monkeypatch.setattr(patterns, "_client", lambda: _BrokenClient())
    assert patterns.rollup() == 0


def test_rollup_fails_open_when_the_pattern_table_write_fails(monkeypatch):
    """Reads succeed, writes fail (migration 162 not yet applied) — must not raise, and other
    groups in the same rollup pass must still get their chance."""
    class _ReadOnlyClient:
        def table(self, name):
            if name == "vula_reflections":
                return _FakeTable(rows)
            raise Exception('relation "vula_mass_mind_patterns" does not exist')

    rows = [_reflection_row("oth", "commerce_assistant", "7b", 0.9) for _ in range(5)]
    monkeypatch.setattr(patterns, "_client", lambda: _ReadOnlyClient())
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False:
                        {"business_type": "food"})
    assert patterns.rollup(min_sample=5) == 0  # write failed, so nothing counted as written


# ── suggest_tier ──────────────────────────────────────────────────────────────────────

def test_suggest_tier_returns_the_best_performing_tier(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {"new-tenant": "food"})
    fake._tables["vula_mass_mind_patterns"] = [
        {"business_type": "food", "skill": "commerce_assistant", "model_tier": "7b",
         "win_rate": 0.6, "avg_score": 0.7, "sample_count": 10},
        {"business_type": "food", "skill": "commerce_assistant", "model_tier": "14b",
         "win_rate": 0.9, "avg_score": 0.85, "sample_count": 12},
    ]
    assert patterns.suggest_tier("new-tenant", "commerce_assistant") == "14b"


def test_suggest_tier_ignores_rows_below_min_sample(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {"new-tenant": "food"})
    fake._tables["vula_mass_mind_patterns"] = [
        {"business_type": "food", "skill": "commerce_assistant", "model_tier": "14b",
         "win_rate": 1.0, "avg_score": 1.0, "sample_count": 2},  # too few samples
    ]
    assert patterns.suggest_tier("new-tenant", "commerce_assistant", min_sample=5) is None


def test_suggest_tier_none_when_no_matching_business_type_or_skill(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(patterns, "_client", lambda: fake)
    _business_types(monkeypatch, {"new-tenant": "trades"})  # no "trades" rows below
    fake._tables["vula_mass_mind_patterns"] = [
        {"business_type": "food", "skill": "commerce_assistant", "model_tier": "14b",
         "win_rate": 1.0, "avg_score": 1.0, "sample_count": 10},
    ]
    assert patterns.suggest_tier("new-tenant", "commerce_assistant") is None


def test_suggest_tier_fails_open_when_the_table_is_missing(monkeypatch):
    monkeypatch.setattr(patterns, "_client", lambda: _BrokenClient())
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False:
                        {"business_type": "food"})
    assert patterns.suggest_tier("off-the-hook", "commerce_assistant") is None


def test_suggest_tier_fails_open_when_tenant_config_lookup_fails(monkeypatch):
    monkeypatch.setattr(patterns, "_client", lambda: _FakeClient())

    def _raise(tid, fresh=False):
        raise RuntimeError("tenant lookup broke")
    monkeypatch.setattr("vula.api.tenants.get_config", _raise)
    assert patterns.suggest_tier("off-the-hook", "commerce_assistant") is None
