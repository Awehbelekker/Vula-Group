"""Bar tabs: open with a tap (WhatsApp proves the number), staff add drinks, the customer pays their
own tab. Other customers never see or get asked about someone else's tab. Made-up names/numbers."""
import re
from datetime import timedelta

import pytest

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.tap.service import REF_PREFIX, TapConfig, TapService
from vula.tap.tabs import TabError

T = "bar-a"
SAM, LEE = "+27 82 111 4482", "+27 83 222 9999"


@pytest.fixture
def env():
    clock = Clock()
    repo = MemoryRepo(clock)
    msg, gw = FakeMessenger(), FakeGateway()
    svc = TapService(repo, msg, gw, TapConfig(pepper="pep", public_base_url="https://api.test",
                                              encrypt=lambda s: "enc:" + s, decrypt=lambda s: s[4:], clock=clock))
    tag = repo.add_tag(T, "bar-1", bound_id="coach")
    repo.team[(T, "coach")] = ["27800000001"]
    repo.team[(T, None)] = ["27800000001"]
    repo.upsert_settings(T, {"tabs_enabled": True})
    return svc, repo, msg, gw, tag, clock


async def open_tab(svc, tag, phone, name="Sam"):
    link = svc.tabs.open_link(tag)
    token = re.search(r"text=TAB%20(\S+)", link).group(1)
    assert await svc.handle_text(T, phone, f"TAB {token}", name) is True
    return svc.repo.tab_for_customer(T, svc.phone_hash(phone))


def last(msg, phone):
    return msg.to(phone, "text")[-1][3]


async def test_open_a_tab_with_a_tap(env):
    svc, repo, msg, gw, tag, _ = env
    tab = await open_tab(svc, tag, SAM)
    assert tab["bill_type"] == "tab" and tab["customer_name"] == "Sam" and tab["subtotal_cents"] == 0
    reply = last(msg, SAM)
    assert "Your tab at" in reply and "Sam" in reply and "can see your name and number" in reply
    assert "New tab: Sam (ending 482)" in last(msg, "27800000001")
    link = svc.tabs.open_link(tag)                       # opening again just shows the tab
    token = re.search(r"text=TAB%20(\S+)", link).group(1)
    await svc.handle_text(T, SAM, f"TAB {token}", "Sam")
    assert "already have a tab" in last(msg, SAM) and len(repo.open_tabs(T)) == 1


async def test_tabs_off_means_no_tab(env):
    svc, repo, msg, gw, tag, _ = env
    repo.upsert_settings(T, {"tabs_enabled": False})
    link = svc.tabs.open_link(tag)
    token = re.search(r"text=TAB%20(\S+)", link).group(1)
    await svc.handle_text(T, SAM, f"TAB {token}", "Sam")
    assert not repo.open_tabs(T) and "couldn't open a tab" in last(msg, SAM)
    assert await svc.handle_text(T, SAM, "my tab") is False      # not intercepted when off


async def test_staff_add_and_void_items_and_the_customer_can_check(env):
    svc, repo, msg, gw, tag, _ = env
    tab = await open_tab(svc, tag, SAM)
    v = svc.tabs.add_item(T, tab["id"], description="Castle", quantity=2, unit_cents=3500, by="m1")
    assert v["total_cents"] == 7000 and v["number"] == "ending 482" and "phone" not in v
    v = svc.tabs.add_item(T, tab["id"], description="Burger", quantity=1, unit_cents=9500, by="m1")
    burger = v["items"][-1]["id"]
    assert svc.tabs.void_item(T, tab["id"], burger)["total_cents"] == 7000
    await svc.handle_text(T, SAM, "my tab")
    assert "2 x Castle" in last(msg, SAM) and "R 70.00" in last(msg, SAM) and "Burger" not in last(msg, SAM)
    with pytest.raises(TabError):
        svc.tabs.add_item(T, tab["id"], description="", quantity=1, unit_cents=100, by="m1")
    with pytest.raises(TabError):
        svc.tabs.add_item(T, tab["id"], description="x", quantity=0, unit_cents=100, by="m1")


