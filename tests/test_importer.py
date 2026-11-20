"""Salary and projection files for the IND@HOU showdown sample."""

from pathlib import Path

from simlab.services.importer import apply_projections, parse_dk_salary

ROOT = Path(__file__).resolve().parents[1] / "sample_data"


def test_sample_slate_matches_names_and_skips_nothing_important():
    slate = parse_dk_salary((ROOT / "dksal.csv").read_text(encoding="utf-8-sig"))
    apply_projections(slate, (ROOT / "rgp_projections.csv").read_text(encoding="utf-8-sig"))
    assert slate.format == "showdown"
    assert slate.game == "IND@HOU"
    by_name = {player.name: player for player in slate.players}
    stroud = by_name["C.J. Stroud"]
    assert stroud.team == "HOU"
    assert stroud.position == "QB"
    assert stroud.salary == 10000
    assert stroud.projection == 21.1
    assert stroud.ownership == 71.4
    assert stroud.cpt_ownership == 34.0
    assert stroud.cpt_dk_id
    assert stroud.dk_id
    assert stroud.cpt_dk_id != stroud.dk_id
    assert by_name["Texans"].position == "DST"
    assert by_name["Texans"].projection == 9.0
    assert by_name["Ka'imi Fairbairn"].projection == 10.0
    assert by_name["Woody Marks"].status == "OUT"
    assert by_name["Jonathan Taylor"].opponent == "HOU"
    # CPT salary in the DK file is 1.5x and is not what we store.
    assert by_name["Jonathan Taylor"].salary == 10600
