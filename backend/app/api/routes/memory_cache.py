from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MemoryCache
from app.schemas import MemoryCacheRead, MemoryCacheUpsert

router = APIRouter(prefix="/memory-cache", tags=["memory-cache"])


@router.post("", response_model=MemoryCacheRead)
def upsert_memory_cache(
    payload: MemoryCacheUpsert, db: Session = Depends(get_db)
) -> MemoryCache:
    cache = db.scalar(select(MemoryCache).where(MemoryCache.user_id == payload.user_id))
    if cache is None:
        cache = MemoryCache(
            user_id=payload.user_id,
            trip_summary=payload.trip_summary,
            trip_summary_hash=payload.trip_summary_hash,
        )
        db.add(cache)
    else:
        cache.trip_summary = payload.trip_summary
        cache.trip_summary_hash = payload.trip_summary_hash
    db.commit()
    db.refresh(cache)
    return cache


@router.get("/{user_id}", response_model=MemoryCacheRead)
def get_memory_cache(user_id: str, db: Session = Depends(get_db)) -> MemoryCache:
    cache = db.scalar(select(MemoryCache).where(MemoryCache.user_id == user_id))
    if cache is None:
        raise HTTPException(status_code=404, detail="Memory cache not found")
    return cache
