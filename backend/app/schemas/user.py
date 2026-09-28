from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SendCodeRequest(BaseModel):
    phone: str


class LoginRequest(BaseModel):
    phone: str
    code: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone: str
    nickname: str
    created_at: datetime


class LoginResponse(BaseModel):
    user_id: str
    phone: str
    nickname: str
    is_new: bool
