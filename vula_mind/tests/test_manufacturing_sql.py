"""Migration 211's functions against a real Postgres (Manufacturing Core phase 2–3): a hand-costed
basin batch must come out to the cent. Runs when VULA_TEST_PG ("host:port", psql on PATH, a
superuser that can create databases) is set — skipped otherwise (CI has no Postgres)."""
import os
import pathlib
import shutil
import subprocess

import pytest

PG = os.environ.get("VULA_TEST_PG")
pytestmark = pytest.mark.skipif(not PG or not shutil.which("psql"), reason="no local Postgres (set VULA_TEST_PG)")
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _psql(*args, db="postgres"):
    host, port = PG.split(":")
    return subprocess.run(["psql", "-h", host, "-p", port, "-U", "postgres", "-v", "ON_ERROR_STOP=1",
                           "-qtA", "-d", db, *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture(scope="module")
def db():
    _psql("-c", "drop database if exists vula_mfg_test", "-c", "create database vula_mfg_test")
    _psql("-f", str(ROOT / "tests/sql/mfg_base.sql"), db="vula_mfg_test")
    for _ in range(2):                                   # idempotent
        _psql("-f", str(ROOT / "migrations/211_manufacturing.sql"), db="vula_mfg_test")
    out = _psql("-f", str(ROOT / "tests/sql/mfg_scenario.sql"), db="vula_mfg_test")
    yield out.splitlines()
    _psql("-c", "drop database if exists vula_mfg_test")


def test_hand_costed_basin_batch_to_the_cent(db):
    assert "0.003867" in db                       # weighted average of R95 and R100 bags
    assert "1:archived,2:active" in db            # a new recipe version archives the old one
    confirmed = next(line for line in db if line.startswith("{\"good_qty\""))
    assert '"materials_cents": 52035' in confirmed and '"total_cents": 103035' in confirmed
    assert '"unit_cost": 343.450000' in confirmed
    assert "3.0000|343.450000|34345" in db        # 3 basins in stock at R343.45 each


def test_confirm_once_own_tenant_only_and_reversal_restores(db):
    assert "already_confirmed" in db and "not_found" in db
    assert "t" in db                               # another tenant's material move refused (NULL)
    assert "Concrete mix|75000.0000|0.003867" in db and "Pigment|1000.0000|0.220000" in db
    assert db[-1] == "reversed"
