from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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


@app.exception_handler(PlanningError)
async def planning_error_handler(_: Request, exc: PlanningError):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code})
