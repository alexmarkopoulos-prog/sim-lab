"""Injuries, snap counts, schedule, and pace from nflverse via nflreadpy.

Downloads happen only when the user asks. A failure leaves the salary-file
statuses (OUT / IR) in place.
"""

from __future__ import annotations

import re
from datetime import date

from simlab.services.names import name_key, normalize_position, normalize_team

_CACHE: dict[int, dict] = {}


def season_from_kickoff(kickoff: str, today: date | None = None) -> int:
    today = today or date.today()
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", kickoff or "")
    if match:
        month = int(match.group(1))
        year = int(match.group(3))
        return year if month >= 3 else year - 1
    return today.year if today.month >= 3 else today.year - 1


def enrich(players: list[dict], game: str, kickoff: str) -> dict:
    """Return per-player injury/snap updates and a slate-level note.

    ``players`` items need name, team, position. Matching is by normalized name.
    """
    import nflreadpy as nfl

    teams = sorted({normalize_team(p["team"]) for p in players if p.get("team")})
    season = season_from_kickoff(kickoff)
    bundle, used_season, warnings = _load(nfl, season, teams)
    if bundle is None:
        detail = " ".join(warnings) or "no schedule was published"
        raise RuntimeError(
            f"nflverse has no {season} schedule ({detail}). DraftKings OUT/IR flags are unchanged."
        )

    week = _match_week(bundle["schedules"], teams, game)
    injuries = _latest_injuries(bundle["injuries"], teams, week)
    snaps = _snap_trends(bundle["snaps"], teams, week)
    pace = _pace(bundle["pbp"], teams)

    updates = []
    for player in players:
        key = name_key(player["name"])
        team = normalize_team(player["team"])
        injury = injuries.get((team, key))
        trend = snaps.get((team, key)) or []
        if player["position"] == "DST" and not trend:
            trend = snaps.get((team, name_key(player["name"]))) or []
        updates.append({
            "name": player["name"],
            "team": team,
            "status": injury["status"] if injury else "",
            "injury": injury["detail"] if injury else "",
            "snaps": trend,
        })
    return {
        "season": used_season,
        "week": week,
        "pace": pace,
        "warnings": warnings,
        "players": updates,
    }


def _load(nfl, season: int, teams: list[str]):
    warnings = []
    if season in _CACHE:
        return _CACHE[season], season, warnings
    try:
        schedules = nfl.load_schedules(seasons=season)
        injuries = nfl.load_injuries(seasons=season)
        snaps = nfl.load_snap_counts(seasons=season)
    except Exception as exc:  # network or a season nflverse has not published
        return None, season, [f"{season}: {exc}"]
    pbp = None
    try:
        pbp = nfl.load_pbp(seasons=season)
    except Exception as exc:
        warnings.append(f"Play-by-play for {season} was not loaded ({exc}).")
    bundle = {"schedules": schedules, "injuries": injuries, "snaps": snaps, "pbp": pbp}
    _CACHE[season] = bundle
    return bundle, season, warnings


def _match_week(schedules, teams: list[str], game: str) -> int | None:
    import polars as pl

    if schedules is None or not teams:
        return None
    frame = schedules
    if game and "@" in game:
        away, home = [normalize_team(part) for part in game.split("@", 1)]
        hit = frame.filter((pl.col("away_team") == away) & (pl.col("home_team") == home))
        if hit.height:
            return int(hit.sort("week")["week"][-1])
    hit = frame.filter(pl.col("home_team").is_in(teams) | pl.col("away_team").is_in(teams))
    if hit.height:
        return int(hit.sort("week")["week"][-1])
    return None


def _latest_injuries(injuries, teams: list[str], week: int | None) -> dict[tuple[str, str], dict]:
    import polars as pl

    if injuries is None or injuries.height == 0:
        return {}
    frame = injuries.filter(pl.col("team").is_in(teams))
    if week is not None and "week" in frame.columns:
        exact = frame.filter(pl.col("week") == week)
        frame = exact if exact.height else frame
    found = {}
    for row in frame.iter_rows(named=True):
        status = _injury_status(row.get("report_status") or "")
        if not status:
            continue
        key = (normalize_team(row.get("team") or ""), name_key(row.get("full_name") or ""))
        found[key] = {
            "status": status,
            "detail": (row.get("report_primary_injury") or status or "").strip(),
        }
    return found


def _injury_status(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"out", "o"}:
        return "OUT"
    if text == "doubtful":
        return "D"
    if text == "questionable":
        return "Q"
    if text in {"ir", "injured reserve"}:
        return "IR"
    return ""


def _snap_trends(snaps, teams: list[str], week: int | None) -> dict[tuple[str, str], list[float]]:
    import polars as pl

    if snaps is None or snaps.height == 0:
        return {}
    frame = snaps.filter(pl.col("team").is_in(teams))
    if week is not None:
        frame = frame.filter(pl.col("week") < week) if frame.filter(pl.col("week") < week).height else frame
    found: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for row in frame.iter_rows(named=True):
        pos = normalize_position(row.get("position") or "")
        pct = row.get("defense_pct") if pos == "DST" else row.get("offense_pct")
        if pct is None:
            continue
        key = (normalize_team(row.get("team") or ""), name_key(row.get("player") or ""))
        found.setdefault(key, []).append((int(row.get("week") or 0), round(float(pct) * 100, 1)))
    trends = {}
    for key, series in found.items():
        series.sort()
        trends[key] = [pct for _week, pct in series[-4:]]
    return trends


def _pace(pbp, teams: list[str]) -> list[dict]:
    import polars as pl

    if pbp is None or not teams:
        return []
    columns = set(pbp.columns)
    needed = {"posteam", "game_id", "week", "play_type"}
    if not needed <= columns:
        return []
    frame = pbp.filter(pl.col("posteam").is_in(teams) & pl.col("play_type").is_in(["pass", "run"]))
    if frame.height == 0:
        return []
    grouped = (
        frame.group_by(["posteam", "game_id", "week"])
        .agg(
            pl.len().alias("plays"),
            (pl.col("play_type") == "pass").mean().alias("pass_rate"),
        )
        .sort(["posteam", "week"])
    )
    notes = []
    for team in teams:
        rows = grouped.filter(pl.col("posteam") == team).tail(4)
        if rows.height == 0:
            continue
        plays = float(rows["plays"].mean())
        rate = float(rows["pass_rate"].mean())
        notes.append({
            "team": team,
            "plays": round(plays, 1),
            "pass_rate": round(rate * 100, 1),
            "games": int(rows.height),
        })
    return notes
