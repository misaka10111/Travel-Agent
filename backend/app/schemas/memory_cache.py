from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MemoryCacheUpsert(BaseModel):
    user_id: str
    trip_summary: str = ""
    trip_summary_hash: str = ""


class MemoryCacheRead(MemoryCacheUpsert):
    model_config = ConfigDict(from_attributes=True)

    updated_at: datetime
