from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserPreferenceBase(BaseModel):
    preferences: dict = {}


class UserPreferenceCreate(UserPreferenceBase):
    user_id: str


class UserPreferenceRead(UserPreferenceBase):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    created_at: datetime
    updated_at: datetime
