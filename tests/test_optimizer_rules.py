"""pydfs rules: captain pool, stacks, only-with, exposure, salary, ownership."""

import pytest
from pydfs_lineup_optimizer.exceptions import LineupOptimizerException

from simlab.services.optimizer import OptPlayer, OptRules, generate_lineups


def _p(pid, name, team, opp, pos, salary, proj, own=10, cpt=2, **kwargs):
    return OptPlayer(
        id=pid,
        name=name,
        team=team,
        opponent=opp,
        position=pos,
        salary=salary,
        projection=proj,
        ownership=own,
        cpt_ownership=cpt,
        dk_id=str(1000 + pid),
        cpt_dk_id=str(2000 + pid),
        **kwargs,
    )


def showdown_pool():
    """Two-team pool that can satisfy a salary floor and a captain allow-list."""
    return [
        _p(1, "C.J. Stroud", "HOU", "IND", "QB", 8000, 22, 30, 20),
        _p(2, "Nico Collins", "HOU", "IND", "WR", 9000, 18, 24, 10),
        _p(3, "Tank Dell", "HOU", "IND", "WR", 5200, 11, 12, 4),
        _p(4, "Dalton Schultz", "HOU", "IND", "TE", 4200, 9, 10, 2),
        _p(5, "David Montgomery", "HOU", "IND", "RB", 7000, 14, 16, 5),
        _p(6, "Texans", "HOU", "IND", "DST", 3800, 7, 8, 1),
        _p(7, "Daniel Jones", "IND", "HOU", "QB", 7200, 13, 14, 3),
        _p(8, "Jonathan Taylor", "IND", "HOU", "RB", 9000, 17, 20, 8),
        _p(9, "Alec Pierce", "IND", "HOU", "WR", 5600, 10, 9, 2),
        _p(10, "Tyler Warren", "IND", "HOU", "TE", 6400, 12, 15, 4),
        _p(11, "Deion Burks", "IND", "HOU", "WR", 3000, 16, 8, 1),
        _p(12, "Spencer Shrader", "IND", "HOU", "K", 3400, 8, 6, 1),
    ]


def rules(**kwargs):
    base = dict(
        n=6,
        qb_stack=1,
        bring_back=1,
        no_naked_pass_catchers=True,
        captain_ids=[1, 8, 2, 3],
        only_with=[],
        max_shared=4,
        min_salary=45000,
        max_salary=50000,
        min_own=40,
        max_own=220,
        randomness=False,
    )
    base.update(kwargs)
    return OptRules(**base)


def _ids(lineup):
    return [slot.player_id for slot in lineup.slots]


def _captain(lineup):
    return next(slot for slot in lineup.slots if slot.slot == "CPT")


def test_showdown_rules_hold_on_every_lineup():
    result = generate_lineups(showdown_pool(), rules(), showdown=True)
    assert len(result.lineups) == 6
    for lineup in result.lineups:
        ids = _ids(lineup)
        assert _captain(lineup).player_id in {1, 8, 2, 3}
        assert lineup.salary <= 50000
        assert lineup.salary >= 45000
        assert 40 <= lineup.ownership <= 220
        assert len(set(ids)) == 6
        teams = {slot.team for slot in lineup.slots}
        assert teams == {"HOU", "IND"}
        _assert_stack_and_naked(lineup)
        assert _captain(lineup).salary == int(round(
            next(p.salary for p in showdown_pool() if p.id == _captain(lineup).player_id) * 1.5
        ))


def test_exclude_and_captain_pool():
    pool = showdown_pool()
    for player in pool:
        if player.name in {"Tyler Warren", "Alec Pierce"}:
            player.excluded = True
    result = generate_lineups(pool, rules(captain_ids=[1, 8]), showdown=True)
    assert result.lineups
    for lineup in result.lineups:
        names = {slot.name for slot in lineup.slots}
        assert "Tyler Warren" not in names
        assert "Alec Pierce" not in names
        assert _captain(lineup).name in {"C.J. Stroud", "Jonathan Taylor"}