async def test_pay_tab_pays_the_items_total(env):
    svc, repo, msg, gw, tag, _ = env
    tab = await open_tab(svc, tag, SAM)
    svc.tabs.add_item(T, tab["id"], description="Castle", quantity=2, unit_cents=3500, by="m1")
    repo.bills[tab["id"]]["subtotal_cents"] = 1                  # a stale display total never decides
    await svc.handle_text(T, SAM, "PAY TAB")
    s = next(iter(repo.sessions.values()))
    assert s["bill_cents"] == 7000 and repo.bills[tab["id"]]["status"] == "claimed"
    with pytest.raises(TabError, match="paying this tab"):
        svc.tabs.add_item(T, tab["id"], description="Tequila", quantity=1, unit_cents=4000, by="m1")
    await svc.handle_interactive(T, SAM, f"kb:tip:{s['id']}:10")
    await svc.handle_interactive(T, SAM, f"kb:pay:{s['id']}")
    gw.itn = {"reference": REF_PREFIX + s["id"], "paid": True, "amount_cents": 7700, "pf_payment_id": "P",
              "fee_cents": 0}
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"
    assert repo.bills[tab["id"]]["status"] == "paid" and not repo.open_tabs(T)


async def test_tapping_the_tag_pays_your_own_tab_and_never_someone_elses(env):
    svc, repo, msg, gw, tag, _ = env
    sam_tab = await open_tab(svc, tag, SAM)
    svc.tabs.add_item(T, sam_tab["id"], description="Castle", quantity=1, unit_cents=3500, by="m1")
    url = svc.tap("bar-1")                                       # Lee taps: no tab of his own
    token = re.search(r"text=PAY%20(\S+)", url).group(1)
    await svc.handle_text(T, LEE, f"PAY {token}")
    assert "no open bill" in last(msg, LEE).lower()             # not a code prompt, not Sam's tab
    url = svc.tap("bar-1")                                       # Sam taps: his own tab
    token = re.search(r"text=PAY%20(\S+)", url).group(1)
    await svc.handle_text(T, SAM, f"PAY {token}")
    assert repo.bills[sam_tab["id"]]["status"] == "claimed"


async def test_empty_tab_cannot_be_paid_and_can_be_closed(env):
    svc, repo, msg, gw, tag, _ = env
    tab = await open_tab(svc, tag, SAM)
    await svc.handle_text(T, SAM, "pay my tab")
    assert "empty" in last(msg, SAM) and not repo.sessions
    assert svc.tabs.close_empty(T, tab["id"]) is True
    tab2 = await open_tab(svc, tag, LEE, "Lee")
    svc.tabs.add_item(T, tab2["id"], description="Castle", quantity=1, unit_cents=3500, by="m1")
    with pytest.raises(TabError, match="has items"):
        svc.tabs.close_empty(T, tab2["id"])


async def test_owner_sees_the_number_staff_see_it_masked(env):
    svc, repo, msg, gw, tag, _ = env
    await open_tab(svc, tag, SAM)
    assert svc.tabs.list_open(T, reveal=True)[0]["phone"] == "27821114482"
    assert "phone" not in svc.tabs.list_open(T, reveal=False)[0]


async def test_idle_tab_gets_one_prompt_and_the_owner_is_told(env):
    svc, repo, msg, gw, tag, clock = env
    tab = await open_tab(svc, tag, SAM)
    svc.tabs.add_item(T, tab["id"], description="Castle", quantity=2, unit_cents=3500, by="m1")
    clock.t += timedelta(hours=2)
    assert await svc.tabs.sweep_idle(T) == 0
    clock.t += timedelta(hours=2)
    assert await svc.tabs.sweep_idle(T) == 1
    assert "still open" in last(msg, SAM) and "PAY TAB" in last(msg, SAM)
    assert "Tab still open: Sam (ending 482), R 70.00" in last(msg, "27800000001")
    assert await svc.tabs.sweep_idle(T) == 0                     # once per tab
    svc.tabs.add_item(T, tab["id"], description="Water", quantity=1, unit_cents=1500, by="m1")
    assert repo.bills[tab["id"]]["tab_nudged_at"] is None        # a new order resets it


async def test_tap_page_offers_open_and_pay_tab(env):
    svc, repo, msg, gw, tag, _ = env
    svc.cfg.enabled = lambda t: True
    v = svc.express.view("bar-1", "browser")
    assert v["kind"] == "tabs" and "text=TAB%20" in v["open_tab_url"] and "text=PAY%20" in v["pay_tab_url"]
    from vula.tap import express_page
    page = express_page.render("bar-1", v)
    assert "Open a tab" in page and "Pay my tab" in page
    repo.upsert_settings(T, {"tabs_enabled": False})
    assert svc.express.view("bar-1", "browser")["kind"] == "no_bill"
