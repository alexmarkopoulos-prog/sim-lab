"""Correlated Monte Carlo for DraftKings NFL, with an optimizer-style field.

Showdown solves the optimal lineup in every sim by enumerating legal CPT+FLEX
rosters (the same structure as the IND@HOU numpy sim this app is built from).
Classic slates are too large to enumerate, so the optimal is the best lineup
inside an optimizer candidate set and the result says so.

The field is mostly $49k+, chosen by noisy projections whose weights are
calibrated toward ownership. Duplicates are exact lineup matches in that
field. Ownership is never multiplied. Every report is a model output.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from simlab.services.dupes import expected_dupes

DISCLAIMER = (
    "Model output for ranking lineups. This is not real contest ROI, "
    "and Sim Lab never enters contests."
)


@dataclass
class SimPlayer:
    id: int
    name: str
    team: str
    opponent: str
    position: str
    salary: int
    projection: float
    ownership: float
    cpt_ownership: float
    boost: float = 1.0


@dataclass
class SimLineup:
    key: str
    source: str
    player_ids: list[int]


@dataclass
class SimParams:
    n_sims: int = 10000
    contest_size: int = 5000
    entry_fee: float = 20.0
    max_entries: int = 4
    cash_pct: float = 0.20
    rake: float = 0.15
    field_sample: int = 4000
    field_pool: int = 8000
    calib_rounds: int = 4
    calib_size: int = 1500
    pool_limit: int = 20
    seed: int = 1119
    payout_tiers: list[dict] | None = None


@dataclass
class SimReport:
    disclaimer: str
    format: str
    n_sims: int
    optimal_method: str
    players: list[dict]
    scripts: list[dict]
    lineups: list[dict]
    field: dict
    payout_tiers: list[dict]
    pool_names: list[str] = field(default_factory=list)


def default_payout_tiers(
    contest_size: int,
    entry_fee: float,
    cash_pct: float = 0.20,
    rake: float = 0.15,
) -> list[dict]:
    """Top-heavy GPP curve. Places inside a band split that band's pool share."""
    contest_size = max(1, int(contest_size))
    pool = contest_size * float(entry_fee) * (1.0 - float(rake))
    cash_n = min(contest_size, max(1, int(math.ceil(contest_size * cash_pct - 1e-12))))
    top01_n = min(cash_n, max(1, int(math.ceil(contest_size * 0.001 - 1e-12))))
    top1_n = min(cash_n, max(top01_n, int(math.ceil(contest_size * 0.01 - 1e-12))))
    bands = []
    if top01_n >= 1:
        bands.append((1, top01_n, 0.35))
    if top1_n > top01_n:
        bands.append((top01_n + 1, top1_n, 0.25))
    if cash_n > top1_n:
        bands.append((top1_n + 1, cash_n, 0.40))
    share = sum(item[2] for item in bands) or 1.0
    tiers = []
    for start, end, weight in bands:
        count = end - start + 1
        prize = round(pool * (weight / share) / count, 2)
        tiers.append({"start": int(start), "end": int(end), "prize": prize})
    return tiers


