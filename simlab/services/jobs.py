"""Background sim jobs. The request returns immediately and the UI polls."""

from __future__ import annotations

import threading
from dataclasses import asdict, replace
from datetime import datetime, timezone

from pydfs_lineup_optimizer.exceptions import LineupOptimizerException

from simlab.db import SessionLocal
from simlab.models import Lineup, Player, SimJob, Slate
from simlab.services.optimizer import (
    BuiltLineup,
    OptPlayer,
    OptRules,
    generate_lineups,
    lineup_signature,
)
from simlab.services.simulator import (
    SimLineup,
    SimParams,
    SimPlayer,
    SimReport,
    run_classic,
    run_showdown,
)

CANCEL: dict[int, threading.Event] = {}
_LOCK = threading.Lock()


class JobCancelled(Exception):
    pass


def sweep_interrupted() -> None:
    db = SessionLocal()
    try:
        rows = db.query(SimJob).filter(SimJob.status.in_(["queued", "running"])).all()
        for job in rows:
            job.status = "error"
            job.error = "Interrupted by a restart."
            job.finished_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def launch(job_id: int) -> None:
    with _LOCK:
        CANCEL[job_id] = threading.Event()
    thread = threading.Thread(target=_run, args=(job_id,), name=f"sim-{job_id}", daemon=True)
    thread.start()


def cancel(job_id: int) -> None:
    event = CANCEL.get(job_id)
    if event:
        event.set()


def _run(job_id: int) -> None:
    try:
        _mark(job_id, status="running", progress=1, message="Starting")
        payload = _load(job_id)
        report = _simulate(job_id, payload)
        _finish(job_id, report)
    except JobCancelled:
        _mark(job_id, status="error", error="Cancelled.", message="Cancelled", done=True)
    except Exception as exc:  # surface the solver/sim error to the UI
        _mark(job_id, status="error", error=str(exc), message="Failed", done=True)
    finally:
        CANCEL.pop(job_id, None)


def _simulate(job_id: int, payload: dict) -> dict:
    def progress(pct: int, message: str) -> None:
        if CANCEL.get(job_id) and CANCEL[job_id].is_set():
            raise JobCancelled()
        _mark(job_id, progress=pct, message=message)

    showdown = payload["format"] == "showdown"
    rules: OptRules = payload["rules"]
    opt_players: list[OptPlayer] = payload["opt_players"]
    saved: list[dict] = payload["saved"]
    extra_n = int(payload["extra_lineups"])
    params: SimParams = payload["params"]

    generated: list[BuiltLineup] = []
    if extra_n > 0 or (not showdown and len(saved) < 8):
        progress(8, "Generating optimizer completions")
        count = extra_n if showdown else max(extra_n, 24)
        try:
            generated = generate_lineups(
                opt_players, replace(rules, n=count, randomness=True), showdown
            ).lineups
        except LineupOptimizerException as exc:
            if not saved:
                raise
            progress(10, f"Extra lineups skipped: {exc}")

    sim_players = [
        SimPlayer(
            id=player.id, name=player.name, team=player.team, opponent=player.opponent,
            position=player.position, salary=player.salary, projection=player.projection,
            ownership=player.ownership, cpt_ownership=player.cpt_ownership, boost=player.boost,
        )
        for player in opt_players
    ]
    meta: dict[str, dict] = {}
    sim_lineups: list[SimLineup] = []
    seen = set()

    def add(source: str, key: str, built_slots: list[dict], salary: int, projection: float, ownership: float):
        ids = [int(slot["player_id"]) for slot in built_slots]
        signature = lineup_signature(ids, showdown)
        if signature in seen:
            return
        seen.add(signature)
        meta[key] = {
            "slots": built_slots,
            "salary": salary,
            "projection": projection,
            "ownership": ownership,
        }
        sim_lineups.append(SimLineup(key=key, source=source, player_ids=ids))

    for lineup in saved:
        add("saved", f"saved-{lineup['id']}", lineup["slots"], lineup["salary"], lineup["projection"], lineup["ownership"])
    for index, lineup in enumerate(generated):
        slots = [_slot_dict(slot) for slot in lineup.slots]
        add("generated", f"gen-{index}", slots, lineup.salary, lineup.projection, lineup.ownership)

    if not sim_players:
        raise ValueError("No active players to simulate.")

    if showdown:
        report = run_showdown(sim_players, sim_lineups, params, progress)
        return _pack(report, meta)

    salaries = {item.key: meta[item.key]["salary"] for item in sim_lineups}
    report = run_classic(sim_players, sim_lineups, sim_lineups, params, salaries, progress)
    return _pack(report, meta)


def _slot_dict(slot) -> dict:
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


def _pack(report: SimReport, meta: dict) -> dict:
    body = asdict(report)
    for row in body["lineups"]:
        extra = meta.get(row["key"])
        if extra:
            row["slots"] = extra["slots"]
            row["salary"] = extra["salary"]
            row["projection"] = extra["projection"]
            row["ownership"] = extra["ownership"]
    return body


def _load(job_id: int) -> dict:
    db = SessionLocal()
    try:
        job = db.get(SimJob, job_id)
        if job is None:
            raise RuntimeError("Sim job disappeared.")
        slate = db.get(Slate, job.slate_id)
        players = db.query(Player).filter_by(slate_id=slate.id).all()
        lineups = db.query(Lineup).filter_by(slate_id=slate.id).all()
        from simlab.api import rules_from_json, sim_params_from_json, to_opt

        # Excluded players stay in this pool so optimal% still measures them.
        # generate_lineups drops them before it builds lineups.
        sim_pool = [
            to_opt(player)
            for player in players
            if player.status not in {"OUT", "IR"} and player.salary > 0
        ]
        return {
            "format": slate.format,
            "rules": rules_from_json(slate.rules or {}, slate.format),
            "opt_players": sim_pool,
            "saved": [
                {
                    "id": lineup.id,
                    "slots": lineup.slots,
                    "salary": lineup.salary,
                    "projection": lineup.projection,
                    "ownership": lineup.ownership,
                }
                for lineup in lineups
            ],
            "extra_lineups": int((job.params or {}).get("extra_lineups") or 0),
            "params": sim_params_from_json(job.params or {}),
        }
    finally:
        db.close()


def _mark(job_id: int, done: bool = False, **fields) -> None:
    db = SessionLocal()
    try:
        job = db.get(SimJob, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        if done:
            job.finished_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _finish(job_id: int, result: dict) -> None:
    _mark(job_id, status="done", progress=100, message="Done", result=result, error="", done=True)
