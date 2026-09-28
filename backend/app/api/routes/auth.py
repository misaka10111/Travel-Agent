import random
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import LoginRequest, LoginResponse, SendCodeRequest

router = APIRouter(prefix="/auth", tags=["auth"])

# 开发模式：验证码存内存（phone -> (code, expire_ts)）
_codes: dict[str, tuple[str, float]] = {}
_CODE_TTL = 300  # 5 分钟有效


@router.post("/send-code")
def send_code(payload: SendCodeRequest) -> dict:
    phone = payload.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="手机号不能为空")
    code = str(random.randint(100000, 999999))
    _codes[phone] = (code, time.time() + _CODE_TTL)
    # 开发模式：直接返回验证码；生产环境应改为发送短信
    return {"phone": phone, "code": code, "dev": True}


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    phone = payload.phone.strip()
    code = payload.code.strip()

    entry = _codes.get(phone)
    if not entry or entry[1] < time.time():
        raise HTTPException(status_code=401, detail="验证码已过期，请重新获取")
    if entry[0] != code:
        raise HTTPException(status_code=401, detail="验证码错误")
    del _codes[phone]  # 一次性使用

    user = db.scalar(select(User).where(User.phone == phone))
    is_new = user is None
    if user is None:
        user = User(phone=phone, nickname="")
        db.add(user)
        db.commit()
        db.refresh(user)

    return LoginResponse(
        user_id=phone,
        phone=phone,
        nickname=user.nickname,
        is_new=is_new,
    )
