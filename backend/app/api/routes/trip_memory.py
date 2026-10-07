from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import TripMemory
from app.schemas import TripMemoryCreate, TripMemoryRead
from app.services.memory_service import merge_trip_preferences, save_trip_summary

router = APIRouter(prefix="/trip-memory", tags=["trip-memory"])


def _recent_trips_for_summary(db: Session, limit: int = 3) -> list[dict]:
    """拉取最近 N 条已确认行程，字段结构与 Orchestrator 的 _fetch_recent_trips 保持一致。"""
    stmt = (
        select(TripMemory)
        .where(TripMemory.chosen_plan_style.is_not(None))
        .order_by(TripMemory.created_at.desc())
        .limit(limit)
    )
    recent: list[dict] = []
    for m in db.scalars(stmt):
        edits = m.user_edits or []
        if isinstance(edits, list):
            edits = edits[:3]
        recent.append(
            {
                "destination": m.destination or "",
                "start_date": m.start_date or "",
                "end_date": m.end_date or "",
                "chosen_plan_style": m.chosen_plan_style or "",
                "rating": m.rating,
                "feedback": m.feedback or "",
                "user_edits": edits,
            }
        )
    return recent


@router.post("", response_model=TripMemoryRead, status_code=status.HTTP_201_CREATED)
def create_trip_memory(
    payload: TripMemoryCreate, db: Session = Depends(get_db)
) -> TripMemory:
    memory = db.scalar(
        select(TripMemory).where(
            TripMemory.user_id == payload.user_id,
            TripMemory.destination == payload.destination,
            TripMemory.start_date == payload.start_date,
            TripMemory.end_date == payload.end_date,
        )
    )
    if memory is None:
        memory = TripMemory(**payload.model_dump())
        db.add(memory)
    else:
        for field, value in payload.model_dump().items():
            setattr(memory, field, value)
    db.commit()
    db.refresh(memory)
    merge_trip_preferences(payload.user_id, payload.final_plan, payload.conversation, db)
    save_trip_summary(payload.user_id, _recent_trips_for_summary(db), db)
    return memory


@router.get("/item/{memory_id}", response_model=TripMemoryRead)
def get_trip_memory(
    memory_id: int,
    user_id: str | None = None,
    db: Session = Depends(get_db),
) -> TripMemory:
    memory = db.get(TripMemory, memory_id)
    if memory is None or (user_id and memory.user_id != user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip memory not found",
        )
    return memory


@router.get("/{user_id}", response_model=list[TripMemoryRead])
def list_trip_memory(user_id: str, db: Session = Depends(get_db)) -> list[TripMemory]:
    stmt = (
        select(TripMemory)
        .where(TripMemory.user_id == user_id)
        .where(TripMemory.chosen_plan_style.is_not(None))
        .order_by(TripMemory.created_at.desc())
    )
    return list(db.scalars(stmt))
