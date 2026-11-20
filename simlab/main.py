"""Sim Lab web server."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from simlab.api import magic_redirect, router
from simlab.config import get_settings
from simlab.db import SessionLocal, get_db, init_db
from simlab.models import User
from simlab.security import hash_password
from simlab.services.jobs import sweep_interrupted

STATIC = Path(__file__).resolve().parent / "static"


def ensure_user() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=settings.email).one_or_none()
        if user is None:
            password_hash = hash_password(settings.password) if settings.password else ""
            db.add(User(email=settings.email, password_hash=password_hash))
        elif settings.password:
            user.password_hash = hash_password(settings.password)
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    ensure_user()
    sweep_interrupted()
    yield


app = FastAPI(title="Sim Lab", lifespan=lifespan)
app.include_router(router)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "sim-lab"}


@app.get("/auth/magic")
def auth_magic(token: str, db: Session = Depends(get_db)):
    return magic_redirect(token, db)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
