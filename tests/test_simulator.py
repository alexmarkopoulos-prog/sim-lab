"""Correlation, exact showdown optimals, and the ownership-calibrated field."""

import numpy as np

from simlab.services.dupes import expected_dupes
from simlab.services.simulator import (
    DISCLAIMER,
    SimLineup,
    SimParams,
    SimPlayer,
    build_optimizer_field,
    enumerate_showdown,
    run_showdown,
    simulate_points,
)


def test_enumeration_respects_cap_and_both_teams():
    salary = np.array([10000, 9000, 8000, 7000, 4000, 3000, 3000, 2000], dtype=np.int32)
    team = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=np.int16)
    lineups, sals = enumerate_showdown(salary, team, cap=50000)
    assert lineups.shape[0] > 0
    assert lineups.shape[1] == 6
    for row, sal in zip(lineups, sals):
        assert len(set(row.tolist())) == 6
        assert sal <= 50000
        assert sal == int(salary[row[0]] * 3 // 2 + salary[row[1:]].sum())
        assert len(set(team[row].tolist())) >= 2


def test_qb_links_to_pass_catchers_and_dst_fades_the_opposing_offense():
    # team slot 0: QB, WR, cheap WR. team slot 1: DST opposing them, plus a QB.
    mu = np.array([20, 15, 8, 7, 18], dtype=np.float64)
    salary = np.array([8000, 7000, 3000, 4000, 7500], dtype=np.float64)
    position = ["QB", "WR", "WR", "DST", "QB"]
    team_slot = np.array([0, 0, 0, 1, 1])
    game_index = np.zeros(5, dtype=np.int64)
    rng = np.random.default_rng(7)
    samples = simulate_points(mu, salary, position, team_slot, game_index, 1, 4000, rng)
    qb_wr = np.corrcoef(samples[:, 0], samples[:, 1])[0, 1]
    qb_dst = np.corrcoef(samples[:, 0], samples[:, 3])[0, 1]
    cheap_cv = samples[:, 2].std() / samples[:, 2].mean()
    qb_cv = samples[:, 0].std() / samples[:, 0].mean()
    assert qb_wr > 0.2
    assert qb_dst < -0.05
    assert cheap_cv > qb_cv


def test_field_is_mostly_49k_and_dupes_match_exact_counts():
    # Two lineups, same captain. The cheap one projects higher, so it wins
    # whenever the salary floor allows it. The $50k lineup is the only legal
    # choice on the 85% of draws that require $49k.
    lineups = np.array([
        [0, 1, 2, 3, 4, 5],
        [0, 1, 2, 3, 4, 6],
    ], dtype=np.int16)
    salaries = np.array([50000, 47000], dtype=np.int32)
    mu = np.array([10, 10, 10, 10, 10, 4, 30], dtype=np.float64)
    cpt_own = np.array([100, 0, 0, 0, 0, 0, 0], dtype=np.float64)
    flex_own = np.ones(7)
    field = build_optimizer_field(
        lineups, salaries, mu, cpt_own, flex_own,
        pool_size=2000, seed=3, calib_rounds=0,
    )
    assert set(np.unique(field)).issubset({0, 1})
    chalk = float(np.mean(field == 0))
    assert 0.75 < chalk < 0.95
    dupes = expected_dupes(field, np.array([0, 1, 4]), contest_size=1001)
    assert abs(dupes[0] - np.mean(field == 0) * 1000) < 1e-6
    assert abs(dupes[1] - np.mean(field == 1) * 1000) < 1e-6
    assert dupes[2] == 0
    # A ownership product over the cheap lineup's players would not equal this count.
    product = (0.4 ** 6) * 1000
    assert abs(dupes[1] - product) > 1


def test_showdown_report_labels_model_output_and_scores_a_lineup():
    players = []
    pid = 1
    for team, opp in (("HOU", "IND"), ("IND", "HOU")):
        players.append(SimPlayer(pid, f"QB {team}", team, opp, "QB", 8000, 18, 20, 15)); pid += 1
        players.append(SimPlayer(pid, f"RB {team}", team, opp, "RB", 7600, 14, 18, 6)); pid += 1
        players.append(SimPlayer(pid, f"WR {team}", team, opp, "WR", 7000, 13, 16, 5)); pid += 1
        players.append(SimPlayer(pid, f"WR2 {team}", team, opp, "WR", 4200, 9, 8, 1)); pid += 1
        players.append(SimPlayer(pid, f"TE {team}", team, opp, "TE", 3600, 7, 6, 1)); pid += 1
    # Captain QB HOU (id 1), flex the five highest remaining salaries that fit.
    flex = [2, 3, 6, 7, 8]
    lineup = SimLineup(key="saved-1", source="saved", player_ids=[1, *flex])
    report = run_showdown(
        players,
        [lineup],
        SimParams(n_sims=300, contest_size=2000, entry_fee=20, field_sample=400,
                  field_pool=600, calib_rounds=1, calib_size=200, pool_limit=12, seed=4),
    )
    assert report.disclaimer == DISCLAIMER
    assert "not real contest ROI" in report.disclaimer
    assert report.optimal_method == "exact_enumeration"
    assert report.lineups
    row = report.lineups[0]
    assert {"top01", "top1", "itm", "roi", "dupes"} <= set(row)
    assert 0 <= row["top01"] <= 100
    assert report.field["pct_salary_49k"] >= 0
    assert any(player["opt"] > 0 for player in report.players)
    assert report.scripts
