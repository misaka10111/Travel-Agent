from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import UserProfile
from app.schemas import UserProfileCreate, UserProfileRead

router = APIRouter(prefix="/profile", tags=["profile"])


@router.post("", response_model=UserProfileRead)
def upsert_profile(
    payload: UserProfileCreate, db: Session = Depends(get_db)
) -> UserProfile:
    profile = db.scalar(
        select(UserProfile).where(UserProfile.user_id == payload.user_id)
    )
    data = payload.model_dump(exclude={"user_id"})
    if profile is None:
        profile = UserProfile(user_id=payload.user_id, **data)
        db.add(profile)
    else:
        for key, value in data.items():
            setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/{user_id}", response_model=UserProfileRead)
def get_profile(user_id: str, db: Session = Depends(get_db)) -> UserProfile:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )
    return profile

