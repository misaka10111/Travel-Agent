from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import UserPreference
from app.schemas import UserPreferenceCreate, UserPreferenceRead
from app.services.memory_service import aggregate_preferences

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.post("", response_model=UserPreferenceRead)
def upsert_preferences(
    payload: UserPreferenceCreate, db: Session = Depends(get_db)
) -> UserPreference:
    pref = db.scalar(
        select(UserPreference).where(UserPreference.user_id == payload.user_id)
    )
    if pref is None:
        pref = UserPreference(user_id=payload.user_id, preferences=payload.preferences)
        db.add(pref)
    else:
        pref.preferences = payload.preferences
    db.commit()
    db.refresh(pref)
    return pref


@router.get("/{user_id}", response_model=UserPreferenceRead)
def get_preferences(user_id: str, db: Session = Depends(get_db)) -> UserPreference:
    pref = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    if pref is None:
        raise HTTPException(
            status_code=404, detail="Preference not found"
        )
    return pref


@router.post("/{user_id}/aggregate", response_model=UserPreferenceRead)
def aggregate_signals(user_id: str, db: Session = Depends(get_db)) -> UserPreference:
    """把该用户的行为信号聚合为长期偏好，并 upsert 到偏好表。"""
    return aggregate_preferences(user_id, db)
