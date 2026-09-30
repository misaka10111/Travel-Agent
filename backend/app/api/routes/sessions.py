"""New P1 API coexists with the legacy plan generator until the P2 cutover."""

import time
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response

from app.agents.client import JsonModelClient
from app.agents.plan_agent import PlanAgent
from app.agents.question_agent import QuestionAgent
from app.config import get_settings
from app.db import SessionLocal
from app.runtime.executor import PlanningRuntime
from app.runtime.guards import PlanningError
from app.runtime.store import SessionStore, fingerprint
from app.schemas.planning_api import AnswerSession, CreatedSession, CreateSession, EditSession, SessionEvent, SessionView, VersionCommand
from app.services.intent_service import apply_patch, invalidate
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/sessions", tags=["planning-sessions"])


def get_runtime(request: Request):
    if not getattr(request.app.state, "planning_runtime", None):
        settings = get_settings()
        client = JsonModelClient(settings)
        request.app.state.planning_runtime = PlanningRuntime(SessionStore(SessionLocal, settings), PlanAgent(client),
            ToolRegistry(QuestionAgent(client), settings), settings)
    return request.app.state.planning_runtime


def owned_session(session_id: str, authorization: str | None = Header(default=None), runtime=Depends(get_runtime)):
    parts = authorization.split(" ", 1) if authorization else []
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None
    runtime.store.authorize(session_id, token)
    return runtime


def command(runtime, session_id, body, kind):
    signature = fingerprint(kind, body.model_dump(mode="json"))
    if runtime.store.replay(session_id, body.request_id, signature):
        return signature, None
    snapshot = runtime.store.load(session_id)
    if snapshot.state.state_version != body.base_state_version:
        raise PlanningError("stale_state")
    return signature, snapshot


@router.post("", response_model=CreatedSession, status_code=201)
async def create_session(body: CreateSession, runtime=Depends(get_runtime)):
    session_id, token = runtime.store.create(body)
    return CreatedSession(**runtime.store.view(session_id).model_dump(), access_token=token)


@router.get("/{session_id}", response_model=SessionView)
async def get_session(session_id: str, runtime=Depends(owned_session)):
    return runtime.store.view(session_id)


@router.get("/{session_id}/map")
async def day_map(session_id: str, day_index: int = Query(ge=1, le=30), runtime=Depends(owned_session)):
    """Proxy one authenticated static map; the Web Service key stays server-side."""
    state = runtime.store.load(session_id).state
    day = next((d for d in state.current_plan.days if d.day_index == day_index), None) if state.current_plan else None
    if day is None:
        raise PlanningError("map_day_unavailable", 404)
    by_id = {p.place_id: p for p in state.candidates}
    nodes = []
    for stop in day.stops:
        point = by_id.get(stop.place_id)
        if point and point.coordinates and point.coordinates.crs == "GCJ02" and point.place_id not in {p.place_id for p in nodes}:
            nodes.append(point)
    if not nodes:
        raise PlanningError("map_coordinates_unavailable", 422)
    key = get_settings().amap_web_service_key
    if not key:
        raise PlanningError("map_service_unavailable", 503)
    coord = lambda point: f"{point.longitude:.6f},{point.latitude:.6f}"
    markers = []
    for index, point in enumerate(nodes[:10], 1):
        label = "H" if point.category == "hotel" else str(index % 10)
        color = "0x5667E9" if point.category == "hotel" else "0xEE7651"
        markers.append(f"mid,{color},{label}:{coord(point.coordinates)}")
    paths = []
    eligible = [leg for leg in day.routes if leg.status == "ok" and leg.geometry and
                leg.geometry.crs == "GCJ02" and leg.geometry.encoding == "points"]
    for leg in eligible[:4]:
        points = leg.geometry.points
        stride = max(1, len(points) // 40)
        sample = points[::stride]
        if sample[-1] != points[-1]:
            sample.append(points[-1])
        if len(sample) >= 2:
            paths.append("5,0x5667E9,0.85,," + ":" + ";".join(coord(point) for point in sample))
    params = {"key": key.get_secret_value(), "size": "640*330", "markers": "|".join(markers)}
    if paths:
        params["paths"] = "|".join(paths)
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=get_settings().map_request_timeout_seconds) as client:
            response = await client.get("https://restapi.amap.com/v3/staticmap", params=params)
    except httpx.RequestError:
        raise PlanningError("map_service_unavailable", 503) from None
    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/") or len(response.content) > 3_000_000:
        raise PlanningError("map_service_unavailable", 503)
    return Response(content=response.content, media_type=response.headers["content-type"],
        headers={"Cache-Control": "private, max-age=120", "X-Route-Segments-Shown": str(len(paths)),
                 "X-Route-Segments-Total": str(len(eligible))})