def test_only_with_blocks_the_player_unless_the_qb_is_there():
    pool = showdown_pool()
    # Burks is cheap and projects like a starter, so the solver wants him.
    bare = generate_lineups(
        pool,
        rules(n=8, only_with=[], no_naked_pass_catchers=False, qb_stack=0, bring_back=0, min_salary=0, min_own=None, max_own=None),
        showdown=True,
    )
    assert any(_has(lineup, 11) and not _has(lineup, 7) for lineup in bare.lineups)

    locked = generate_lineups(
        pool,
        rules(n=8, only_with=[(11, 7)], no_naked_pass_catchers=False, qb_stack=0, bring_back=0, min_salary=0, min_own=None, max_own=None),
        showdown=True,
    )
    assert locked.lineups
    for lineup in locked.lineups:
        if _has(lineup, 11):
            assert _has(lineup, 7)


def test_no_two_pass_catchers_without_their_qb():
    result = generate_lineups(
        showdown_pool(),
        rules(qb_stack=0, bring_back=0, no_naked_pass_catchers=True, min_salary=0),
        showdown=True,
    )
    for lineup in result.lineups:
        _assert_stack_and_naked(lineup, require_stack=False)


def test_qb_stack_and_bring_back():
    result = generate_lineups(
        showdown_pool(),
        rules(qb_stack=1, bring_back=1, no_naked_pass_catchers=True),
        showdown=True,
    )
    for lineup in result.lineups:
        _assert_stack_and_bring(lineup, stack=1, bring=1)


def test_max_shared_players():
    result = generate_lineups(showdown_pool(), rules(n=5, max_shared=3, min_salary=0), showdown=True)
    lineups = result.lineups
    assert len(lineups) >= 2
    for i, left in enumerate(lineups):
        for right in lineups[i + 1:]:
            shared = len(set(_ids(left)) & set(_ids(right)))
            assert shared <= 3


def test_showdown_exposure_cap_counts_captain_and_flex_together():
    pool = showdown_pool()
    for player in pool:
        if player.id == 1:
            player.projection = 40
            player.max_exposure = 0.25
    result = generate_lineups(
        pool,
        rules(n=8, captain_ids=[], max_shared=5, min_salary=0, min_own=None, max_own=None, qb_stack=0, bring_back=0),
        showdown=True,
    )
    assert len(result.lineups) == 8
    appearances = sum(1 for lineup in result.lineups if _has(lineup, 1))
    assert appearances <= 2


def test_min_exposure_is_met():
    pool = showdown_pool()
    for player in pool:
        if player.id == 12:
            player.projection = 1
            player.min_exposure = 0.5
    result = generate_lineups(
        pool,
        rules(n=6, captain_ids=[], max_shared=5, min_salary=0, min_own=None, max_own=None, qb_stack=0, bring_back=0, no_naked_pass_catchers=False),
        showdown=True,
    )
    appearances = sum(1 for lineup in result.lineups if _has(lineup, 12))
    assert appearances >= 3


def test_classic_exposure_and_roster_shape():
    pool = _classic_pool()
    for player in pool:
        if player.name == "Patrick Mahomes":
            player.max_exposure = 0.25
            player.projection = 40
    result = generate_lineups(
        pool,
        OptRules(
            n=4,
            qb_stack=1,
            bring_back=1,
            no_naked_pass_catchers=True,
            max_shared=7,
            min_salary=44000,
            max_salary=50000,
            min_own=0,
            max_own=400,
        ),
        showdown=False,
    )
    assert len(result.lineups) == 4
    appearances = 0
    for lineup in result.lineups:
        assert lineup.salary <= 50000
        assert lineup.salary >= 44000
        counts = {}
        for slot in lineup.slots:
            counts[slot.position] = counts.get(slot.position, 0) + 1
        assert counts["QB"] == 1 and counts["DST"] == 1
        assert counts["RB"] >= 2 and counts["WR"] >= 3 and counts["TE"] >= 1
        if any(slot.name == "Patrick Mahomes" for slot in lineup.slots):
            appearances += 1
        _assert_stack_and_bring(lineup, stack=1, bring=1)
    assert appearances <= 1


