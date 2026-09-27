from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserProfileBase(BaseModel):
    age_group: str = ""
    gender: str = ""
    identity: str = ""
    city: str = ""
    travel_style: list[str] = []


class UserProfileCreate(UserProfileBase):
    user_id: str


class UserProfileRead(UserProfileBase):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    created_at: datetime
    updated_at: datetime
