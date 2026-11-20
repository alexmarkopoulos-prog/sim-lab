"""HTTP API for auth, slates, lineups, and background sims."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse, Response as RawResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from simlab.config import get_settings
from simlab.db import get_db
from simlab.models import Lineup, MagicLink, Player, SessionToken, SimJob, Slate, User
from simlab.security import (
    expiry,
    hash_password,
    new_token,
    send_magic_email,
    token_hash,
    verify_password,
)
from simlab.services.importer import ImportedSlate, apply_projections, parse_dk_salary
from simlab.services.jobs import cancel as cancel_job
from simlab.services.jobs import launch
from simlab.services.nflverse import enrich
from simlab.services.optimizer import (
    OptPlayer,
    OptRules,
    generate_lineups,
    validate_manual,
)
from simlab.services.simulator import SimParams
from pydfs_lineup_optimizer.exceptions import LineupOptimizerException

COOKIE = "simlab_session"
ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(prefix="/api")


def default_rules(fmt: str) -> dict:
    if fmt == "showdown":
        return {
            "n": 4,
            "qb_stack": 1,
            "bring_back": 1,
            "no_naked_pass_catchers": True,
            "captain_ids": [],
            "only_with": [],
            "max_shared": 4,
            "max_per_captain": 2,
            "min_salary": 46000,
            "max_salary": 50000,
            "min_own": 160,
            "max_own": 210,
        }
    return {
        "n": 4,
        "qb_stack": 1,
        "bring_back": 1,
        "no_naked_pass_catchers": True,
        "captain_ids": [],
        "only_with": [],
        "max_shared": 6,
        "max_per_captain": None,
        "min_salary": 49000,
        "max_salary": 50000,
        "min_own": 80,
        "max_own": 280,
    }


def _optional_int(value):
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value):
    if value is None or value == "":
        return None
    return float(value)


def rules_from_json(raw: dict, fmt: str) -> OptRules:
    data = {**default_rules(fmt), **(raw or {})}
    only = []
    for item in data.get("only_with") or []:
        if isinstance(item, dict) and item.get("player_id") and item.get("with_id"):
            only.append((int(item["player_id"]), int(item["with_id"])))
    return OptRules(
        n=max(1, min(150, int(data.get("n") or 1))),
        qb_stack=max(0, int(data.get("qb_stack") or 0)),
        bring_back=max(0, int(data.get("bring_back") or 0)),
        no_naked_pass_catchers=bool(data.get("no_naked_pass_catchers", True)),
        captain_ids=[int(item) for item in data.get("captain_ids") or []],
        only_with=only,
        max_shared=_optional_int(data.get("max_shared")),
        max_per_captain=_optional_int(data.get("max_per_captain")),
        min_salary=_optional_int(data.get("min_salary")),
        max_salary=_optional_int(data.get("max_salary")),
        min_own=_optional_float(data.get("min_own")),
        max_own=_optional_float(data.get("max_own")),
        randomness=bool(data.get("randomness")),
    )


def sim_params_from_json(raw: dict) -> SimParams:
    tiers = raw.get("payout_tiers") or None
    cash = float(raw.get("cash_pct") or 0.2)
    if cash > 1:
        cash = cash / 100.0
    return SimParams(
        n_sims=max(200, min(40000, int(raw.get("n_sims") or 10000))),
        contest_size=max(2, min(500000, int(raw.get("contest_size") or 5000))),
        entry_fee=max(0.01, float(raw.get("entry_fee") or 20)),
        max_entries=max(1, int(raw.get("max_entries") or 1)),
        cash_pct=min(1.0, max(0.001, cash)),
        rake=min(0.5, max(0.0, float(raw.get("rake") or 0.15))),
        field_sample=max(100, min(8000, int(raw.get("field_sample") or 2500))),
        field_pool=max(200, min(30000, int(raw.get("field_pool") or 6000))),
        calib_rounds=max(0, min(8, int(raw.get("calib_rounds") or 3))),
        calib_size=max(100, min(5000, int(raw.get("calib_size") or 1000))),
        pool_limit=max(8, min(24, int(raw.get("pool_limit") or 18))),
        seed=int(raw.get("seed") or 1119),
        payout_tiers=tiers,
    )


def to_opt(player: Player) -> OptPlayer:
    return OptPlayer(
        id=player.id,
        name=player.name,
        team=player.team,
        opponent=player.opponent,
        position=player.position,
        salary=player.salary,
        projection=player.projection,
        ownership=player.ownership,
        cpt_ownership=player.cpt_ownership,
        dk_id=player.dk_id,
        cpt_dk_id=player.cpt_dk_id,
        boost=player.boost or 1,
        min_exposure=(player.min_exposure or 0) / 100.0,
        max_exposure=(player.max_exposure if player.max_exposure is not None else 100) / 100.0,
        lock=player.lock or "none",
        excluded=bool(player.excluded),
        status=player.status or "",
    )


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required.")
    row = db.query(SessionToken).filter_by(token_hash=token_hash(token)).one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.expires_at.replace(tzinfo=timezone.utc) < now:
        raise HTTPException(status_code=401, detail="Sign in required.")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=14 * 24 * 3600,
        path="/",
    )


def _open_session(db: Session, user: User) -> str:
    token = new_token()
    db.add(SessionToken(user_id=user.id, token_hash=token_hash(token), expires_at=expiry(days=14)))
    db.commit()
    return token


class LoginIn(BaseModel):
    email: str
    password: str = ""


class MagicIn(BaseModel):
    email: str


class RulesIn(BaseModel):
    n: int = 4
    qb_stack: int = 1
    bring_back: int = 1
    no_naked_pass_catchers: bool = True
    captain_ids: list[int] = Field(default_factory=list)
    only_with: list[dict] = Field(default_factory=list)
    max_shared: int | None = 4
    max_per_captain: int | None = 2
    min_salary: int | None = 46000
    max_salary: int | None = 50000
    min_own: float | None = 160
    max_own: float | None = 210


class PlayerPatch(BaseModel):
    boost: float | None = None
    min_exposure: float | None = None
    max_exposure: float | None = None
    lock: str | None = None
    excluded: bool | None = None


class ManualIn(BaseModel):
    slots: list[dict]


class SimIn(BaseModel):
    n_sims: int = 10000
    contest_size: int = 5000
    entry_fee: float = 20
    max_entries: int = 4
    cash_pct: float = 0.2
    rake: float = 0.15
    extra_lineups: int = 12
    payout_text: str = ""
    pool_limit: int = 18


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.password_login_enabled:
        raise HTTPException(status_code=400, detail="Password login is disabled. Use a magic link.")
    email = body.email.strip().lower()
    user = db.query(User).filter_by(email=email).one_or_none()
    if user is None or email != settings.email or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is wrong.")
    token = _open_session(db, user)
    response = Response(content='{"ok":true}', media_type="application/json")
    _set_cookie(response, token)
    return response


@router.post("/auth/magic")
def request_magic(body: MagicIn, db: Session = Depends(get_db)):
    settings = get_settings()
    email = body.email.strip().lower()
    message = "If that account exists, a sign-in link is on its way."
    payload = {"message": message}
    if email != settings.email:
        return payload
    user = db.query(User).filter_by(email=email).one_or_none()
    if user is None:
        return payload
    token = new_token()
    db.add(MagicLink(user_id=user.id, token_hash=token_hash(token), expires_at=expiry(minutes=20)))
    db.commit()
    link = f"{settings.base_url}/auth/magic?token={token}"
    sent = False
    try:
        sent = send_magic_email(email, link)
    except Exception as exc:
        payload["message"] = f"The mail server rejected the link ({exc})."
    if settings.dev_expose_magic_link and not sent:
        payload["dev_link"] = link
        payload["message"] = "SMTP is not configured. Use the development link below. Turn this off in production."
    elif sent:
        payload["message"] = "Sign-in link sent."
    return payload


@router.post("/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token:
        db.query(SessionToken).filter_by(token_hash=token_hash(token)).delete()
        db.commit()
    response = Response(content='{"ok":true}', media_type="application/json")
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"email": user.email}


def consume_magic_token(token: str, db: Session) -> User:
    row = db.query(MagicLink).filter_by(token_hash=token_hash(token)).one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.used or row.expires_at.replace(tzinfo=timezone.utc) < now:
        raise HTTPException(status_code=400, detail="That sign-in link is invalid or expired.")
    row.used = True
    user = db.get(User, row.user_id)
    db.commit()
    if user is None:
        raise HTTPException(status_code=400, detail="That sign-in link is invalid or expired.")
    return user


def magic_redirect(token: str, db: Session):
    user = consume_magic_token(token, db)
    response = RedirectResponse("/", status_code=303)
    _set_cookie(response, _open_session(db, user))
    return response


def _owned_slate(db: Session, user: User, slate_id: int) -> Slate:
    slate = db.get(Slate, slate_id)
    if slate is None or slate.user_id != user.id:
        raise HTTPException(status_code=404, detail="Slate not found.")
    return slate


def _sim_index(slate: Slate) -> dict[int, dict]:
    jobs = sorted(slate.jobs, key=lambda job: job.id)
    for job in reversed(jobs):
        if job.status == "done" and job.result:
            return {int(row["id"]): row for row in job.result.get("players") or [] if row.get("id") is not None}
    return {}


def _player_json(player: Player, sim: dict | None = None) -> dict:
    value = round(player.projection / (player.salary / 1000.0), 2) if player.salary else 0
    body = {
        "id": player.id,
        "name": player.name,
        "team": player.team,
        "opponent": player.opponent,
        "position": player.position,
        "salary": player.salary,
        "projection": player.projection,
        "ownership": player.ownership,
        "cpt_ownership": player.cpt_ownership,
        "value": value,
        "dk_id": player.dk_id,
        "cpt_dk_id": player.cpt_dk_id,
        "status": player.status,
        "boost": player.boost,
        "min_exposure": player.min_exposure,
        "max_exposure": player.max_exposure,
        "lock": player.lock,
        "excluded": player.excluded,
        "snaps": player.snaps or [],
        "injury": player.injury or "",
        "opt": None,
        "cpt_opt": None,
        "leverage": None,
    }
    if sim:
        body["opt"] = sim.get("opt")
        body["cpt_opt"] = sim.get("cpt_opt")
        body["leverage"] = sim.get("leverage")
    return body


def _lineup_json(lineup: Lineup) -> dict:
    return {
        "id": lineup.id,
        "source": lineup.source,
        "slots": lineup.slots,
        "salary": lineup.salary,
        "projection": lineup.projection,
        "ownership": lineup.ownership,
    }


def _job_json(job: SimJob | None) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "params": job.params,
        "result": job.result,
    }


def _slate_json(slate: Slate, full: bool = False) -> dict:
    body = {
        "id": slate.id,
        "name": slate.name,
        "format": slate.format,
        "game": slate.game,
        "kickoff": slate.kickoff,
        "rules": slate.rules or default_rules(slate.format),
        "nfl": slate.nfl or {},
        "created_at": slate.created_at.isoformat() if slate.created_at else "",
    }
    if full:
        sim = _sim_index(slate)
        body["players"] = [_player_json(player, sim.get(player.id)) for player in slate.players]
        body["lineups"] = [_lineup_json(lineup) for lineup in slate.lineups]
        latest = max(slate.jobs, key=lambda job: job.id) if slate.jobs else None
        body["latest_sim"] = _job_json(latest)
    return body


def _store_slate(db: Session, user: User, imported: ImportedSlate) -> Slate:
    slate = Slate(
        user_id=user.id,
        name=imported.name,
        format=imported.format,
        game=imported.game,
        kickoff=imported.kickoff,
        rules=default_rules(imported.format),
        nfl={"warnings": imported.warnings},
    )
    db.add(slate)
    db.flush()
    for player in imported.players:
        db.add(Player(
            slate_id=slate.id,
            name=player.name,
            team=player.team,
            opponent=player.opponent,
            position=player.position,
            salary=player.salary,
            projection=player.projection,
            ownership=player.ownership,
            cpt_ownership=player.cpt_ownership,
            dk_id=player.dk_id,
            cpt_dk_id=player.cpt_dk_id,
            status=player.status,
        ))
    db.commit()
    db.refresh(slate)
    return slate


@router.get("/slates")
def list_slates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Slate).filter_by(user_id=user.id).order_by(Slate.id.desc()).all()
    return [_slate_json(row) for row in rows]


@router.post("/slates")
async def upload_slate(
    salary: UploadFile = File(...),
    projections: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        salary_text = (await salary.read()).decode("utf-8-sig")
        proj_text = (await projections.read()).decode("utf-8-sig")
        imported = parse_dk_salary(salary_text)
        apply_projections(imported, proj_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    slate = _store_slate(db, user, imported)
    return _slate_json(slate, full=True)


@router.post("/slates/sample")
def sample_slate(user: User = Depends(current_user), db: Session = Depends(get_db)):
    salary_path = ROOT / "sample_data" / "dksal.csv"
    proj_path = ROOT / "sample_data" / "rgp_projections.csv"
    if not salary_path.exists() or not proj_path.exists():
        raise HTTPException(status_code=404, detail="Sample files are not in this build.")
    imported = parse_dk_salary(salary_path.read_text(encoding="utf-8-sig"))
    apply_projections(imported, proj_path.read_text(encoding="utf-8-sig"))
    slate = _store_slate(db, user, imported)
    return _slate_json(slate, full=True)


@router.get("/slates/{slate_id}")
def get_slate(slate_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _slate_json(_owned_slate(db, user, slate_id), full=True)


@router.delete("/slates/{slate_id}")
def delete_slate(slate_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slate = _owned_slate(db, user, slate_id)
    db.delete(slate)
    db.commit()
    return {"ok": True}


@router.post("/slates/{slate_id}/enrich")
def enrich_slate(slate_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slate = _owned_slate(db, user, slate_id)
    payload = [
        {"name": player.name, "team": player.team, "position": player.position}
        for player in slate.players
    ]
    try:
        report = enrich(payload, slate.game, slate.kickoff)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"nflverse request failed: {exc}") from exc
    from simlab.services.names import normalize_team

    by_key = {(row["team"], row["name"]): row for row in report["players"]}
    for player in slate.players:
        row = by_key.get((normalize_team(player.team), player.name))
        if row is None:
            continue
        if row["snaps"]:
            player.snaps = row["snaps"]
        if row["injury"]:
            player.injury = row["injury"]
        incoming = row["status"]
        if incoming in {"OUT", "IR"} or (incoming and player.status not in {"OUT", "IR"}):
            player.status = incoming
    slate.nfl = {
        "season": report["season"],
        "week": report["week"],
        "pace": report["pace"],
        "warnings": report["warnings"],
    }
    db.commit()
    db.refresh(slate)
    return _slate_json(slate, full=True)


class RulesBody(RulesIn):
    pass


@router.put("/slates/{slate_id}/rules")
def save_rules(slate_id: int, body: RulesIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slate = _owned_slate(db, user, slate_id)
    slate.rules = body.model_dump()
    db.commit()
    return slate.rules


@router.patch("/slates/{slate_id}/players/{player_id}")
def patch_player(
    slate_id: int,
    player_id: int,
    body: PlayerPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    slate = _owned_slate(db, user, slate_id)
    player = next((row for row in slate.players if row.id == player_id), None)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found.")
    if body.boost is not None:
        player.boost = min(3.0, max(0.05, float(body.boost)))
    if body.min_exposure is not None:
        player.min_exposure = min(100.0, max(0.0, float(body.min_exposure)))
    if body.max_exposure is not None:
        player.max_exposure = min(100.0, max(0.0, float(body.max_exposure)))
    if player.min_exposure > player.max_exposure:
        raise HTTPException(status_code=400, detail="Min exposure cannot exceed max exposure.")
    if body.excluded is not None:
        player.excluded = bool(body.excluded)
        if player.excluded:
            player.lock = "none"
    if body.lock is not None:
        lock = body.lock
        if slate.format != "showdown" and lock == "cpt":
            lock = "roster"
        if lock not in {"none", "cpt", "flex", "roster"}:
            raise HTTPException(status_code=400, detail="Unknown lock.")
        if lock == "cpt":
            for other in slate.players:
                if other.id != player.id and other.lock == "cpt":
                    other.lock = "none"
        player.lock = lock
        if lock != "none":
            player.excluded = False
    db.commit()
    return _player_json(player)


@router.post("/slates/{slate_id}/lineups/generate")
def generate(slate_id: int, body: RulesIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slate = _owned_slate(db, user, slate_id)
    slate.rules = body.model_dump()
    rules = rules_from_json(slate.rules, slate.format)
    opt_players = [to_opt(player) for player in slate.players]
    try:
        result = generate_lineups(opt_players, rules, slate.format == "showdown")
    except LineupOptimizerException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for lineup in list(slate.lineups):
        if lineup.source == "optimizer":
            db.delete(lineup)
    db.flush()
    for built in result.lineups:
        db.add(Lineup(
            slate_id=slate.id,
            source="optimizer",
            slots=[_built_slot(slot) for slot in built.slots],
            salary=built.salary,
            projection=built.projection,
            ownership=built.ownership,
        ))
    db.commit()
    db.refresh(slate)
    return {"warning": result.warning, "lineups": [_lineup_json(lineup) for lineup in slate.lineups]}


def _built_slot(slot) -> dict:
    return {
        "slot": slot.slot,
        "player_id": slot.player_id,
        "name": slot.name,
        "team": slot.team,
        "position": slot.position,
        "salary": slot.salary,
        "projection": slot.projection,
        "ownership": slot.ownership,
        "dk_id": slot.dk_id,
    }


@router.post("/slates/{slate_id}/lineups")
def create_manual(slate_id: int, body: ManualIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slate = _owned_slate(db, user, slate_id)
    lineup = _manual(slate, body.slots)
    row = Lineup(
        slate_id=slate.id,
        source="manual",
        slots=[_built_slot(slot) for slot in lineup.slots],
        salary=lineup.salary,
        projection=lineup.projection,
        ownership=lineup.ownership,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _lineup_json(row)


@router.patch("/slates/{slate_id}/lineups/{lineup_id}")
def edit_lineup(
    slate_id: int,
    lineup_id: int,
    body: ManualIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    slate = _owned_slate(db, user, slate_id)
    row = next((lineup for lineup in slate.lineups if lineup.id == lineup_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Lineup not found.")
    lineup = _manual(slate, body.slots)
    row.slots = [_built_slot(slot) for slot in lineup.slots]
    row.salary = lineup.salary
    row.projection = lineup.projection
    row.ownership = lineup.ownership
    row.source = "manual"
    db.commit()
    return _lineup_json(row)


@router.delete("/slates/{slate_id}/lineups/{lineup_id}")
def delete_lineup(slate_id: int, lineup_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slate = _owned_slate(db, user, slate_id)
    row = next((lineup for lineup in slate.lineups if lineup.id == lineup_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Lineup not found.")
    db.delete(row)
    db.commit()
    return {"ok": True}


def _manual(slate: Slate, slots: list[dict]):
    players = {player.id: to_opt(player) for player in slate.players}
    parsed = []
    for slot in slots:
        try:
            parsed.append((int(slot["player_id"]), str(slot.get("slot") or "")))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Each slot needs a player_id.") from exc
    try:
        return validate_manual(players, parsed, slate.format == "showdown")
    except LineupOptimizerException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/slates/{slate_id}/lineups/export")
def export_lineups(slate_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slate = _owned_slate(db, user, slate_id)
    if not slate.lineups:
        raise HTTPException(status_code=400, detail="Build a lineup before exporting.")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if slate.format == "showdown":
        writer.writerow(["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"])
        for lineup in slate.lineups:
            ordered = sorted(lineup.slots, key=lambda slot: (slot.get("slot") != "CPT", -slot.get("salary", 0)))
            writer.writerow([slot.get("dk_id") or slot.get("name") for slot in ordered])
    else:
        writer.writerow(["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"])
        order = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
        for lineup in slate.lineups:
            pools: dict[str, list] = {}
            for slot in lineup.slots:
                pools.setdefault(slot.get("slot") or slot.get("position"), []).append(slot)
            writer.writerow([_take(pools, name) for name in order])
    filename = f"simlab_{(slate.game or 'slate').replace('@', '-')}_{slate.format}.csv"
    return RawResponse(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _take(pools: dict, name: str) -> str:
    bucket = pools.get(name) or []
    if not bucket:
        return ""
    slot = bucket.pop(0)
    return slot.get("dk_id") or slot.get("name") or ""


@router.post("/slates/{slate_id}/sims")
def start_sim(slate_id: int, body: SimIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slate = _owned_slate(db, user, slate_id)
    running = db.query(SimJob).filter_by(slate_id=slate.id).filter(SimJob.status.in_(["queued", "running"])).first()
    if running:
        raise HTTPException(status_code=409, detail="A sim is already running for this slate.")
    params = body.model_dump()
    if body.payout_text.strip():
        params["payout_tiers"] = _parse_payouts(body.payout_text)
    params["extra_lineups"] = max(0, min(40, body.extra_lineups))
    job = SimJob(slate_id=slate.id, status="queued", progress=0, message="Queued", params=params)
    db.add(job)
    db.commit()
    db.refresh(job)
    launch(job.id)
    return _job_json(job)


def _parse_payouts(text: str) -> list[dict]:
    tiers = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            places, prize = line.split(":")
            start, end = places.split("-") if "-" in places else (places, places)
            tiers.append({"start": int(start), "end": int(end), "prize": float(prize)})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Could not read payout line '{raw}'. Use 1-1:10000.") from exc
    if not tiers:
        raise HTTPException(status_code=400, detail="Add at least one payout line.")
    return tiers


@router.get("/sims/{job_id}")
def get_sim(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(SimJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Sim not found.")
    _owned_slate(db, user, job.slate_id)
    return _job_json(job)


@router.post("/sims/{job_id}/cancel")
def cancel_sim(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(SimJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Sim not found.")
    _owned_slate(db, user, job.slate_id)
    cancel_job(job.id)
    return {"ok": True}