@router.post("/{session_id}/run", response_model=SessionView, status_code=202)
async def run_session(session_id: str, body: VersionCommand, runtime=Depends(owned_session)):
    signature, snapshot = command(runtime, session_id, body, "run")
    if snapshot is None:
        return runtime.store.view(session_id, replayed=True)
    if snapshot.state.status in {"waiting_user", "cancelled", "failed"} or (snapshot.state.status == "completed" and not snapshot.state.plan_needs_refresh):
        raise PlanningError("session_not_resumable")
    if snapshot.lease_id and snapshot.lease_until > time.time():
        raise PlanningError("session_already_running")
    lease_id = str(uuid4())
    state = snapshot.state
    state.status, state.attention_reason = "running", None
    state.agent_message = None
    runtime.store.save(state, expected=state.state_version, kind="run_requested",
        command=(body.request_id, signature), new_lease=lease_id)
    runtime.start(session_id, lease_id)
    return runtime.store.view(session_id)


@router.post("/{session_id}/retry", response_model=CreatedSession, status_code=201)
async def retry_session(session_id: str, body: VersionCommand, runtime=Depends(owned_session)):
    child_id, token = runtime.store.retry_with_context(session_id, body.base_state_version)
    return CreatedSession(**runtime.store.view(child_id).model_dump(), access_token=token)


@router.post("/{session_id}/answers", response_model=SessionView)
async def answer_session(session_id: str, body: AnswerSession, runtime=Depends(owned_session)):
    answer = body.answer
    signature = fingerprint("answer", body.model_dump(mode="json"))
    if runtime.store.replay(session_id, answer.request_id, signature):
        return runtime.store.view(session_id, replayed=True)
    snapshot = runtime.store.load(session_id)
    state = snapshot.state
    if state.status != "waiting_user" or answer.state_version != state.state_version:
        raise PlanningError("stale_answer_or_not_waiting")
    question = next((q for q in state.pending_questions if q.question_id == answer.question_id), None)
    if question is None:
        raise PlanningError("unknown_question", 422)
    try:
        answer.check_question(question)
    except ValueError:
        raise PlanningError("invalid_answer_options_or_version", 422) from None
    if body.intent_patch is not None:
        previous = state.intent_snapshot.model_copy(deep=True)
        state.intent_snapshot = apply_patch(state.intent_snapshot, body.intent_patch, source="answer",
            source_ref=answer.request_id, allowed=question.gap_ids)
        invalidate(state, previous)
    snapshot.messages.append({"kind": "answer", "question": question.model_dump(mode="json"),
        "answer": answer.model_dump(mode="json"), "processed": body.intent_patch is not None})
    state.pending_questions = [q for q in state.pending_questions if q.question_id != question.question_id]
    state.status = "waiting_user" if state.pending_questions else "draft"
    runtime.store.save(state, expected=state.state_version, kind="answer_accepted", messages=snapshot.messages,
        command=(answer.request_id, signature))
    return runtime.store.view(session_id)


@router.patch("/{session_id}", response_model=SessionView)
async def edit_session(session_id: str, body: EditSession, runtime=Depends(owned_session)):
    signature, snapshot = command(runtime, session_id, body, "edit")
    if snapshot is None:
        return runtime.store.view(session_id, replayed=True)
    state = snapshot.state
    if state.status in {"cancelled", "failed"}:
        raise PlanningError("session_terminal")
    if body.intent_patch is None and body.selections is None and body.message is None:
        raise PlanningError("empty_edit", 422)
    if body.intent_patch:
        previous = state.intent_snapshot.model_copy(deep=True)
        state.intent_snapshot = apply_patch(state.intent_snapshot, body.intent_patch, source="form", source_ref=body.request_id)
        invalidate(state, previous)
    if body.selections is not None:
        if not {s.place_id for s in body.selections} <= set(state.candidate_ids):
            raise PlanningError("unknown_selected_place", 422)
        state.selections = body.selections
    state.status, state.attention_reason = "draft", None
    state.agent_message = None
    state.pending_questions = []
    if state.current_plan:
        state.plan_needs_refresh = True
    # A manual edit supersedes unanswered interpretation jobs from the previous intent.
    for message in snapshot.messages:
        if message.get("kind") in {"answer", "intake"} and not message.get("processed"):
            message["processed"], message["superseded"] = True, True
    if body.message:
        snapshot.messages.append({"kind": "intake", "text": body.message, "message_id": body.request_id, "processed": False})
    runtime.store.save(state, expected=state.state_version, kind="user_edited", messages=snapshot.messages,
        command=(body.request_id, signature))
    return runtime.store.view(session_id)


@router.post("/{session_id}/cancel", response_model=SessionView)
async def cancel_session(session_id: str, body: VersionCommand, runtime=Depends(owned_session)):
    signature, snapshot = command(runtime, session_id, body, "cancel")
    if snapshot is None:
        return runtime.store.view(session_id, replayed=True)
    state = snapshot.state
    if state.status in {"completed", "cancelled", "failed"}:
        raise PlanningError("session_terminal")
    state.status, state.pending_questions = "cancelled", []
    state.attention_reason = "user_cancelled"
    state.agent_message = None
    runtime.store.save(state, expected=state.state_version, kind="cancelled", command=(body.request_id, signature))
    return runtime.store.view(session_id)


@router.get("/{session_id}/events", response_model=list[SessionEvent])
async def get_events(session_id: str, after: int = Query(default=0, ge=0), runtime=Depends(owned_session)):
    return runtime.store.events(session_id, after)
