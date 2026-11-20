"""DraftKings classic and showdown generation on pydfs-lineup-optimizer 3.6.1.

That release is the last one on PyPI and it pins PuLP 2.4. A few behaviors
need a local patch when the slate is NFL showdown or the rule is cumulative
ownership:

* ``randomness=True`` is deprecated inside 3.6 ("will be removed in 3.6") and
  is not called. Diversity uses ``RandomFantasyPointsStrategy``.
* Captain mode only rosters CPT and FLEX. Football position is stored on
  ``original_positions`` and enforced with an ``OptimizerRule``.
* ``set_projected_ownership`` treats the bounds as an average and divides
  values above 1 by 100, so a 160–210% cumulative target is a direct sum
  constraint instead.
* Showdown inserts two ``Player`` objects per person (CPT salary is 1.5x).
  Exposure caps use a ``PlayersGroup`` over both copies so the cap is on the
  person, which is what a lineup builder means by exposure.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from pydfs_lineup_optimizer import Player, PlayersGroup, get_optimizer
from pydfs_lineup_optimizer.constants import Site, Sport
from pydfs_lineup_optimizer.exceptions import GenerateLineupException, LineupOptimizerException
from pydfs_lineup_optimizer.fantasy_points_strategy import RandomFantasyPointsStrategy
from pydfs_lineup_optimizer.player import GameInfo
from pydfs_lineup_optimizer.rules import OptimizerRule
from pydfs_lineup_optimizer.solvers import SolverSign

from simlab.services.names import normalize_position, split_name

CAP = 50000
PASS_CATCHERS = {"WR", "TE"}
SKILL = {"WR", "TE", "RB"}


@dataclass
class OptPlayer:
    id: int
    name: str
    team: str
    opponent: str
    position: str
    salary: int
    projection: float
    ownership: float
    cpt_ownership: float
    dk_id: str = ""
    cpt_dk_id: str = ""
    boost: float = 1.0
    min_exposure: float = 0.0
    max_exposure: float = 1.0
    lock: str = "none"
    excluded: bool = False
    status: str = ""


@dataclass
class OptRules:
    n: int = 4
    qb_stack: int = 1
    bring_back: int = 1
    no_naked_pass_catchers: bool = True
    captain_ids: list[int] = field(default_factory=list)
    only_with: list[tuple[int, int]] = field(default_factory=list)
    max_shared: int | None = 4
    max_per_captain: int | None = None
    min_salary: int | None = 46000
    max_salary: int | None = 50000
    min_own: float | None = 160.0
    max_own: float | None = 210.0
    randomness: bool = False


@dataclass
class BuiltSlot:
    slot: str
    player_id: int
    name: str
    team: str
    position: str
    salary: int
    projection: float
    ownership: float
    dk_id: str


@dataclass
class BuiltLineup:
    slots: list[BuiltSlot]
    salary: int
    projection: float
    ownership: float

    @property
    def player_ids(self) -> list[int]:
        return [slot.player_id for slot in self.slots]


@dataclass
class GenerateResult:
    lineups: list[BuiltLineup]
    warning: str | None = None


@dataclass
class LabSpec:
    """Read by the custom rule during solve. Attached to the optimizer."""

    showdown: bool
    qb_stack: int
    bring_back: int
    no_naked_pass_catchers: bool
    only_with: list[tuple[str, str]]
    min_own: float | None
    max_own: float | None
    max_salary: int | None
    max_shared: int | None
    max_per_captain: int | None
    opponents: dict[str, str]
    min_exposure: dict[str, float]


class LabConstraints(OptimizerRule):
    """Stacks, bring-backs, only-with, naked pass-catchers, ownership, salary cap."""

    def apply(self, solver):
        spec: LabSpec = self.optimizer.lab_spec
        by_name: dict[str, list] = defaultdict(list)
        by_team: dict[str, list[tuple]] = defaultdict(list)
        for player, variable in self.players_dict.items():
            by_name[player.full_name].append(variable)
            by_team[player.team].append((player, variable))

        own_vars = []
        own_coef = []
        sal_vars = []
        sal_coef = []
        for player, variable in self.players_dict.items():
            own_vars.append(variable)
            own_coef.append(float(player.projected_ownership or 0.0))
            sal_vars.append(variable)
            sal_coef.append(float(player.salary))
        if spec.min_own is not None:
            solver.add_constraint(own_vars, own_coef, SolverSign.GTE, float(spec.min_own), name="cum_own_min")
        if spec.max_own is not None:
            solver.add_constraint(own_vars, own_coef, SolverSign.LTE, float(spec.max_own), name="cum_own_max")
        if spec.max_salary is not None and spec.max_salary < CAP:
            solver.add_constraint(sal_vars, sal_coef, SolverSign.LTE, float(spec.max_salary), name="max_salary")

        for index, (player_name, with_name) in enumerate(spec.only_with):
            left = by_name.get(player_name) or []
            right = by_name.get(with_name) or []
            if not left:
                continue
            if not right:
                solver.add_constraint(left, None, SolverSign.EQ, 0, name=f"only_with_block_{index}")
                continue
            solver.add_constraint(
                left + right,
                [1.0] * len(left) + [-1.0] * len(right),
                SolverSign.LTE,
                0,
                name=f"only_with_{index}",
            )

        for team, members in by_team.items():
            pcs = [var for player, var in members if _football_pos(player) in PASS_CATCHERS]
            qbs = [var for player, var in members if _football_pos(player) == "QB"]
            if spec.no_naked_pass_catchers and pcs:
                if not qbs:
                    solver.add_constraint(pcs, None, SolverSign.LTE, 1, name=f"naked_{team}")
                else:
                    width = float(len(pcs))
                    for q_index, qb_var in enumerate(qbs):
                        solver.add_constraint(
                            pcs + [qb_var],
                            [1.0] * len(pcs) + [-width],
                            SolverSign.LTE,
                            1,
                            name=f"naked_{team}_{q_index}",
                        )
            if spec.qb_stack > 0 and qbs and pcs:
                for q_index, qb_var in enumerate(qbs):
                    solver.add_constraint(
                        pcs + [qb_var],
                        [1.0] * len(pcs) + [-float(spec.qb_stack)],
                        SolverSign.GTE,
                        0,
                        name=f"stack_{team}_{q_index}",
                    )
            if spec.bring_back > 0 and qbs:
                opp = spec.opponents.get(team)
                if not opp:
                    continue
                opp_skill = [
                    var
                    for player, var in by_team.get(opp, [])
                    if _football_pos(player) in SKILL
                ]
                if not opp_skill:
                    continue
                for q_index, qb_var in enumerate(qbs):
                    solver.add_constraint(
                        opp_skill + [qb_var],
                        [1.0] * len(opp_skill) + [-float(spec.bring_back)],
                        SolverSign.GTE,
                        0,
                        name=f"bring_{team}_{q_index}",
                    )


class MaxSharedPeopleRule(OptimizerRule):
    """Cap how many people (not CPT/FLEX copies) a lineup shares with earlier ones.

    pydfs' own repeating-player rule counts ``Player`` objects. Showdown creates
    two of those per person, so a captain in one lineup and a flex in the next
    would not count as shared. This rule sums each person's copies instead.
    """

    def __init__(self, optimizer, players_dict, context):
        super().__init__(optimizer, players_dict, context)
        self.limit = optimizer.lab_spec.max_shared
        self.by_name: dict[str, list] = defaultdict(list)
        for player, variable in players_dict.items():
            self.by_name[player.full_name].append(variable)
        self.previous: list[tuple[str, ...]] = []

    def apply_for_iteration(self, solver, result):
        if self.limit is None:
            return
        if result:
            self.previous.append(tuple(sorted({player.full_name for player in result})))
        for index, names in enumerate(self.previous):
            variables = []
            for name in names:
                variables.extend(self.by_name.get(name, []))
            if variables:
                solver.add_constraint(
                    variables, None, SolverSign.LTE, int(self.limit), name=f"shared_people_{index}"
                )


class CaptainDiversityRule(OptimizerRule):
    """Stop one showdown captain from filling every lineup."""

    def __init__(self, optimizer, players_dict, context):
        super().__init__(optimizer, players_dict, context)
        self.limit = optimizer.lab_spec.max_per_captain
        self.cpt = {
            player.full_name: variable
            for player, variable in players_dict.items()
            if "CPT" in player.positions
        }
        self.used: dict[str, int] = defaultdict(int)

    def apply_for_iteration(self, solver, result):
        if not self.limit:
            return
        if result:
            for player in result:
                if "CPT" in player.positions:
                    self.used[player.full_name] += 1
        for index, (name, count) in enumerate(self.used.items()):
            variable = self.cpt.get(name)
            if variable is not None and count >= self.limit:
                solver.add_constraint([variable], None, SolverSign.EQ, 0, name=f"cpt_cap_{index}")


class PersonMinExposureRule(OptimizerRule):
    """Force a showdown person into enough remaining lineups to hit a minimum."""

    def __init__(self, optimizer, players_dict, context):
        super().__init__(optimizer, players_dict, context)
        spec: LabSpec = optimizer.lab_spec
        self.total = max(1, context.total_lineups)
        self.need: dict[str, int] = {}
        self.vars: dict[str, list] = {}
        self.used: dict[str, int] = defaultdict(int)
        grouped: dict[str, list] = defaultdict(list)
        for player, variable in players_dict.items():
            grouped[player.full_name].append(variable)
        for name, frac in spec.min_exposure.items():
            if frac <= 0 or name not in grouped:
                continue
            count = int(math.ceil(frac * self.total - 1e-9))
            if count > 0:
                self.need[name] = count
                self.vars[name] = grouped[name]

    def apply_for_iteration(self, solver, result):
        if not self.need:
            return
        if result:
            seen = set()
            for player in result:
                if player.full_name in self.need and player.full_name not in seen:
                    self.used[player.full_name] += 1
                    seen.add(player.full_name)
        remaining = self.context.remaining_lineups
        for index, (name, count) in enumerate(self.need.items()):
            left = count - self.used[name]
            if left > 0 and left >= remaining:
                solver.add_constraint(self.vars[name], None, SolverSign.GTE, 1, name=f"min_exp_{index}")


def _football_pos(player: Player) -> str:
    original = player.original_positions or player.positions
    return normalize_position(original[0] if original else "")


def _full_name(name: str) -> str:
    first, last = split_name(name)
    return f"{first} {last}" if last else f"{first} "


def generate_lineups(players: list[OptPlayer], rules: OptRules, showdown: bool) -> GenerateResult:
    active = [
        player
        for player in players
        if not player.excluded
        and player.max_exposure > 0
        and player.status not in {"OUT", "IR"}
        and player.salary > 0
    ]
    if showdown and len(active) < 6:
        raise LineupOptimizerException("Need at least 6 active players to build a showdown lineup.")
    if not showdown and len(active) < 9:
        raise LineupOptimizerException("Need at least 9 active players to build a classic lineup.")

    captain_ids = set(rules.captain_ids)
    for player in active:
        if player.lock == "cpt":
            captain_ids.add(player.id)

    site = Site.DRAFTKINGS_CAPTAIN_MODE if showdown else Site.DRAFTKINGS
    optimizer = get_optimizer(site, Sport.FOOTBALL)
    if not showdown:
        # Classic settings ask for 2 games. A one-game upload should still solve.
        games = {(p.team, p.opponent) for p in active if p.opponent}
        if len(games) < 2:
            optimizer.settings.min_games = None

    game_infos: dict[tuple[str, str], GameInfo] = {}
    pydfs_players: list[Player] = []
    by_pid_slot: dict[tuple[int, str], Player] = {}
    id_owner: dict[str, OptPlayer] = {}

    for player in active:
        info = _game_info(player, game_infos)
        first, last = split_name(player.name)
        base = max(0.0, player.projection) * max(0.0, player.boost)
        if showdown:
            copies = (
                ("CPT", f"c{player.id}", int(round(player.salary * 1.5)), base * 1.5, player.cpt_ownership),
                ("FLEX", f"f{player.id}", int(player.salary), base, player.ownership),
            )
        else:
            copies = (("ROSTER", f"r{player.id}", int(player.salary), base, player.ownership),)
        for slot, pid, salary, fppg, own in copies:
            obj = Player(
                pid,
                first,
                last,
                [slot] if showdown else [player.position],
                player.team,
                salary,
                fppg,
                projected_ownership=own,
                game_info=info,
                original_positions=[player.position],
            )
            if not showdown:
                cap = player.max_exposure
                if player.lock == "roster":
                    cap = 1.0
                if cap < 0.999:
                    obj.max_exposure = max(0.0, cap)
                if player.min_exposure > 0 and player.lock != "roster":
                    obj.min_exposure = min(1.0, player.min_exposure)
            pydfs_players.append(obj)
            by_pid_slot[(player.id, slot)] = obj
            id_owner[pid] = player

    optimizer.player_pool.load_players(pydfs_players)
    if showdown:
        try:
            optimizer.set_total_teams(min_teams=2)
        except LineupOptimizerException:
            pass

    roster_size = 6 if showdown else 9
    shared_limit = rules.max_shared
    if shared_limit is None or shared_limit >= roster_size:
        shared_limit = roster_size - 1
    if rules.min_salary and rules.min_salary > 0:
        optimizer.set_min_salary_cap(float(rules.min_salary))
    if rules.randomness:
        optimizer.set_fantasy_points_strategy(RandomFantasyPointsStrategy(0.04, 0.2))

    name_of = {player.id: _full_name(player.name) for player in active}
    only_with = []
    for player_id, with_id in rules.only_with:
        if player_id in name_of and with_id in name_of:
            only_with.append((name_of[player_id], name_of[with_id]))

    opponents = {}
    for player in active:
        if player.opponent:
            opponents[player.team] = player.opponent

    min_exposure = {}
    if showdown:
        for player in active:
            if player.min_exposure > 0 and player.lock not in {"cpt", "flex"}:
                min_exposure[_full_name(player.name)] = min(1.0, player.min_exposure)
        _lock_showdown(optimizer, active, by_pid_slot, captain_ids)
        _add_showdown_exposure_groups(optimizer, active, by_pid_slot, captain_ids)
    else:
        for player in active:
            obj = by_pid_slot[(player.id, "ROSTER")]
            if player.lock == "roster":
                optimizer.player_pool.lock_player(obj)
            elif player.excluded:
                optimizer.player_pool.remove_player(obj)

    optimizer.lab_spec = LabSpec(
        showdown=showdown,
        qb_stack=int(rules.qb_stack or 0),
        bring_back=int(rules.bring_back or 0),
        no_naked_pass_catchers=bool(rules.no_naked_pass_catchers),
        only_with=only_with,
        min_own=rules.min_own,
        max_own=rules.max_own,
        max_salary=rules.max_salary,
        max_shared=shared_limit,
        max_per_captain=rules.max_per_captain if showdown else None,
        opponents=opponents,
        min_exposure=min_exposure,
    )
    optimizer.add_new_rule(LabConstraints)
    optimizer.add_new_rule(MaxSharedPeopleRule)
    if showdown and rules.max_per_captain:
        optimizer.add_new_rule(CaptainDiversityRule)
    if showdown and min_exposure:
        optimizer.add_new_rule(PersonMinExposureRule)

    built: list[BuiltLineup] = []
    warning = None
    try:
        for lineup in optimizer.optimize(max(1, int(rules.n))):
            built.append(_export_lineup(lineup, id_owner, showdown))
    except GenerateLineupException as exc:
        warning = str(exc)
        if not built:
            raise LineupOptimizerException(warning) from exc
    except LineupOptimizerException:
        raise
    if len(built) < rules.n and warning is None:
        warning = f"Built {len(built)} of {rules.n} lineups before the rules ran out of solutions."
    return GenerateResult(lineups=built, warning=warning)


def _add_showdown_exposure_groups(optimizer, players: list[OptPlayer], by_slot, captain_ids: set[int]) -> None:
    """Cap CPT+FLEX appearances together via PlayersGroup.max_exposure.

    Groups are added after disallowed captain copies are removed, so every
    player in the group is still in the solver.
    """
    removed = optimizer.player_pool.removed_players
    for player in players:
        if player.lock in {"cpt", "flex"}:
            continue
        cap = player.max_exposure
        if cap >= 0.999:
            continue
        copies = []
        cpt = by_slot[(player.id, "CPT")]
        flex = by_slot[(player.id, "FLEX")]
        if cpt not in removed and (not captain_ids or player.id in captain_ids):
            copies.append(cpt)
        if flex not in removed:
            copies.append(flex)
        if not copies:
            continue
        optimizer.add_players_group(
            PlayersGroup(
                players=copies,
                min_from_group=None,
                max_from_group=1,
                max_exposure=max(0.01, min(0.99, cap)),
            )
        )


def _lock_showdown(optimizer, players: list[OptPlayer], by_slot, captain_ids: set[int]) -> None:
    allowed = []
    for player in players:
        cpt = by_slot[(player.id, "CPT")]
        flex = by_slot[(player.id, "FLEX")]
        if captain_ids and player.id not in captain_ids:
            optimizer.player_pool.remove_player(cpt)
        else:
            allowed.append(cpt)
        if player.lock == "cpt":
            optimizer.player_pool.lock_player(cpt, "CPT")
        elif player.lock == "flex":
            optimizer.player_pool.lock_player(flex, "FLEX")
    if captain_ids and allowed:
        # Exactly one CPT, and it has to come from the allow-list. The CPT
        # objects are only eligible for the CPT slot, so this group is the captain.
        optimizer.add_players_group(PlayersGroup(players=allowed, min_from_group=1, max_from_group=1))


def _game_info(player: OptPlayer, cache: dict[tuple[str, str], GameInfo]) -> GameInfo | None:
    if not player.opponent:
        return None
    key = tuple(sorted((player.team, player.opponent)))
    if key not in cache:
        cache[key] = GameInfo(home_team=key[1], away_team=key[0], starts_at=None)
    return cache[key]


def _export_lineup(lineup, id_owner: dict[str, OptPlayer], showdown: bool) -> BuiltLineup:
    slots: list[BuiltSlot] = []
    for lp in lineup:
        owner = id_owner[lp.id]
        slot_name = lp.lineup_position
        if showdown and slot_name == "CPT":
            salary = int(round(owner.salary * 1.5))
            projection = owner.projection * 1.5
            ownership = owner.cpt_ownership
            dk_id = owner.cpt_dk_id or owner.dk_id
        else:
            salary = int(owner.salary)
            projection = owner.projection
            ownership = owner.ownership
            dk_id = owner.dk_id
        slots.append(
            BuiltSlot(
                slot=slot_name if showdown else slot_name,
                player_id=owner.id,
                name=owner.name,
                team=owner.team,
                position=owner.position,
                salary=salary,
                projection=round(projection, 2),
                ownership=round(ownership, 2),
                dk_id=dk_id,
            )
        )
    if showdown:
        slots.sort(key=lambda slot: (slot.slot != "CPT", -slot.salary, slot.name))
    return BuiltLineup(
        slots=slots,
        salary=sum(slot.salary for slot in slots),
        projection=round(sum(slot.projection for slot in slots), 2),
        ownership=round(sum(slot.ownership for slot in slots), 2),
    )


def lineup_signature(player_ids: list[int], showdown: bool) -> tuple:
    if showdown and player_ids:
        return (player_ids[0], tuple(sorted(player_ids[1:])))
    return tuple(sorted(player_ids))


def validate_manual(players: dict[int, OptPlayer], slots: list[tuple[int, str]], showdown: bool) -> BuiltLineup:
    """Accept a hand-edited DK-legal roster. Stack rules are not re-imposed."""
    if showdown:
        if len(slots) != 6:
            raise LineupOptimizerException("A showdown lineup needs a captain and five flex players.")
        if sum(1 for _pid, slot in slots if slot == "CPT") != 1:
            raise LineupOptimizerException("Pick exactly one captain.")
    else:
        if len(slots) != 9:
            raise LineupOptimizerException("A classic lineup needs 9 players.")
    ids = [pid for pid, _slot in slots]
    if len(set(ids)) != len(ids):
        raise LineupOptimizerException("A player can only appear once.")
    built_slots: list[BuiltSlot] = []
    teams = set()
    for pid, slot in slots:
        player = players.get(pid)
        if player is None:
            raise LineupOptimizerException("That player is not on this slate.")
        if player.status in {"OUT", "IR"}:
            raise LineupOptimizerException(f"{player.name} is {player.status}.")
        teams.add(player.team)
        if showdown and slot == "CPT":
            salary = int(round(player.salary * 1.5))
            projection = player.projection * 1.5
            ownership = player.cpt_ownership
            dk_id = player.cpt_dk_id or player.dk_id
        else:
            salary = int(player.salary)
            projection = player.projection
            ownership = player.ownership
            dk_id = player.dk_id
        built_slots.append(
            BuiltSlot(slot, player.id, player.name, player.team, player.position, salary,
                      round(projection, 2), round(ownership, 2), dk_id)
        )
    if len(teams) < 2:
        raise LineupOptimizerException("Use players from at least two teams.")
    if not showdown and not _classic_shape_ok(built_slots):
        raise LineupOptimizerException("Classic roster must be QB, 2 RB, 3 WR, TE, FLEX, DST.")
    total_salary = sum(slot.salary for slot in built_slots)
    if total_salary > CAP:
        raise LineupOptimizerException(f"Salary ${total_salary:,} is over the $50,000 cap.")
    if showdown:
        built_slots.sort(key=lambda slot: (slot.slot != "CPT", -slot.salary, slot.name))
    return BuiltLineup(
        slots=built_slots,
        salary=total_salary,
        projection=round(sum(slot.projection for slot in built_slots), 2),
        ownership=round(sum(slot.ownership for slot in built_slots), 2),
    )


def _classic_shape_ok(slots: list[BuiltSlot]) -> bool:
    counts: dict[str, int] = defaultdict(int)
    for slot in slots:
        counts[slot.position] += 1
    skill = counts["RB"] + counts["WR"] + counts["TE"]
    return (
        counts["QB"] == 1
        and counts["DST"] == 1
        and counts["RB"] >= 2
        and counts["WR"] >= 3
        and counts["TE"] >= 1
        and skill == 7
        and len(slots) == 9
    )