def enumerate_showdown(salary: np.ndarray, team: np.ndarray, cap: int = 50000):
    """Legal showdown lineups: captain + 5 distinct flex, both teams, under the cap.

    Captain salary is 1.5x, matching DraftKings. Returns index array (M, 6)
    and lineup salaries in dollars.
    """
    salary = np.asarray(salary, dtype=np.int32)
    team = np.asarray(team, dtype=np.int16)
    n = int(salary.shape[0])
    rows: list[tuple[int, ...]] = []
    sals: list[int] = []
    everyone = list(range(n))
    for captain in everyone:
        captain_salary = int(salary[captain] * 3 // 2)
        captain_team = int(team[captain])
        others = [j for j in everyone if j != captain]
        for combo in combinations(others, 5):
            total = captain_salary + int(salary[list(combo)].sum())
            if total > cap:
                continue
            flex_teams = team[list(combo)]
            if int(flex_teams.min()) == int(flex_teams.max()) == captain_team:
                continue
            rows.append((captain, *combo))
            sals.append(total)
    if not rows:
        return np.zeros((0, 6), np.int16), np.zeros((0,), np.int32)
    return np.asarray(rows, dtype=np.int16), np.asarray(sals, dtype=np.int32)


def simulate_points(
    mu: np.ndarray,
    salary: np.ndarray,
    position: list[str],
    team_slot: np.ndarray,
    game_index: np.ndarray,
    n_games: int,
    n_sims: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Lognormal fantasy points with a shared game-script factor model.

    A QB loads on his team's score and pass share, so his pass catchers move
    with him. RBs load on team score and against pass share. A DST loads
    negatively on the opposing offense. Cheap skill players get a fatter tail
    and a bust draw.
    """
    mu = np.asarray(mu, dtype=np.float64)
    salary = np.asarray(salary, dtype=np.float64)
    team_slot = np.asarray(team_slot, dtype=np.int64)
    game_index = np.asarray(game_index, dtype=np.int64)
    n = mu.shape[0]
    factors = max(1, n_games) * 6
    load = np.zeros((n, factors), dtype=np.float64)
    sig = np.zeros(n, dtype=np.float64)
    for j in range(n):
        base = int(game_index[j]) * 6
        t = int(team_slot[j])
        pos = position[j]
        cheap = salary[j] < 5000
        load[j, base + 0] = 0.12
        if pos == "QB":
            load[j, base + 1 + t] = 0.30
            load[j, base + 3 + t] = 0.22
            sig[j] = 0.30
        elif pos in {"WR", "TE"}:
            load[j, base + 1 + t] = 0.24
            load[j, base + 3 + t] = 0.22
            sig[j] = 0.80 if cheap else 0.55
        elif pos == "RB":
            load[j, base + 1 + t] = 0.28
            load[j, base + 3 + t] = -0.15
            load[j, base + 5] = 0.12 if t == 0 else -0.10
            sig[j] = 0.85 if cheap else 0.45
        elif pos == "K":
            load[j, base + 1 + t] = 0.18
            load[j, base + 3 + t] = -0.05
            sig[j] = 0.42
        else:
            load[j, base + 0] = -0.10
            load[j, base + 1 + (1 - t)] = -0.32
            load[j, base + 5] = 0.12 if t == 0 else -0.12
            sig[j] = 0.62
    z = rng.standard_normal((n_sims, factors))
    for g in range(max(1, n_games)):
        base = g * 6
        z[:, base + 1] += 0.35 * z[:, base + 5]
        z[:, base + 2] -= 0.35 * z[:, base + 5]
    residual = rng.standard_normal((n_sims, n))
    latent = z @ load.T + residual * sig
    scale = np.sqrt((load ** 2).sum(axis=1) + sig ** 2)
    scale[scale == 0] = 1.0
    x = latent / scale
    s_tot = np.where(salary < 5000, 0.85, 0.62)
    s_tot = np.where(np.array(position) == "QB", 0.42, s_tot)
    s_tot = np.where(np.array(position) == "K", 0.50, s_tot)
    s_tot = np.where(np.array(position) == "DST", 0.75, s_tot)
    safe_mu = np.maximum(mu, 0.0)
    samples = safe_mu * np.exp(s_tot * x - s_tot ** 2 / 2.0)
    cheap_wr = np.array([(position[j] in {"WR", "TE"} and salary[j] < 5000) for j in range(n)])
    bust = (rng.random((n_sims, n)) < 0.18) & cheap_wr
    samples = np.where(bust, samples * 0.15, samples)
    peak = float(safe_mu.max()) if safe_mu.size else 1.0
    if peak <= 0:
        peak = 1.0
    dst = np.array(position) == "DST"
    samples = np.where(dst, samples - 1.5 + 1.5 * (safe_mu / peak), samples)
    return samples.astype(np.float32)


def optimal_indices(samples: np.ndarray, lineups: np.ndarray, batch: int = 24) -> np.ndarray:
    """Index of the highest-scoring showdown lineup in each sim."""
    n_sims = samples.shape[0]
    out = np.zeros(n_sims, dtype=np.int32)
    if lineups.size == 0:
        return out
    mult = np.array([1.5, 1, 1, 1, 1, 1], dtype=np.float32)
    for start in range(0, n_sims, batch):
        block = samples[start:start + batch]
        scores = np.zeros((block.shape[0], lineups.shape[0]), dtype=np.float32)
        for slot in range(6):
            scores += mult[slot] * block[:, lineups[:, slot]]
        out[start:start + batch] = scores.argmax(axis=1)
    return out


def _score_selected(samples: np.ndarray, lineups: np.ndarray, chosen: np.ndarray, captain_mult: float) -> np.ndarray:
    """Score ``chosen`` lineup indices. chosen shape (sims,) or (sims, k)."""
    picked = lineups[chosen]
    scores = np.zeros(chosen.shape, dtype=np.float32)
    width = picked.shape[-1]
    for slot in range(width):
        mult = captain_mult if slot == 0 and captain_mult != 1 else 1.0
        if slot == 0:
            mult = captain_mult
        scores += np.float32(mult) * np.take_along_axis(samples, picked[..., slot], axis=1)
    return scores


def build_optimizer_field(
    lineups: np.ndarray,
    salaries: np.ndarray,
    mu: np.ndarray,
    cpt_own: np.ndarray,
    flex_own: np.ndarray,
    *,
    pool_size: int,
    seed: int,
    calib_rounds: int = 4,
    calib_size: int = 1500,
    high_salary: int = 49000,
    low_salary: int = 46500,
    high_salary_rate: float = 0.85,
    captain_mult: float = 1.5,
) -> np.ndarray:
    """Pick a field of lineup indices. Most of them are at least $49k.

    Captains are drawn from captain ownership. Flex weights are nudged so
    realized flex exposure moves toward flex ownership. Each draw then takes
    the best noisy projection at that captain that clears the salary floor.
    """
    lineups = np.asarray(lineups)
    salaries = np.asarray(salaries)
    mu = np.asarray(mu, dtype=np.float64)
    n = mu.shape[0]
    m = lineups.shape[0]
    if m == 0 or pool_size <= 0:
        return np.zeros((0,), dtype=np.int32)
    by_cpt = [np.where(lineups[:, 0] == c)[0] for c in range(n)]
    cpt_p = np.maximum(np.asarray(cpt_own, dtype=np.float64), 0.0)
    if cpt_p.sum() <= 0:
        cpt_p = np.ones(n)
    cpt_p = cpt_p / cpt_p.sum()
    flex_tgt = np.maximum(np.asarray(flex_own, dtype=np.float64), 0.0)
    if flex_tgt.sum() <= 0:
        flex_tgt = np.ones(n)
    flex_tgt = flex_tgt / flex_tgt.sum() * 500.0
    weights = np.ones(n, dtype=np.float64)
    best_w = weights.copy()
    best_err = 1e18
    rounds = max(0, calib_rounds)
    for step in range(rounds):
        sample = _draw_field(
            by_cpt, lineups, salaries, mu, weights, cpt_p, calib_size,
            seed=1000 + step, high_salary=high_salary, low_salary=low_salary,
            high_salary_rate=high_salary_rate, captain_mult=captain_mult,
        )
        flex_exp = _flex_exposure(lineups, sample, n)
        err = float(np.abs(flex_exp - flex_tgt).sum())
        if err < best_err:
            best_err = err
            best_w = weights.copy()
        ratio = np.clip((np.maximum(flex_tgt, 0.3) / np.maximum(flex_exp, 0.3)) ** 0.25, 0.8, 1.25)
        weights = weights * ratio
    if rounds:
        weights = best_w
    return _draw_field(
        by_cpt, lineups, salaries, mu, weights, cpt_p, pool_size,
        seed=seed, high_salary=high_salary, low_salary=low_salary,
        high_salary_rate=high_salary_rate, captain_mult=captain_mult,
    )


def _flex_exposure(lineups: np.ndarray, chosen: np.ndarray, n: int) -> np.ndarray:
    if chosen.size == 0:
        return np.zeros(n)
    used = lineups[chosen]
    any_count = np.zeros(n, dtype=np.float64)
    for slot in range(used.shape[1]):
        any_count += np.bincount(used[:, slot], minlength=n)
    cpt = np.bincount(used[:, 0], minlength=n).astype(np.float64)
    return (any_count - cpt) / chosen.shape[0] * 100.0


def _draw_field(
    by_cpt, lineups, salaries, mu, weights, cpt_p, k, *, seed, high_salary,
    low_salary, high_salary_rate, captain_mult,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    k = int(k)
    captains = rng.choice(len(cpt_p), size=k, p=cpt_p)
    proj = (mu * weights)[None, :] * np.exp(0.28 * rng.standard_normal((k, mu.shape[0])))
    floors = np.where(rng.random(k) < high_salary_rate, high_salary, low_salary)
    out = np.full(k, -1, dtype=np.int32)
    for captain, members in enumerate(by_cpt):
        entries = np.where(captains == captain)[0]
        if entries.size == 0 or members.size == 0:
            continue
        out[entries] = _best_for_captain(
            lineups, salaries, proj[entries], floors[entries], members, captain_mult
        )
    return out


def _best_for_captain(lineups, salaries, proj, floors, members, captain_mult) -> np.ndarray:
    """Best lineup index per entry. Falls back to the best score if none clear the floor."""
    members = np.asarray(members, dtype=np.int32)
    k = proj.shape[0]
    best_score = np.full(k, -1e18, dtype=np.float64)
    best_index = np.full(k, int(members[0]), dtype=np.int32)
    raw_best = np.full(k, -1e18, dtype=np.float64)
    raw_index = np.full(k, int(members[0]), dtype=np.int32)
    chunk = max(1, int(30_000_000 / (8 * max(k, 1))))
    for start in range(0, members.shape[0], chunk):
        ids = members[start:start + chunk]
        scores = _lineup_proj(proj, lineups[ids], captain_mult)
        local = scores.argmax(axis=1)
        local_score = scores[np.arange(k), local]
        improve_raw = local_score > raw_best
        raw_best = np.where(improve_raw, local_score, raw_best)
        raw_index = np.where(improve_raw, ids[local], raw_index)
        masked = np.where(salaries[ids][None, :] >= floors[:, None], scores, -1e18)
        local = masked.argmax(axis=1)
        local_score = masked[np.arange(k), local]
        improve = local_score > best_score
        best_score = np.where(improve, local_score, best_score)
        best_index = np.where(improve, ids[local], best_index)
    missed = best_score < -1e17
    if np.any(missed):
        best_index = best_index.copy()
        best_index[missed] = raw_index[missed]
    return best_index


def _lineup_proj(proj: np.ndarray, slots: np.ndarray, captain_mult: float) -> np.ndarray:
    scores = np.zeros((proj.shape[0], slots.shape[0]), dtype=np.float64)
    for slot in range(slots.shape[1]):
        mult = captain_mult if slot == 0 else 1.0
        scores += mult * proj[:, slots[:, slot]]
    return scores


def contest_metrics(
    user_scores: np.ndarray,
    field_scores: np.ndarray,
    *,
    contest_size: int,
    entry_fee: float,
    cash_pct: float,
    dupes: float,
    tiers: list[dict],
) -> dict:
    """Top 0.1%, top 1%, ITM, and sim ROI for one lineup against a field sample.

    ``user_scores`` is (sims,). ``field_scores`` is (sims, opponents).
    ROI divides the prize by one plus the expected exact-match dupes.
    """
    contest_size = max(2, int(contest_size))
    spots_01 = max(1, int(math.ceil(contest_size * 0.001 - 1e-12)))
    spots_1 = max(spots_01, int(math.ceil(contest_size * 0.01 - 1e-12)))
    cash_spots = max(1, int(math.ceil(contest_size * float(cash_pct) - 1e-12)))
    ahead = (field_scores > user_scores[:, None]).mean(axis=1) * float(contest_size - 1)
    rank = 1.0 + ahead
    table = _prize_table(contest_size, tiers)
    idx = np.clip(np.floor(rank).astype(np.int32), 1, contest_size)
    prizes = table[idx] / (1.0 + max(0.0, float(dupes)))
    fee = float(entry_fee) if entry_fee else 1.0
    return {
        "top01": round(float((ahead < spots_01).mean() * 100.0), 2),
        "top1": round(float((ahead < spots_1).mean() * 100.0), 2),
        "itm": round(float((ahead < cash_spots).mean() * 100.0), 2),
        "roi": round(float(prizes.mean() / fee - 1.0), 4),
        "mean_score": round(float(user_scores.mean()), 2),
        "dupes": round(float(dupes), 2),
    }


def _prize_table(contest_size: int, tiers: list[dict]) -> np.ndarray:
    table = np.zeros(contest_size + 1, dtype=np.float64)
    for tier in tiers:
        start = max(1, int(tier["start"]))
        end = min(contest_size, int(tier["end"]))
        if end >= start:
            table[start:end + 1] = float(tier["prize"])
    return table


def select_showdown_pool(players: list[SimPlayer], must_ids: set[int], limit: int) -> list[SimPlayer]:
    eligible = [
        p for p in players
        if p.status not in {"OUT", "IR"} and p.salary > 0 and (p.projection >= 1.0 or p.id in must_ids)
    ]
    # SimPlayer has no status in the dataclass. Filter before calling, or ignore.
    return eligible


def run_showdown(
    players: list[SimPlayer],
    lineups: list[SimLineup],
    params: SimParams,
    progress=None,
) -> SimReport:
    def tick(pct: int, message: str) -> None:
        if progress:
            progress(pct, message)

    tick(5, "Building the showdown universe")
    pool = _trim_pool(players, lineups, params.pool_limit)
    if len(pool) < 6:
        raise ValueError("Need at least 6 active players with projections to simulate showdown.")
    arrays = _arrays(pool)
    rng = np.random.default_rng(params.seed)
    tick(12, "Enumerating legal captain lineups")
    L, LS = enumerate_showdown(arrays["salary"], arrays["team_code"])
    if L.shape[0] == 0:
        raise ValueError("No legal showdown lineups under the $50,000 cap.")
    tick(22, f"Simulating {params.n_sims:,} correlated slates")
    samples = simulate_points(
        arrays["mu"], arrays["salary"], arrays["position"], arrays["team_slot"],
        arrays["game_index"], arrays["n_games"], params.n_sims, rng,
    )
    tick(48, "Solving the optimal lineup in each sim")
    best = optimal_indices(samples, L)
    opt_lineups = L[best]
    n = len(pool)
    any_count = np.zeros(n, dtype=np.float64)
    for slot in range(6):
        any_count += np.bincount(opt_lineups[:, slot], minlength=n)
    cpt_count = np.bincount(opt_lineups[:, 0], minlength=n).astype(np.float64)
    any_pct = any_count / params.n_sims * 100.0
    cpt_pct = cpt_count / params.n_sims * 100.0
    player_rows = _player_rows(pool, any_pct, cpt_pct)

    scripts = _captain_scripts(pool, arrays, opt_lineups, params.n_sims) if arrays["n_teams"] <= 2 else []

    tick(62, "Building an optimizer-style field from ownership")
    field_ids = build_optimizer_field(
        L, LS, arrays["mu_raw"], arrays["cpt_own"], arrays["flex_own"],
        pool_size=params.field_pool, seed=params.seed + 17,
        calib_rounds=params.calib_rounds, calib_size=min(params.calib_size, params.field_pool),
    )
    salary_rate = float((LS[field_ids[field_ids >= 0]] >= 49000).mean() * 100.0) if np.any(field_ids >= 0) else 0.0
    lookup = _lineup_lookup(L)
    id_index = {player.id: i for i, player in enumerate(pool)}
    solved = _resolve_lineups(lineups, id_index, lookup)

    tick(78, "Scoring lineups against the field")
    tiers = params.payout_tiers or default_payout_tiers(
        params.contest_size, params.entry_fee, params.cash_pct, params.rake
    )
    field_n = max(1, min(params.field_sample, max(1, params.contest_size - 1)))
    report_lineups = _score_lineups(
        samples, L, LS, solved, field_ids, field_n, params, tiers, rng, captain_mult=1.5
    )
    tick(100, "Done")
    return SimReport(
        disclaimer=DISCLAIMER,
        format="showdown",
        n_sims=params.n_sims,
        optimal_method="exact_enumeration",
        players=player_rows,
        scripts=scripts,
        lineups=report_lineups,
        field={
            "pool": int(np.sum(field_ids >= 0)),
            "sample": field_n,
            "contest_size": params.contest_size,
            "entry_fee": params.entry_fee,
            "max_entries": params.max_entries,
            "cash_pct": params.cash_pct,
            "pct_salary_49k": round(salary_rate, 1),
            "legal_lineups": int(L.shape[0]),
        },
        payout_tiers=tiers,
        pool_names=[player.name for player in pool],
    )


def run_classic(
    players: list[SimPlayer],
    candidates: list[SimLineup],
    lineups: list[SimLineup],
    params: SimParams,
    salaries_of: dict[str, int],
    progress=None,
) -> SimReport:
    """Classic optimal is the best optimizer candidate in each sim, not a full enumeration."""

    def tick(pct: int, message: str) -> None:
        if progress:
            progress(pct, message)

    tick(8, "Simulating correlated classic slates")
    if len(candidates) < 1:
        raise ValueError("Classic sims need at least one lineup. Generate lineups first.")
    pool = _ordered_pool(players, candidates + lineups)
    arrays = _arrays(pool)
    rng = np.random.default_rng(params.seed)
    samples = simulate_points(
        arrays["mu"], arrays["salary"], arrays["position"], arrays["team_slot"],
        arrays["game_index"], arrays["n_games"], params.n_sims, rng,
    )
    id_index = {player.id: i for i, player in enumerate(pool)}
    cand_idx = []
    cand_sal = []
    for cand in candidates:
        idxs = [id_index[pid] for pid in cand.player_ids if pid in id_index]
        if len(idxs) != len(cand.player_ids):
            continue
        cand_idx.append(idxs)
        cand_sal.append(salaries_of.get(cand.key, 0))
    C = np.asarray(cand_idx, dtype=np.int16)
    CS = np.asarray(cand_sal, dtype=np.int32)
    tick(40, "Scoring optimizer candidates in each sim")
    cand_scores = np.zeros((params.n_sims, C.shape[0]), dtype=np.float32)
    for slot in range(C.shape[1]):
        cand_scores += samples[:, C[:, slot]]
    best = cand_scores.argmax(axis=1)
    any_count = np.zeros(len(pool), dtype=np.float64)
    chosen = C[best]
    for slot in range(chosen.shape[1]):
        any_count += np.bincount(chosen[:, slot], minlength=len(pool))
    any_pct = any_count / params.n_sims * 100.0
    player_rows = _player_rows(pool, any_pct, np.zeros(len(pool)))
    tick(60, "Building an optimizer-style classic field")
    field_ids = _classic_field(C, CS, arrays["mu_raw"], arrays["flex_own"], params)
    known = field_ids[field_ids >= 0]
    salary_rate = float((CS[known] >= 49000).mean() * 100.0) if known.size else 0.0
    solved = []
    cand_lookup = {}
    for i, cand in enumerate(candidates):
        if i >= C.shape[0]:
            break
        cand_lookup[tuple(cand.player_ids)] = i
    for lu in lineups:
        solved.append({
            "lineup": lu,
            "index": cand_lookup.get(tuple(lu.player_ids), -1),
            "slots": [id_index[pid] for pid in lu.player_ids],
        })
    # Also rank the candidate completions themselves.
    for i, cand in enumerate(candidates):
        if any(item["lineup"].key == cand.key for item in solved):
            continue
        solved.append({"lineup": cand, "index": i, "slots": C[i].tolist()})
    tick(80, "Scoring lineups against the field")
    tiers = params.payout_tiers or default_payout_tiers(
        params.contest_size, params.entry_fee, params.cash_pct, params.rake
    )
    field_n = max(1, min(params.field_sample, max(1, params.contest_size - 1)))
    # Reuse showdown scoring by wrapping candidates as "lineups" with no captain multiplier.
    fake_L = C
    report_lineups = _score_lineups(
        samples, fake_L, CS, solved, field_ids, field_n, params, tiers, rng, captain_mult=1.0
    )
    tick(100, "Done")
    return SimReport(
        disclaimer=DISCLAIMER,
        format="classic",
        n_sims=params.n_sims,
        optimal_method="optimizer_candidates",
        players=player_rows,
        scripts=[],
        lineups=report_lineups,
        field={
            "pool": int(known.size),
            "sample": field_n,
            "contest_size": params.contest_size,
            "entry_fee": params.entry_fee,
            "max_entries": params.max_entries,
            "cash_pct": params.cash_pct,
            "pct_salary_49k": round(salary_rate, 1),
            "legal_lineups": int(C.shape[0]),
        },
        payout_tiers=tiers,
        pool_names=[player.name for player in pool],
    )


def _classic_field(C, CS, mu, own, params: SimParams) -> np.ndarray:
    """Noisy max-projection picks, salary floor, weights calibrated to ownership."""
    rng = np.random.default_rng(params.seed + 3)
    n = mu.shape[0]
    target = np.maximum(own, 0.0)
    if target.sum() <= 0:
        target = np.ones(n)
    target = target / target.sum() * 900.0
    weights = np.ones(n)
    best_w = weights.copy()
    best_err = 1e18

    def draw(k, w, seed):
        local = np.random.default_rng(seed)
        proj = (mu * w) * np.exp(0.28 * local.standard_normal((k, n)))
        scores = np.zeros((k, C.shape[0]), dtype=np.float64)
        for slot in range(C.shape[1]):
            scores += proj[:, C[:, slot]]
        floors = np.where(local.random(k) < 0.85, 49000, 46500)
        masked = np.where(CS[None, :] >= floors[:, None], scores, -1e18)
        pick = masked.argmax(axis=1)
        missed = masked[np.arange(k), pick] < -1e17
        if np.any(missed):
            pick = pick.copy()
            pick[missed] = scores[missed].argmax(axis=1)
        return pick.astype(np.int32)

    for step in range(max(0, params.calib_rounds)):
        sample = draw(min(params.calib_size, 800), weights, 50 + step)
        expo = np.zeros(n)
        used = C[sample]
        for slot in range(used.shape[1]):
            expo += np.bincount(used[:, slot], minlength=n)
        expo = expo / sample.shape[0] * 100.0
        err = float(np.abs(expo - target).sum())
        if err < best_err:
            best_err = err
            best_w = weights.copy()
        weights = weights * np.clip((np.maximum(target, 0.3) / np.maximum(expo, 0.3)) ** 0.25, 0.8, 1.25)
    if params.calib_rounds:
        weights = best_w
    return draw(params.field_pool, weights, params.seed + 9)


def _trim_pool(players: list[SimPlayer], lineups: list[SimLineup], limit: int) -> list[SimPlayer]:
    must = {pid for lu in lineups for pid in lu.player_ids}
    active = [p for p in players if p.salary > 0 and p.projection > 0]
    ranked = sorted(active, key=lambda p: p.projection * max(p.boost, 0.01), reverse=True)
    kept: list[SimPlayer] = []
    seen = set()
    for player in ranked:
        if player.id in must or len(kept) < limit:
            if player.id not in seen:
                kept.append(player)
                seen.add(player.id)
    if len(kept) < 6:
        kept = ranked[: max(6, limit)]
    return kept


def _ordered_pool(players: list[SimPlayer], lineups: list[SimLineup]) -> list[SimPlayer]:
    must = {pid for lu in lineups for pid in lu.player_ids}
    return [p for p in players if p.id in must or p.projection > 0]


def _arrays(pool: list[SimPlayer]) -> dict:
    teams = []
    for player in pool:
        if player.team not in teams:
            teams.append(player.team)
    # Game groups from team/opponent pairs.
    games: list[tuple[str, str]] = []
    game_of: dict[str, int] = {}
    for player in pool:
        pair = tuple(sorted((player.team, player.opponent or player.team)))
        if pair not in games:
            games.append(pair)
        game_of[player.team] = games.index(pair)
    team_code = []
    team_slot = []
    game_index = []
    for player in pool:
        pair = games[game_of[player.team]]
        team_code.append(teams.index(player.team))
        team_slot.append(0 if player.team == pair[0] else 1)
        game_index.append(game_of[player.team])
    mu_raw = np.array([max(0.05, p.projection) for p in pool], dtype=np.float64)
    boost = np.array([max(0.0, p.boost) for p in pool], dtype=np.float64)
    return {
        "salary": np.array([p.salary for p in pool], dtype=np.int32),
        "team_code": np.array(team_code, dtype=np.int16),
        "team_slot": np.array(team_slot, dtype=np.int64),
        "game_index": np.array(game_index, dtype=np.int64),
        "n_games": len(games),
        "n_teams": len({p.team for p in pool}),
        "position": [p.position for p in pool],
        "mu": mu_raw * np.maximum(boost, 0.05),
        "mu_raw": mu_raw,
        "cpt_own": np.array([p.cpt_ownership for p in pool], dtype=np.float64),
        "flex_own": np.array([p.ownership for p in pool], dtype=np.float64),
        "team_names": [pair[0] for pair in games] if len(games) == 1 else [],
        "games": games,
    }


def _player_rows(pool: list[SimPlayer], any_pct: np.ndarray, cpt_pct: np.ndarray) -> list[dict]:
    rows = []
    for i, player in enumerate(pool):
        opt = float(any_pct[i])
        cpt = float(cpt_pct[i])
        rows.append({
            "id": player.id,
            "name": player.name,
            "team": player.team,
            "position": player.position,
            "salary": player.salary,
            "projection": round(player.projection, 2),
            "ownership": round(player.ownership, 2),
            "cpt_ownership": round(player.cpt_ownership, 2),
            "opt": round(opt, 2),
            "cpt_opt": round(cpt, 2),
            "leverage": round(opt - player.ownership, 2),
            "cpt_leverage": round(cpt - player.cpt_ownership, 2),
            "value": round(player.projection / (player.salary / 1000.0), 2) if player.salary else 0,
        })
    rows.sort(key=lambda row: -row["opt"])
    return rows


def _captain_scripts(pool, arrays, opt_lineups, n_sims: int) -> list[dict]:
    games = arrays["games"]
    if len(games) != 1:
        return []
    side_a, side_b = games[0]
    # team_slot 0 is side_a (the sorted-first team).
    flex_slots = opt_lineups[:, 1:]
    slot_of = arrays["team_slot"]
    n_a = (slot_of[flex_slots] == 0).sum(axis=1)
    counts: Counter[str] = Counter()
    names = [p.name for p in pool]
    captains = opt_lineups[:, 0]
    for i in range(n_sims):
        label = f"{names[int(captains[i])]} CPT + {int(n_a[i])} {side_a}/{5 - int(n_a[i])} {side_b} flex"
        counts[label] += 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
    return [{"label": label, "pct": round(count / n_sims * 100.0, 2)} for label, count in ranked]


def _lineup_lookup(lineups: np.ndarray) -> dict[tuple[int, ...], int]:
    lookup = {}
    for i in range(lineups.shape[0]):
        captain = int(lineups[i, 0])
        flex = tuple(sorted(int(x) for x in lineups[i, 1:]))
        lookup[(captain, *flex)] = i
    return lookup


def _resolve_lineups(lineups: list[SimLineup], id_index: dict[int, int], lookup: dict) -> list[dict]:
    solved = []
    for lu in lineups:
        try:
            idxs = [id_index[pid] for pid in lu.player_ids]
        except KeyError:
            continue
        if len(idxs) == 6:
            key = (idxs[0], *tuple(sorted(idxs[1:])))
            index = lookup.get(key, -1)
        else:
            index = -1
        solved.append({"lineup": lu, "index": index, "slots": idxs})
    return solved


def _score_lineups(samples, L, LS, solved, field_ids, field_n, params, tiers, rng, captain_mult: float) -> list[dict]:
    valid_field = field_ids[field_ids >= 0]
    if valid_field.size == 0:
        valid_field = np.arange(L.shape[0], dtype=np.int32)
    draw = rng.choice(valid_field, size=(params.n_sims, field_n), replace=True)
    field_scores = _score_selected(samples, L, draw, captain_mult)
    dupe_ids = np.array([item["index"] for item in solved], dtype=np.int64)
    dupe_counts = expected_dupes(valid_field, dupe_ids, params.contest_size)
    rows = []
    for i, item in enumerate(solved):
        user_scores = _score_custom(samples, item["slots"], captain_mult)
        metrics = contest_metrics(
            user_scores, field_scores,
            contest_size=params.contest_size,
            entry_fee=params.entry_fee,
            cash_pct=params.cash_pct,
            dupes=float(dupe_counts[i]),
            tiers=tiers,
        )
        lu: SimLineup = item["lineup"]
        rows.append({
            "key": lu.key,
            "source": lu.source,
            "player_ids": list(lu.player_ids),
            **metrics,
        })
    rows.sort(key=lambda row: (-row["top01"], -row["roi"]))
    return rows


def _score_custom(samples: np.ndarray, slots: list[int], captain_mult: float) -> np.ndarray:
    scores = np.zeros(samples.shape[0], dtype=np.float32)
    for i, idx in enumerate(slots):
        mult = captain_mult if i == 0 else 1.0
        scores += np.float32(mult) * samples[:, idx]
    return scores
