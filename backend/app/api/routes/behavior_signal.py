from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BehaviorSignal
from app.schemas import BehaviorSignalCreate, BehaviorSignalRead

router = APIRouter(prefix="/behavior-signals", tags=["behavior-signals"])


@router.post("", response_model=BehaviorSignalRead, status_code=status.HTTP_201_CREATED)
def create_signal(
    payload: BehaviorSignalCreate, db: Session = Depends(get_db)
) -> BehaviorSignal:
    signal = BehaviorSignal(**payload.model_dump())
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal


@router.get("/{user_id}", response_model=list[BehaviorSignalRead])
def list_signals(user_id: str, db: Session = Depends(get_db)) -> list[BehaviorSignal]:
    stmt = (
        select(BehaviorSignal)
        .where(BehaviorSignal.user_id == user_id)
        .order_by(BehaviorSignal.created_at.desc())
    )
    return list(db.scalars(stmt))

