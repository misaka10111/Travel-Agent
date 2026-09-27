from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BehaviorSignalCreate(BaseModel):
    user_id: str
    action: str
    target: str = ""
    detail: str = ""


class BehaviorSignalRead(BehaviorSignalCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime

