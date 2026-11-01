"""The suite can never write to a real Supabase (see tests/conftest.py)."""
from config import settings


def test_supabase_is_off_under_pytest():
    assert settings.supabase_url == ""
    assert settings.supabase_service_key == ""


def test_durable_writes_go_nowhere():
    from vula import durable
    assert durable.upsert("vula_takeoff_jobs", {"id": "never"}) is False
    assert durable.select("vula_takeoff_jobs") is None
