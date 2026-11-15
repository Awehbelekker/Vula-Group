"""Spreadsheets out of the chat (13 Nov 2026): a reply's own tables as Excel, exactly as shown
(vula/answer_sheet.py), only from the caller's own chat; and a sheet custom_sheet made comes back
to the dashboard as a file. Made-up names and figures."""
import io
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl
import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula import answer_sheet as A, identity
from vula.api import commerce
from vula.commerce import service

T = "kelp-boardbags"
ANSWER = """## Board bag prices
| Supplier | Product | Price | Delivery |
|---|---|---:|---|
| **Surf Shop** | 9'6 day bag | R 1 450 [1] | 3 days |
| Bags Direct | 9'6 travel bag | R1 390.50 | free over R 1 000 |
| Call | 082 555 1234 | 12 | - |

### Next steps
| Step | Who |
|---|---|
| Ask for a bulk quote | Ian |

Sources:
[1] Surf Shop — https://shop.example/bags
[2] Bags Direct — https://bags.example/prices
"""


def _book(data):
    return openpyxl.load_workbook(io.BytesIO(data))


def test_tables_and_sources_are_found_with_their_headings():
    ts = A.tables(ANSWER)
    assert [t["title"] for t in ts] == ["Board bag prices", "Next steps"]
    assert ts[0]["header"] == ["Supplier", "Product", "Price", "Delivery"] and ts[0]["rows"][0][0] == "Surf Shop"
    assert A.sources(ANSWER) == [{"n": "1", "title": "Surf Shop", "url": "https://shop.example/bags"},
                                 {"n": "2", "title": "Bags Direct", "url": "https://bags.example/prices"}]


@pytest.mark.parametrize("cell,expected,fmt", [
    ("R 1 450", 1450.0, "R"), ("R1 390.50", 1390.5, "R"), ("-R 200", -200.0, "R"), ("R 1 450 [1]", 1450.0, "R"),
    ("1 450", 1450, "#"), ("12", 12, "#"), ("3.5", 3.5, "#"),
    ("082 555 1234", "082 555 1234", None), ("0821", "0821", None), ("free over R 1 000", "free over R 1 000", None),
    ("12%", "12%", None), ("3 days", "3 days", None),
])
def test_only_plain_amounts_and_numbers_become_numbers(cell, expected, fmt):
    v, f, _note = A.value(cell)
    assert v == expected
    assert (f or "")[:1] == (fmt or "")


def test_the_workbook_holds_the_answer_as_written_and_reads_back():
    data, want = A.workbook("Kelp Gear", "Board bags", ANSWER, "#2C5545")
    wb = _book(data)
    assert wb.sheetnames == ["Board bag prices", "Next steps", "Sources"]
    ws = wb["Board bag prices"]
    assert ws["A1"].value == "Kelp Gear — Board bags" and ws["A4"].value == "Supplier"
    assert ws["C5"].value == 1450 and ws["C5"].comment.text.startswith("Source [1]")
    assert ws["B7"].value == "082 555 1234" and ws["D6"].value == "free over R 1 000"
    assert wb["Sources"]["C5"].hyperlink.target == "https://shop.example/bags"
    assert A.read_back(data, want) == []
    want["Board bag prices!C5"] = 1500.0                       # a changed figure is caught
    assert A.read_back(data, want)


def test_an_answer_without_a_table_or_sources_says_how_to_get_one():
    with pytest.raises(A.NoTable, match="as a table"):
        A.workbook("Kelp Gear", "x", "Just a sentence.")
    data, _ = A.workbook("Kelp Gear", "x", "Oil it yearly [1].\n\nSources:\n[1] Deck care — https://d.example/")
    assert _book(data).sheetnames == ["Answer", "Sources"]


# ── the route: only a reply in the caller's own chat ────────────────────────────────────────

def _db():
    return FakeSupabase({
        "commerce_conversation_sessions": [
            {"id": "s-me", "tenant_id": T, "session_key": "webadmin:m:m1"},
            {"id": "s-sam", "tenant_id": T, "session_key": "webadmin:m:m2"},
            {"id": "s-else", "tenant_id": "other", "session_key": "webadmin:m:m1"}],
        "commerce_conversation_messages": [
            {"id": "mine", "tenant_id": T, "session_id": "s-me", "role": "assistant", "content": ANSWER},
            {"id": "asked", "tenant_id": T, "session_id": "s-me", "role": "user", "content": ANSWER},
            {"id": "sams", "tenant_id": T, "session_id": "s-sam", "role": "assistant", "content": ANSWER},
            {"id": "elsewhere", "tenant_id": "other", "session_id": "s-else", "role": "assistant", "content": ANSWER},
            {"id": "plain", "tenant_id": T, "session_id": "s-me", "role": "assistant", "content": "No table here."}],
    })


async def _sheet(message_id):
    req = MagicMock()
    req.headers = {"authorization": "Bearer tok"}
    with patch.object(service, "_client", return_value=_db()), \
         patch.object(service, "_brand_accent", AsyncMock(return_value=None)), \
         patch("vula.api.tenants.display_name", lambda t: "Kelp Gear"), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=identity.Person(T, member_id="m1"))):
        return await commerce.admin_assistant_sheet(T, commerce.AnswerSheet(message_id=message_id), req)


@pytest.mark.asyncio
async def test_your_own_reply_comes_back_as_an_xlsx():
    r = await _sheet("mine")
    assert r.media_type.endswith("spreadsheetml.sheet")
    assert 'filename="Board_bag_prices_' in r.headers["content-disposition"]
    assert _book(r.body)["Board bag prices"]["C5"].value == 1450


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", ["sams", "elsewhere", "asked", "nope"])
async def test_anyone_elses_reply_or_another_businesss_is_404(message_id):
    with pytest.raises(HTTPException) as e:
        await _sheet(message_id)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_a_reply_without_a_table_is_a_plain_400():
    with pytest.raises(HTTPException) as e:
        await _sheet("plain")
    assert e.value.status_code == 400 and "as a table" in e.value.detail


# ── a sheet custom_sheet made comes back to the dashboard as a file ──────────────────────────

@pytest.mark.asyncio
async def test_the_dashboard_reply_carries_the_files_a_tool_made():
    from core.skills.base import SkillOutput

    class _Admin:
        async def __call__(self, inp):
            inp.metadata["files_out"].append({"name": "Price_list.xlsx", "url": "https://signed/x", "kind": "xlsx"})
            return SkillOutput(answer="📊 Price list — checked against the books.", skill_name="commerce_admin")

    class _Setter:     # commerce_admin's run() shares ctx["files"] with metadata["files_out"]
        async def __call__(self, inp):
            inp.metadata.setdefault("files_out", [])
            return await _Admin()(inp)
    req = MagicMock()
    req.headers = {"authorization": "Bearer tok"}
    with patch.object(service, "_client", return_value=FakeSupabase()), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=identity.Person(T, member_id="m1", role="owner"))), \
         patch("vula.personal_memory.handle", return_value=None), \
         patch("vula.personal_memory.load", AsyncMock(return_value="")), \
         patch("core.skills.loader.get_skill", return_value=_Setter()):
        out = await commerce.admin_assistant(T, commerce.AssistantRequest(message="a spreadsheet of my products"), req)
    assert out["files"] == [{"name": "Price_list.xlsx", "url": "https://signed/x", "kind": "xlsx"}]


def test_commerce_admin_shares_its_files_list_with_the_caller():
    import inspect
    from core.skills import commerce_admin as ca
    src = inspect.getsource(ca.CommerceAdminSkill.run)
    assert '"files": inp.metadata.setdefault("files_out", [])' in src
