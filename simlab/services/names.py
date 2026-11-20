"""Name and team matching between DraftKings files and projection files."""

from __future__ import annotations

import re

TEAM_ALIAS = {
    "JAC": "JAX",
    "WSH": "WAS",
    "LA": "LAR",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "KAN": "KC",
    "LVR": "LV",
    "NWE": "NE",
    "NOR": "NO",
    "SFO": "SF",
    "TAM": "TB",
}

NICKNAMES = {
    "ARI": "cardinals",
    "ATL": "falcons",
    "BAL": "ravens",
    "BUF": "bills",
    "CAR": "panthers",
    "CHI": "bears",
    "CIN": "bengals",
    "CLE": "browns",
    "DAL": "cowboys",
    "DEN": "broncos",
    "DET": "lions",
    "GB": "packers",
    "HOU": "texans",
    "IND": "colts",
    "JAX": "jaguars",
    "KC": "chiefs",
    "LV": "raiders",
    "LAC": "chargers",
    "LAR": "rams",
    "MIA": "dolphins",
    "MIN": "vikings",
    "NE": "patriots",
    "NO": "saints",
    "NYG": "giants",
    "NYJ": "jets",
    "PHI": "eagles",
    "PIT": "steelers",
    "SEA": "seahawks",
    "SF": "49ers",
    "TB": "buccaneers",
    "TEN": "titans",
    "WAS": "commanders",
}

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv)\b\.?")


def normalize_team(team: str) -> str:
    team = (team or "").strip().upper()
    return TEAM_ALIAS.get(team, team)


def normalize_position(pos: str) -> str:
    pos = (pos or "").strip().upper().replace("D/ST", "DST").replace("DEF", "DST")
    if pos in {"D", "DS"}:
        return "DST"
    return pos


def name_key(value: str) -> str:
    """Loose key: drops punctuation, suffixes, and apostrophes (C.J. == CJ)."""
    text = (value or "").replace("\u2019", "'").replace("\u2018", "'").lower()
    text = text.replace("'", "")
    text = _SUFFIX.sub("", text)
    return re.sub(r"[^a-z0-9]", "", text)


def split_name(name: str) -> tuple[str, str]:
    parts = (name or "").split()
    if len(parts) <= 1:
        return (parts[0] if parts else name), ""
    return parts[0], " ".join(parts[1:])


def is_dst(name: str, position: str) -> bool:
    pos = normalize_position(position)
    if pos == "DST":
        return True
    key = name_key(name)
    return any(nick in key or key in nick for nick in NICKNAMES.values() if len(key) >= 4)
