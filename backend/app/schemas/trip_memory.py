from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TripMemoryCreate(BaseModel):
    user_id: str
    destination: str
    start_date: str
    end_date: str
    chosen_plan_style: str | None = None
    final_plan: dict = {}
    conversation: list = []
    user_edits: list = []
    feedback: str = ""
    rating: int | None = None


class TripMemoryRead(TripMemoryCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
