"""Customer-facing fixed messages use the tenant's own name — never Off the Hook's."""
import asyncio
from unittest.mock import AsyncMock, patch

from vula.commerce import onboarding


def _capture(state, text, tenant="bakery-co"):
    with patch.object(onboarding, "active_capture", return_value={"onboarding_state": state}), \
         patch.object(onboarding, "_update"), \
         patch("vula.api.tenants.get_config", return_value={"display_name": "Bread & Co"}), \
         patch("vula.api.commerce.record_inbound_consent", create=True):
        return asyncio.run(onboarding.handle_capture(tenant, "27820000001", text))


def test_optin_confirmation_names_the_tenant():
    reply = _capture("awaiting_reply", "hi")
    assert "Bread & Co" in reply and "Off the Hook" not in reply


def test_welcome_names_the_tenant():
    reply = _capture("confirming_optin", "yes please")
    assert "Welcome to *Bread & Co*" in reply


def test_supplier_intake_link_is_the_tenants_own():
    from vula.api import whatsapp as wa
    sent = AsyncMock(return_value=True)

    def run(tenant, cfg):
        sent.reset_mock()
        with patch.object(wa, "_send_reply", new=sent), \
             patch("vula.api.tenants.get_config", return_value=cfg), \
             patch("vula.commerce.service._client", side_effect=RuntimeError("no db")):
            src = wa._handle_commerce_message
            # Drive only the supplier-intake branch: everything before it is patched to fall through.
            with patch.object(wa, "_is_tenant_owner", return_value=False):
                try:
                    asyncio.run(src("27820000002", "I can supply you with linefish", "m1", tenant))
                except Exception:
                    pass
        texts = [c.args[1] for c in sent.call_args_list if len(c.args) > 1]
        return next((t for t in texts if "suppliers" in t), "")

    other = run("bakery-co", {"display_name": "Bread & Co"})
    assert other and "offthehook" not in other
    oth = run("off-the-hook", {"display_name": "Off the Hook"})
    assert "https://offthehook.co.za/suppliers" in oth
    own = run("bakery-co", {"display_name": "Bread & Co", "supplier_intake_url": "https://bread.co/supply"})
    assert "https://bread.co/supply" in own
