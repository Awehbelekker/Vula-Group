"""Tests for the per-tenant LLM spend cap metering helpers (vula/integrations/metering.py,
migration 166) — go-live readiness pass, Phase 1.1. Opt-in only: no cap set means uncapped,
and every read fails open (never blocks generation over a metering hiccup)."""
from unittest.mock import MagicMock, patch

from vula.integrations import metering


def _mock_client(usage_rows=None, config_rows=None):
    db = MagicMock()

    def table(name):
        m = MagicMock()
        if name == "vula_ai_usage":
            m.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                data=usage_rows or []
            )
        elif name == "vula_tenant_config":
            m.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
                data=config_rows or []
            )
        return m

    db.table.side_effect = table
    return db


def test_today_spend_sums_across_models():
    rows = [{"est_cost_usd": 0.5}, {"est_cost_usd": 1.25}]
    with patch("vula.integrations.metering._client", return_value=_mock_client(usage_rows=rows)):
        assert metering.today_spend("digg-demo") == 1.75


def test_today_spend_empty_tenant_id_returns_zero_without_db_call():
    with patch("vula.integrations.metering._client") as mock_client:
        assert metering.today_spend("") == 0.0
    mock_client.assert_not_called()


def test_today_spend_fails_open_on_db_error():
    with patch("vula.integrations.metering._client", side_effect=RuntimeError("db down")):
        assert metering.today_spend("digg-demo") == 0.0


def test_spend_cap_usd_returns_none_when_unset():
    with patch(
        "vula.integrations.metering._client",
        return_value=_mock_client(config_rows=[{"spend_cap_usd": None}]),
    ):
        assert metering.spend_cap_usd("digg-demo") is None


def test_spend_cap_usd_returns_none_when_no_row():
    with patch("vula.integrations.metering._client", return_value=_mock_client(config_rows=[])):
        assert metering.spend_cap_usd("digg-demo") is None


def test_spend_cap_usd_returns_configured_value():
    with patch(
        "vula.integrations.metering._client",
        return_value=_mock_client(config_rows=[{"spend_cap_usd": 5.0}]),
    ):
        assert metering.spend_cap_usd("digg-demo") == 5.0


def test_spend_cap_usd_fails_open_on_db_error():
    with patch("vula.integrations.metering._client", side_effect=RuntimeError("db down")):
        assert metering.spend_cap_usd("digg-demo") is None


def test_is_over_spend_cap_false_when_no_cap_set():
    with patch("vula.integrations.metering.spend_cap_usd", return_value=None):
        assert metering.is_over_spend_cap("digg-demo") is False


def test_is_over_spend_cap_true_at_or_above_cap():
    with (
        patch("vula.integrations.metering.spend_cap_usd", return_value=2.0),
        patch("vula.integrations.metering.today_spend", return_value=2.0),
    ):
        assert metering.is_over_spend_cap("digg-demo") is True


def test_is_over_spend_cap_false_below_cap():
    with (
        patch("vula.integrations.metering.spend_cap_usd", return_value=2.0),
        patch("vula.integrations.metering.today_spend", return_value=1.0),
    ):
        assert metering.is_over_spend_cap("digg-demo") is False


def test_get_request_tenant_reads_contextvar():
    metering.set_request_tenant("digg-demo")
    try:
        assert metering.get_request_tenant() == "digg-demo"
    finally:
        metering.set_request_tenant(None)
