"""Additive planning tables. Supplier payloads and access tokens never enter JSON."""

from sqlalchemy import JSON, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PlanningSession(Base):
    __tablename__ = "planning_sessions"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    state_json: Mapped[dict] = mapped_column(JSON)
    profile_json: Mapped[dict] = mapped_column(JSON)
    messages_json: Mapped[list] = mapped_column(JSON, default=list)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    map_calls: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    model_limit: Mapped[int] = mapped_column(Integer)
    map_limit: Mapped[int] = mapped_column(Integer)
    step_limit: Mapped[int] = mapped_column(Integer)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)


class PlanningCommand(Base):
    __tablename__ = "planning_commands"
    __table_args__ = (UniqueConstraint("session_id", "request_id"),)
    command_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))


class PlanningEvent(Base):
    __tablename__ = "planning_events"
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    state_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class PlanningMigration(Base):
    __tablename__ = "planning_migrations"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
