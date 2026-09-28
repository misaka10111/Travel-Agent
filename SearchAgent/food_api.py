"""独立餐饮 HTTP 接口，不启动其他 SearchAgent 工具。

从项目根运行：uvicorn SearchAgent.food_api:app --host 127.0.0.1 --port 8001
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .food import FoodSearchError, FoodSearchRequest, FoodSearchResponse, search_restaurants

load_dotenv(Path(__file__).resolve().parent / ".env")


class FoodValidationDetail(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class FoodAPIError(BaseModel):
    code: str
    message: str
    details: list[FoodValidationDetail] | None = None


class FoodAPIErrorResponse(BaseModel):
    error: FoodAPIError


def _cors_origins() -> list[str]:
    raw = os.getenv("FOOD_CORS_ORIGINS")
    if not raw:
        return ["http://localhost:5173", "http://127.0.0.1:5173"]
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("FOOD_CORS_ORIGINS 必须是 JSON 字符串数组") from exc
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("FOOD_CORS_ORIGINS 必须是 JSON 字符串数组")
    return values


app = FastAPI(
    title="SearchAgent 餐饮检索接口",
    version="1.0.0",
    description="从高德返回可供规划智能体使用的餐厅候选。价格和评分缺失时保留 null。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(FoodSearchError)
async def food_error_handler(_request: Request, exc: FoodSearchError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI 默认错误含 input；这里不回显用户输入或任何凭据。
    details = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "餐饮查询参数不合法，请根据 details 修改字段。",
                "details": details,
            }
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "餐饮检索服务内部错误。"}},
    )


@app.get("/health")
def health() -> dict[str, str]:
    """仅检查 HTTP 服务可用性，不消耗模型或高德配额。"""
    return {"status": "ok", "service": "food-search"}


@app.post(
    "/api/search/food",
    response_model=FoodSearchResponse,
    responses={
        422: {"model": FoodAPIErrorResponse, "description": "请求参数不合法或自然语言条件解析失败"},
        500: {"model": FoodAPIErrorResponse, "description": "服务内部错误"},
        502: {"model": FoodAPIErrorResponse, "description": "上游接口错误"},
        503: {"model": FoodAPIErrorResponse, "description": "服务端密钥或模型配置缺失/不合法"},
        504: {"model": FoodAPIErrorResponse, "description": "上游接口超时"},
    },
)
def search_food(request: FoodSearchRequest) -> FoodSearchResponse:
    """前端仅发送查询参数；服务端负责读取 API 密钥并返回结构化候选。"""
    return search_restaurants(request)
