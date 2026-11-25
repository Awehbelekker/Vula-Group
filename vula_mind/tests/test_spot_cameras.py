"""Live cameras at surf spots (vula/commerce/spot_cameras.py): a link to the owner's page unless
the owner agreed to an embed, and only ever a player on a host we embed."""
from vula.commerce import spot_cameras as sc

BIG_BAY = (-33.794, 18.459)


def cam(**over):
    return {"id": "c", "name": "Cam", "spot": "Spot", "lat": -33.7946, "lng": 18.4577, "owner": "Owner",
            "page_url": "https://owner.example/", "embed_url": "https://g0.ipcamlive.com/player/player.php?alias=x",
            "permitted_on": None, **over}


def test_big_bay_is_listed_as_a_link_until_the_club_agrees():
    got = sc.near(*BIG_BAY)
    assert [c["id"] for c in got] == ["big-bay-slsc"]
    c = got[0]
    assert c["kind"] == "link" and c["embed_url"] is None                    # no permission on record yet
    assert c["page_url"] == "https://bigbaylifesaving.co.za/" and c["owner"] == "Big Bay Surf Lifesaving Club"
    assert set(c) == {"id", "name", "spot", "owner", "page_url", "kind", "embed_url"}   # no position, no notes


def test_an_embed_needs_the_owners_yes_and_a_host_we_embed():
    assert sc.public(cam())["kind"] == "link"
    yes = sc.public(cam(permitted_on="2026-12-01"))
    assert yes["kind"] == "embed" and yes["embed_url"].startswith("https://g0.ipcamlive.com/")
    for bad in ("https://evil.example/player", "http://g0.ipcamlive.com/player/x", "https://ipcamlive.com.evil.example/p",
                "javascript:alert(1)", ""):
        got = sc.public(cam(permitted_on="2026-12-01", embed_url=bad))
        assert got["kind"] == "link" and got["embed_url"] is None, bad


def test_only_cameras_near_the_spot_and_nearest_first():
    far = cam(id="far", lat=-34.1087, lng=18.4702)                           # Muizenberg, ~35 km away
    closer = cam(id="closer", lat=-33.7941, lng=18.4592)
    assert [c["id"] for c in sc.near(*BIG_BAY, cameras=[far, cam(id="near"), closer])] == ["closer", "near"]
    assert sc.near(None, None) == [] and sc.near("x", 18.4) == [] and sc.near(-34.1087, 18.4702) == []
