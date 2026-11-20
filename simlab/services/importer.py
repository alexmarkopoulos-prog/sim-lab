"""DraftKings salary CSV and projection CSV import."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from simlab.services.names import (
    NICKNAMES,
    is_dst,
    name_key,
    normalize_position,
    normalize_team,
)


@dataclass
class ImportedPlayer:
    name: str
    team: str
    opponent: str
    position: str
    salary: int
    projection: float
    ownership: float
    cpt_ownership: float
    dk_id: str
    cpt_dk_id: str
    status: str
    avg_points: float
    game: str


@dataclass
class ImportedSlate:
    name: str
    format: str
    game: str
    kickoff: str = ""
    players: list[ImportedPlayer] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _rows(text: str) -> list[dict[str, str]]:
    sample = text.lstrip("\ufeff")
    return list(csv.DictReader(io.StringIO(sample)))


def _num(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    raw = str(value).strip().replace("%", "").replace(",", "").replace("$", "")
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_game_info(value: str) -> tuple[str, str, str]:
    """Return away, home, label from a DraftKings Game Info cell."""
    text = (value or "").strip()
    head = text.split()[0] if text else ""
    if "@" in head:
        away, home = head.split("@", 1)
        away, home = normalize_team(away), normalize_team(home)
        return away, home, f"{away}@{home}"
    return "", "", head


def _status(value: str) -> str:
    text = (value or "").strip().upper()
    if text in {"O", "OUT"}:
        return "OUT"
    if text in {"IR", "IL"}:
        return "IR"
    if text in {"Q", "QUESTIONABLE"}:
        return "Q"
    if text in {"D", "DOUBTFUL"}:
        return "D"
    return text


def parse_dk_salary(text: str) -> ImportedSlate:
    rows = _rows(text)
    if not rows:
        raise ValueError("DraftKings salary file is empty.")
    required = {"Name", "Salary", "TeamAbbrev", "Position"}
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(
            "This does not look like a DraftKings salary CSV. Missing columns: "
            + ", ".join(sorted(missing))
        )
    showdown = any((row.get("Roster Position") or "").strip().upper() == "CPT" for row in rows)
    games: set[str] = set()
    kickoff = ""
    grouped: dict[str, dict] = {}
    for row in rows:
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        team = normalize_team(row.get("TeamAbbrev") or "")
        pos = normalize_position(row.get("Position") or "")
        roster = (row.get("Roster Position") or pos).strip().upper()
        game_info = row.get("Game Info") or ""
        away, home, label = parse_game_info(game_info)
        if label:
            games.add(label)
        if game_info and not kickoff:
            kickoff = " ".join(game_info.split()[1:])
        opponent = ""
        if team and away and home:
            opponent = home if team == away else away
        key = name_key(name) + "|" + team
        slot = grouped.get(key)
        if slot is None:
            slot = {
                "name": name,
                "team": team,
                "opponent": opponent,
                "position": pos,
                "salary": 0,
                "dk_id": "",
                "cpt_dk_id": "",
                "status": "",
                "avg_points": _num(row.get("AvgPointsPerGame")),
                "game": label,
            }
            grouped[key] = slot
        status = _status(row.get("Status") or "")
        if status in {"OUT", "IR"} or (status and not slot["status"]):
            slot["status"] = status
        if roster == "CPT":
            slot["cpt_dk_id"] = (row.get("ID") or "").strip()
        else:
            # Classic rows and showdown FLEX rows carry the base salary and flex id.
            slot["salary"] = int(round(_num(row.get("Salary"))))
            slot["dk_id"] = (row.get("ID") or slot["dk_id"]).strip()
            if not showdown:
                slot["cpt_dk_id"] = ""
        if _num(row.get("AvgPointsPerGame")):
            slot["avg_points"] = _num(row.get("AvgPointsPerGame"))
    players = []
    for slot in grouped.values():
        if showdown and slot["salary"] <= 0:
            continue
        players.append(
            ImportedPlayer(
                name=slot["name"],
                team=slot["team"],
                opponent=slot["opponent"],
                position=slot["position"],
                salary=int(slot["salary"]),
                projection=0.0,
                ownership=0.0,
                cpt_ownership=0.0,
                dk_id=slot["dk_id"],
                cpt_dk_id=slot["cpt_dk_id"],
                status=slot["status"],
                avg_points=float(slot["avg_points"]),
                game=slot["game"],
            )
        )
    players.sort(key=lambda p: (-p.salary, p.name))
    if len(games) == 1:
        game = next(iter(games))
    elif not games:
        game = ""
    else:
        game = f"{len(games)} games"
    fmt = "showdown" if showdown else "classic"
    title = f"{game} Showdown" if showdown and game and "games" not in game else (
        game or "DraftKings slate"
    )
    if not showdown:
        title = f"{game} Classic" if game else "DraftKings Classic"
    return ImportedSlate(name=title, format=fmt, game=game, kickoff=kickoff, players=players)


def _proj_columns(row: dict[str, str]) -> dict[str, str]:
    return {name_key(key): key for key in row.keys()}


def apply_projections(slate: ImportedSlate, text: str) -> None:
    rows = _rows(text)
    if not rows:
        raise ValueError("Projections file is empty.")
    cols = _proj_columns(rows[0])
    for needed in ("name", "points"):
        if needed not in cols and not (needed == "points" and "projection" in cols):
            raise ValueError(
                "Projections CSV needs Name and Points columns "
                "(Ownership and CPT Ownership are optional)."
            )
    name_col = cols.get("name") or cols.get("player")
    points_col = cols.get("points") or cols.get("projection") or cols.get("proj")
    team_col = cols.get("team")
    opp_col = cols.get("opponent") or cols.get("opp")
    pos_col = cols.get("position") or cols.get("pos")
    own_col = cols.get("ownership") or cols.get("own") or cols.get("flexownership")
    cpt_col = cols.get("cptownership") or cols.get("cptown") or cols.get("captainownership")
    if not name_col or not points_col:
        raise ValueError("Projections CSV needs Name and Points columns.")

    by_key: dict[tuple[str, str], list[ImportedPlayer]] = {}
    by_name: dict[str, list[ImportedPlayer]] = {}
    dst_by_team: dict[str, ImportedPlayer] = {}
    for player in slate.players:
        by_key.setdefault((name_key(player.name), player.team), []).append(player)
        by_name.setdefault(name_key(player.name), []).append(player)
        if player.position == "DST":
            dst_by_team[player.team] = player

    matched: set[int] = set()
    unmatched: list[str] = []
    for row in rows:
        name = (row.get(name_col) or "").strip()
        if not name:
            continue
        team = normalize_team(row.get(team_col) or "") if team_col else ""
        pos = normalize_position(row.get(pos_col) or "") if pos_col else ""
        player = _match_projection(name, team, pos, by_key, by_name, dst_by_team)
        if player is None:
            unmatched.append(name)
            continue
        player.projection = _num(row.get(points_col))
        if own_col:
            player.ownership = _num(row.get(own_col))
        if cpt_col:
            player.cpt_ownership = _num(row.get(cpt_col))
        if opp_col and not player.opponent:
            player.opponent = normalize_team(row.get(opp_col) or "")
        matched.add(id(player))

    for player in slate.players:
        if id(player) not in matched:
            player.projection = player.avg_points
    if unmatched:
        preview = ", ".join(unmatched[:8])
        extra = f" (+{len(unmatched) - 8} more)" if len(unmatched) > 8 else ""
        slate.warnings.append(f"Unmatched projection rows: {preview}{extra}.")
    missing_proj = [p.name for p in slate.players if p.projection <= 0 and p.status not in {"OUT", "IR"}]
    if missing_proj:
        slate.warnings.append(
            f"{len(missing_proj)} active players have no projection and fell back to DK average points."
        )


def _match_projection(name, team, pos, by_key, by_name, dst_by_team):
    key = name_key(name)
    if team and (key, team) in by_key:
        return by_key[(key, team)][0]
    if is_dst(name, pos):
        if team and team in dst_by_team:
            return dst_by_team[team]
        for abbr, nick in NICKNAMES.items():
            if nick in key or key in nick:
                if abbr in dst_by_team:
                    return dst_by_team[abbr]
    hits = by_name.get(key) or []
    if len(hits) == 1:
        return hits[0]
    if team:
        team_hits = [p for p in hits if p.team == team]
        if len(team_hits) == 1:
            return team_hits[0]
    # Last-name fallback within the team.
    if team and key:
        loose = []
        for (pkey, pteam), group in by_key.items():
            if pteam == team and (pkey.endswith(key) or key.endswith(pkey)):
                loose.extend(group)
        if len(loose) == 1:
            return loose[0]
    return None
