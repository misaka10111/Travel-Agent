from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserProfileBase(BaseModel):
    age_group: str = ""
    mbti: str = ""
    city: str = ""
    companion: list[str] = []
    pace: list[str] = []
    budget: list[str] = []
    accommodation: list[str] = []
    transport: list[str] = []
    interests: list[str] = []
    dietary: list[str] = []


class UserProfileCreate(UserProfileBase):
    user_id: str


class UserProfileRead(UserProfileBase):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    created_at: datetime
    updated_at: datetime

