from contextlib import asynccontextmanager
import re

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.routes import (
    agent,
    auth,
    behavior_signal,
    destinations,
    health,
    plan,
    profile,
    trip_memory,
    trips,
    sessions,
)
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.services.destination_service import seed_destinations
from app.migrations.planning import migrate_planning
from app.runtime.guards import PlanningError

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI):
    migrate_planning(engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_destinations(db)
    try:
        yield
    finally:
        runtime = getattr(application.state, "planning_runtime", None)
        if runtime:
            await runtime.shutdown()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_prefix = settings.api_prefix
app.include_router(health.router, prefix=api_prefix)
app.include_router(auth.router, prefix=api_prefix)
app.include_router(trips.router, prefix=api_prefix)
app.include_router(destinations.router, prefix=api_prefix)
app.include_router(agent.router, prefix=api_prefix)
app.include_router(profile.router, prefix=api_prefix)
app.include_router(trip_memory.router, prefix=api_prefix)
app.include_router(behavior_signal.router, prefix=api_prefix)
app.include_router(plan.router, prefix=api_prefix)
app.include_router(sessions.router, prefix=api_prefix)


@app.api_route("/_AMapService/{service_path:path}", methods=["GET", "POST"])
async def amap_js_service_proxy(service_path: str, request: Request):
    """Forward JS API service calls without exposing its security code to the browser."""
    credential = settings.amap_js_security_code
    if credential is None or not credential.get_secret_value():
        return JSONResponse(status_code=503, content={"code": "amap_js_proxy_not_configured"})
    if not re.fullmatch(r"v[3-5]/[A-Za-z0-9_./-]+", service_path) or ".." in service_path:
        return JSONResponse(status_code=404, content={"code": "unknown_amap_service"})
    host = "https://webapi.amap.com" if service_path.startswith("v4/map/styles") else "https://restapi.amap.com"
    params = [(key, value) for key, value in request.query_params.multi_items() if key != "jscode"]
    params.append(("jscode", credential.get_secret_value()))
    try:
        async with httpx.AsyncClient(timeout=settings.map_request_timeout_seconds) as client:
            upstream = await client.request(
                request.method,
                f"{host}/{service_path}",
                params=params,
                content=await request.body() if request.method == "POST" else None,
                headers={"Content-Type": request.headers.get("content-type", "application/x-www-form-urlencoded")}
                if request.method == "POST" else None,
            )
    except httpx.HTTPError:
        return JSONResponse(status_code=502, content={"code": "amap_js_proxy_unavailable"})
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={"Content-Type": upstream.headers.get("content-type", "application/octet-stream")},
    )


@app.exception_handler(PlanningError)
async def planning_error_handler(_: Request, exc: PlanningError):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code})
