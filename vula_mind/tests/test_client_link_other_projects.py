"""A client's link never shows a document that names another of the business's projects
(vula/commerce/project_share_docs.other_projects, 10 Nov 2026) — and the business's own address,
printed across two lines, no longer "names" a project (doc_filing.without_own_address).

DIGG, Belladonna: a skip invoice for Belladonna and Paarden Island, and a Pudlo invoice delivered to
HPC's site, were on Rudi's link; every skip slip addressed to "Digg Architects 22 \\nPorterfield Road"
also named the Porterfield project, so none was filed without asking."""
from unittest.mock import patch

from vula.commerce import project_share_docs as sd
from vula.integrations import doc_filing as df

T = "test-tenant"
PROJECTS = [
    {"name": "Belladonna", "ids": {"belladonna", "belladonna drive", "vredehoek"}, "words": {"belladonna"}},
    {"name": "HPC Bokaap", "ids": {"hpc bokaap", "15 jordaan", "hpc projek"}, "words": {"hpc"}},
    {"name": "Atlantis Paarden Eiland", "ids": {"atlantis paarden eiland", "paarden island", "8 wessex"},
     "words": {"atlantis"}},
    {"name": "Life Changers - Tableview", "ids": {"life changers tableview"}, "words": {"life"}},
    {"name": "Porterfield", "ids": {"porterfield"}, "words": {"porterfield"}},
    {"name": "Sporty TV – Phase 2", "ids": {"sporty tv phase 2"}, "words": set()},
]


def _named(doc, project="Belladonna", related=None):
    with patch.object(df, "own_address_phrases", return_value=["22 porterfield road"]):
        return sd.other_projects(T, project, doc, PROJECTS, related={project.lower()} if related is None else related)


def test_the_own_address_is_removed_even_across_a_line_break():
    with patch.object(df, "own_address_phrases", return_value=["22 porterfield road"]):
        out = df.without_own_address(T, "Att: Digg Architects 22 \nPorterfield Road \nBlouberg 744 07 Belladonna Drive")
    assert "porterfield" not in out.lower() and "Belladonna Drive" in out


def test_a_document_that_names_another_projects_site_is_named():
    skip = {"summary": "Skip rentals", "fields": {"line_items": [
        {"description": "Skip Rental - 8 Wessex Paarden Island Road"},
        {"description": "Skip Rental - 07 Belladonna Drive, Vredehoek"}]}}
    assert _named(skip) == ["Atlantis Paarden Eiland"]
    pudlo = {"summary": "Tiles and grout, with a delivery address of 15 Jordaan Street, Bo-Kaap."}
    assert _named(pudlo) == ["HPC Bokaap"]
    assert _named({"summary": "Order ref HPC 0042"}) == ["HPC Bokaap"]          # a short word in capitals


def test_what_is_not_another_project():
    # the business's own address on every slip
    assert _named({"summary": "Att: Digg Architects 22 \nPorterfield Road Blouberg"}) == []
    # a short project word inside a company name, not in capitals
    assert _named({"summary": "a delivery note from Ceramica Life's Tile (Pty) Ltd"}) == []
    # the project's own site
    assert _named({"summary": "Skip rental, 07 Belladonna Drive"}) == []
    # a phase or the same client's other job is no leak
    assert _named({"summary": "Sporty TV Phase 2 quote"}, project="Sporty TV",
                  related={"sporty tv", "sporty tv phase 2"}) == []


def test_the_owner_can_show_one_anyway():
    pudlo = {"summary": "delivery address 15 Jordaan Street", "fields": {"_show_on_client_link": True}}
    assert _named(pudlo) == []


def test_the_client_documents_leave_it_off_and_never_fail(monkeypatch):
    from vula.commerce import project_file as pf
    docs = [{"id": "a", "category": "Invoice", "summary": "Skip, 8 Wessex Paarden Island", "fields": {},
             "created_at": "2026-10-22"},
            {"id": "b", "category": "Invoice", "summary": "Skip, 07 Belladonna Drive", "fields": {},
             "created_at": "2026-10-23"}]
    monkeypatch.setattr(pf, "_documents", lambda t, p: docs)
    monkeypatch.setattr(pf, "_supplier_rows", lambda t: [])
    monkeypatch.setattr(sd, "_related", lambda t, p: {"belladonna"})
    with patch("vula.integrations.project_resolver._projects", return_value=PROJECTS), \
         patch.object(df, "own_address_phrases", return_value=[]):
        assert [r["id"] for r in sd.client_documents(T, "Belladonna")] == ["b"]
    with patch("vula.integrations.project_resolver._projects", side_effect=RuntimeError("db down")):
        assert {r["id"] for r in sd.client_documents(T, "Belladonna")} == {"a", "b"}      # fail-open: page stays up
