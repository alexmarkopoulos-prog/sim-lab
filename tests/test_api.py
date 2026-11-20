"""Login, slate upload, and the IND@HOU showdown build."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "sample_data"
BANNED = {"Xavier Hutchinson", "Seth McGowan", "Tyler Warren"}
CAPTAINS = {"C.J. Stroud", "Jonathan Taylor", "Nico Collins", "Tank Dell"}


def _login(client):
    response = client.post("/api/auth/login", json={"email": "alex@example.com", "password": "test-password"})
    assert response.status_code == 200, response.text


def test_password_and_magic_link(client):
    bad = client.post("/api/auth/login", json={"email": "alex@example.com", "password": "nope"})
    assert bad.status_code == 401
    _login(client)
    me = client.get("/api/me")
    assert me.json()["email"] == "alex@example.com"
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401

    magic = client.post("/api/auth/magic", json={"email": "alex@example.com"})
    link = magic.json()["dev_link"]
    path = link.split("http://testserver", 1)[1]
    followed = client.get(path, follow_redirects=False)
    assert followed.status_code == 303
    assert client.get("/api/me").json()["email"] == "alex@example.com"


def test_upload_generate_and_export(client):
    _login(client)
    with (ROOT / "dksal.csv").open("rb") as salary, (ROOT / "rgp_projections.csv").open("rb") as projections:
        created = client.post(
            "/api/slates",
            files={
                "salary": ("dksal.csv", salary, "text/csv"),
                "projections": ("rgp.csv", projections, "text/csv"),
            },
        )
    assert created.status_code == 200, created.text
    slate = created.json()
    assert slate["format"] == "showdown"
    assert slate["game"] == "IND@HOU"
    by_name = {player["name"]: player for player in slate["players"]}
    assert by_name["C.J. Stroud"]["projection"] == 21.1
    assert by_name["Woody Marks"]["status"] == "OUT"
    for name in BANNED:
        patched = client.patch(
            f"/api/slates/{slate['id']}/players/{by_name[name]['id']}",
            json={"excluded": True},
        )
        assert patched.status_code == 200
    rules = dict(slate["rules"])
    rules["captain_ids"] = [by_name[name]["id"] for name in CAPTAINS]
    rules["n"] = 4
    rules["max_per_captain"] = 2
    generated = client.post(f"/api/slates/{slate['id']}/lineups/generate", json=rules)
    assert generated.status_code == 200, generated.text
    lineups = [row for row in generated.json()["lineups"] if row["source"] == "optimizer"]
    assert len(lineups) == 4
    for lineup in lineups:
        names = {slot["name"] for slot in lineup["slots"]}
        assert not names & BANNED
        captain = next(slot for slot in lineup["slots"] if slot["slot"] == "CPT")
        assert captain["name"] in CAPTAINS
        assert 46000 <= lineup["salary"] <= 50000
        assert 160 <= lineup["ownership"] <= 210
        assert len(lineup["slots"]) == 6
    exported = client.get(f"/api/slates/{slate['id']}/lineups/export")
    assert exported.status_code == 200
    assert exported.text.splitlines()[0] == "CPT,FLEX,FLEX,FLEX,FLEX,FLEX"
    assert by_name["C.J. Stroud"]["cpt_dk_id"] in exported.text or by_name["Nico Collins"]["cpt_dk_id"] in exported.text
