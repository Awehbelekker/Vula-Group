"""24 Nov 2026 audit: one client's names reached every business — Field Ops WhatsApp templates
named `digg_…`, project costs "paid by DIGG". Made-up data."""
import pytest

from tests.fake_supabase import FakeSupabase
from vula.api import field_ops
from vula.commerce import service


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({"commerce_wa_templates": [
        {"tenant_id": "builder-a", "name": "digg_task_assigned"},
        {"tenant_id": "builder-a", "name": "digg_task_chase"},
        {"tenant_id": "builder-a", "name": "field_task_chase"},      # the neutral one approved too
    ]})
    monkeypatch.setattr(service, "_client", lambda: d)
    field_ops._NAMES_CACHE.clear()
    yield d
    field_ops._NAMES_CACHE.clear()


def test_a_new_business_gets_the_neutral_names(db):
    for key, name in field_ops.FIELD_TEMPLATES.items():
        assert field_ops.field_template("builder-b", key) == name
        assert not name.startswith("digg")


def test_a_business_with_only_the_old_template_keeps_it(db):
    assert field_ops.field_template("builder-a", "task_assigned") == "digg_task_assigned"


def test_the_neutral_one_wins_once_it_is_on_record(db):
    assert field_ops.field_template("builder-a", "task_chase") == "field_task_chase"


def test_no_database_still_names_a_template(monkeypatch):
    def broken():
        raise RuntimeError("database down")
    monkeypatch.setattr(service, "_client", broken)
    field_ops._NAMES_CACHE.clear()
    assert field_ops.field_template("builder-c", "daily_tasks") == "field_daily_tasks"
    field_ops._NAMES_CACHE.clear()


def test_the_submission_script_matches_what_the_code_sends():
    import importlib.util
    import pathlib
    import re
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "create_launch_templates.py"
    spec = importlib.util.spec_from_file_location("launch_templates", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    script = {name: len(set(re.findall(r"\{\{(\d+)\}\}", body))) for name, body, *_ in mod.FIELDOPS}
    sent = {"field_task_assigned": 3, "field_task_chase": 2, "field_walkthrough_start": 2,
            "field_walkthrough_approved": 1, "field_walkthrough_rejected": 2, "field_daily_tasks": 2}
    assert script == sent
    assert set(script) == set(field_ops.FIELD_TEMPLATES.values())
