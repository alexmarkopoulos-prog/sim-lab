"""The IND@HOU showdown Alex asked to build, then a short sim of those lineups."""

from pathlib import Path

from simlab.services.importer import apply_projections, parse_dk_salary
from simlab.services.optimizer import OptPlayer, OptRules, generate_lineups
from simlab.services.simulator import DISCLAIMER, SimLineup, SimParams, SimPlayer, run_showdown

ROOT = Path(__file__).resolve().parents[1] / "sample_data"
BANNED = {"Xavier Hutchinson", "Seth McGowan", "Tyler Warren"}
CAPTAINS = {"C.J. Stroud", "Jonathan Taylor", "Nico Collins", "Tank Dell"}


def _slate_players():
    slate = parse_dk_salary((ROOT / "dksal.csv").read_text(encoding="utf-8-sig"))
    apply_projections(slate, (ROOT / "rgp_projections.csv").read_text(encoding="utf-8-sig"))
    players = []
    ids = {}
    for index, player in enumerate(slate.players, start=1):
        players.append(OptPlayer(
            id=index, name=player.name, team=player.team, opponent=player.opponent,
            position=player.position, salary=player.salary, projection=player.projection,
            ownership=player.ownership, cpt_ownership=player.cpt_ownership,
            dk_id=player.dk_id, cpt_dk_id=player.cpt_dk_id, status=player.status,
            excluded=player.name in BANNED,
        ))
        ids[player.name] = index
    return players, ids


def test_four_showdown_lineups_follow_the_slate_rules():
    players, ids = _slate_players()
    rules = OptRules(
        n=4, qb_stack=1, bring_back=1, no_naked_pass_catchers=True,
        captain_ids=[ids[name] for name in CAPTAINS], max_shared=4, max_per_captain=2,
        min_salary=46000, max_salary=50000, min_own=160, max_own=210,
    )
    result = generate_lineups(players, rules, showdown=True)
    assert len(result.lineups) == 4
    captains = set()
    for lineup in result.lineups:
        names = {slot.name for slot in lineup.slots}
        assert not names & BANNED
        captain = next(slot for slot in lineup.slots if slot.slot == "CPT")
        assert captain.name in CAPTAINS
        captains.add(captain.name)
        assert 46000 <= lineup.salary <= 50000
        assert 160 <= lineup.ownership <= 210
        teams = {slot.team for slot in lineup.slots}
        assert len(teams) >= 2
    assert len(captains) >= 2

    sim_players = [
        SimPlayer(p.id, p.name, p.team, p.opponent, p.position, p.salary, p.projection,
                  p.ownership, p.cpt_ownership)
        for p in players if p.status not in {"OUT", "IR"} and p.projection > 0
    ]
    lineups = [
        SimLineup(f"saved-{i}", "saved", [slot.player_id for slot in lineup.slots])
        for i, lineup in enumerate(result.lineups)
    ]
    report = run_showdown(
        sim_players, lineups,
        SimParams(n_sims=400, contest_size=4000, entry_fee=20, field_sample=300,
                  field_pool=800, calib_rounds=1, calib_size=200, pool_limit=14, seed=19),
    )
    assert report.disclaimer == DISCLAIMER
    assert len(report.lineups) == 4
    assert report.field["pct_salary_49k"] >= 70
    by_id = {player.id: player for player in players}
    by_key = {lineup.key: lineup for lineup in lineups}
    gaps = []
    for row in report.lineups:
        ids = by_key[row["key"]].player_ids
        product = 3999.0
        for index, pid in enumerate(ids):
            own = by_id[pid].cpt_ownership if index == 0 else by_id[pid].ownership
            product *= max(own / 100.0, 0.001)
        gaps.append(abs(row["dupes"] - product))
        assert row["dupes"] >= 0
    # At least one lineup's duplicate count is an exact-match frequency,
    # which is not the product of its six ownerships.
    assert max(gaps) > 1