def test_locked_captain_and_infeasible_message():
    pool = showdown_pool()
    for player in pool:
        if player.id == 8:
            player.lock = "cpt"
    result = generate_lineups(pool, rules(n=2, captain_ids=[8]), showdown=True)
    assert all(_captain(lineup).player_id == 8 for lineup in result.lineups)
    for player in pool:
        player.excluded = player.id != 8
    with pytest.raises(LineupOptimizerException):
        generate_lineups(pool, rules(n=1), showdown=True)


def _has(lineup, pid):
    return any(slot.player_id == pid for slot in lineup.slots)


def _assert_stack_and_naked(lineup, require_stack=False):
    by_team = {}
    for slot in lineup.slots:
        by_team.setdefault(slot.team, []).append(slot)
    for team, slots in by_team.items():
        qbs = [s for s in slots if s.position == "QB"]
        pcs = [s for s in slots if s.position in {"WR", "TE"}]
        if len(pcs) >= 2:
            assert qbs, team
        if require_stack and qbs:
            assert len(pcs) >= 1


def _assert_stack_and_bring(lineup, stack, bring):
    by_team = {}
    for slot in lineup.slots:
        by_team.setdefault(slot.team, []).append(slot)
    teams = set(by_team)
    for team, slots in by_team.items():
        qbs = [s for s in slots if s.position == "QB"]
        pcs = [s for s in slots if s.position in {"WR", "TE"}]
        if len(pcs) >= 2:
            assert qbs
        if not qbs:
            continue
        assert len(pcs) >= stack
        other = next(iter(teams - {team}))
        opp_skill = [s for s in by_team[other] if s.position in {"WR", "TE", "RB"}]
        assert len(opp_skill) >= bring


def _classic_pool():
    """Two games, enough depth to fill QB/RB/WR/TE/FLEX/DST."""
    rows = []
    pid = 1
    specs = [
        ("Patrick Mahomes", "KC", "BAL", "QB", 7000, 24, 40),
        ("Travis Kelce", "KC", "BAL", "TE", 6000, 14, 20),
        ("Rashee Rice", "KC", "BAL", "WR", 5800, 13, 18),
        ("Xavier Worthy", "KC", "BAL", "WR", 4800, 10, 12),
        ("Isiah Pacheco", "KC", "BAL", "RB", 6200, 14, 16),
        ("Kareem Hunt", "KC", "BAL", "RB", 4500, 8, 6),
        ("Chiefs", "KC", "BAL", "DST", 3200, 7, 8),
        ("Lamar Jackson", "BAL", "KC", "QB", 7200, 23, 30),
        ("Zay Flowers", "BAL", "KC", "WR", 6000, 13, 16),
        ("Mark Andrews", "BAL", "KC", "TE", 4600, 9, 10),
        ("Derrick Henry", "BAL", "KC", "RB", 7800, 18, 28),
        ("Justice Hill", "BAL", "KC", "RB", 4000, 7, 5),
        ("Ravens", "BAL", "KC", "DST", 3400, 7, 6),
        ("Josh Allen", "BUF", "MIA", "QB", 7000, 22, 20),
        ("James Cook", "BUF", "MIA", "RB", 6400, 15, 14),
        ("Khalil Shakir", "BUF", "MIA", "WR", 5000, 11, 10),
        ("Dalton Kincaid", "BUF", "MIA", "TE", 4200, 8, 6),
        ("Tyreek Hill", "MIA", "BUF", "WR", 6800, 16, 18),
        ("De'Von Achane", "MIA", "BUF", "RB", 7000, 16, 20),
        ("Jaylen Waddle", "MIA", "BUF", "WR", 5600, 12, 12),
        ("Dolphins", "MIA", "BUF", "DST", 3000, 6, 4),
        ("Bills", "BUF", "MIA", "DST", 3100, 6, 5),
    ]
    built = []
    for name, team, opp, pos, sal, proj, own in specs:
        built.append(_p(pid, name, team, opp, pos, sal, proj, own, 0))
        pid += 1
    return built
