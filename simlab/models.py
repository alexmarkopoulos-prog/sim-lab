"""Saved slates, lineups, and sim jobs for the single signed-in user."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from simlab.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SessionToken(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MagicLink(Base):
    __tablename__ = "magic_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Slate(Base):
    __tablename__ = "slates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(20))
    game: Mapped[str] = mapped_column(String(80), default="")
    kickoff: Mapped[str] = mapped_column(String(80), default="")
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    nfl: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    players: Mapped[list["Player"]] = relationship(back_populates="slate", cascade="all, delete-orphan")
    lineups: Mapped[list["Lineup"]] = relationship(back_populates="slate", cascade="all, delete-orphan")
    jobs: Mapped[list["SimJob"]] = relationship(back_populates="slate", cascade="all, delete-orphan")


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slate_id: Mapped[int] = mapped_column(ForeignKey("slates.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    team: Mapped[str] = mapped_column(String(8), default="")
    opponent: Mapped[str] = mapped_column(String(8), default="")
    position: Mapped[str] = mapped_column(String(8), default="")
    salary: Mapped[int] = mapped_column(Integer, default=0)
    projection: Mapped[float] = mapped_column(Float, default=0)
    ownership: Mapped[float] = mapped_column(Float, default=0)
    cpt_ownership: Mapped[float] = mapped_column(Float, default=0)
    dk_id: Mapped[str] = mapped_column(String(32), default="")
    cpt_dk_id: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(12), default="")
    boost: Mapped[float] = mapped_column(Float, default=1)
    min_exposure: Mapped[float] = mapped_column(Float, default=0)
    max_exposure: Mapped[float] = mapped_column(Float, default=100)
    lock: Mapped[str] = mapped_column(String(12), default="none")
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    snaps: Mapped[list] = mapped_column(JSON, default=list)
    injury: Mapped[str] = mapped_column(String(80), default="")

    slate: Mapped[Slate] = relationship(back_populates="players")


class Lineup(Base):
    __tablename__ = "lineups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slate_id: Mapped[int] = mapped_column(ForeignKey("slates.id"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="optimizer")
    slots: Mapped[list] = mapped_column(JSON, default=list)
    salary: Mapped[int] = mapped_column(Integer, default=0)
    projection: Mapped[float] = mapped_column(Float, default=0)
    ownership: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    slate: Mapped[Slate] = relationship(back_populates="lineups")


class SimJob(Base):
    __tablename__ = "sim_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slate_id: Mapped[int] = mapped_column(ForeignKey("slates.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(200), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    slate: Mapped[Slate] = relationship(back_populates="jobs")
