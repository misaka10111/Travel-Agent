from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import TripMemory
from app.schemas import TripMemoryCreate, TripMemoryRead
from app.services.memory_service import merge_conversation_preferences

router = APIRouter(prefix="/trip-memory", tags=["trip-memory"])


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
    if payload.conversation:
        merge_conversation_preferences(payload.user_id, payload.conversation, db)
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
        .order_by(TripMemory.created_at.desc())
    )
    return list(db.scalars(stmt))
