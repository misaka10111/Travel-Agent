from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import TripMemory
from app.schemas import TripMemoryCreate, TripMemoryRead

router = APIRouter(prefix="/trip-memory", tags=["trip-memory"])


@router.post("", response_model=TripMemoryRead, status_code=status.HTTP_201_CREATED)
def create_trip_memory(
    payload: TripMemoryCreate, db: Session = Depends(get_db)
) -> TripMemory:
    memory = TripMemory(**payload.model_dump())
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


@router.get("/{user_id}", response_model=list[TripMemoryRead])
def list_trip_memory(user_id: str, db: Session = Depends(get_db)) -> list[TripMemory]:
    stmt = (
        select(TripMemory)
        .where(TripMemory.user_id == user_id)
        .order_by(TripMemory.created_at.desc())
    )
    return list(db.scalars(stmt))

