"""Real HTTP/SQL/runtime control flow; only paid model/map services are substituted."""

import asyncio
import json
import time
from datetime import date
from uuid import uuid4

import pytest
import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app.agents.client import JsonModelClient
from app.agents.plan_agent import PlanAgent
from app.agents.question_agent import QuestionAgent
from app.api.routes.sessions import get_runtime
from app.config import Settings
from app.db import Base
from app.main import app
from app.migrations.planning import migrate_planning
from app.models.planning import PlanningCommand, PlanningEvent, PlanningMigration, PlanningSession
from app.models.trip import Trip
from app.providers.maps.base import MapError
from app.runtime.executor import PlanningRuntime
from app.runtime.guards import PlanningError
from app.runtime.guards import check_action
from app.schemas.action import AGENT_ACTION_ADAPTER
from app.runtime.store import SessionStore
from app.schemas.place import Coordinates, Place, ProviderRef
from app.schemas.session import Selection
from app.schemas.planning_api import CreateSession, IntentPatch, ProfileSnapshot
from app.services.intent_service import apply_patch, with_profile
from app.tools.registry import ToolRegistry


class ScriptedModel:
    def __init__(self):
        self.mode = "normal"
        self.delay = 0
        self.contexts = []

    async def complete(self, system, context, schema):
        self.contexts.append(context)
        if self.delay:
            await asyncio.sleep(self.delay)
        if "gap_ids" in context:
            return {"prompt": "这次共有几位旅行者？", "mode": "single", "options": [
                {"option_id": "two", "label": "2 人"}, {"option_id": "three", "label": "3 人"}]}
        if "answer" in context:
            return {"party": {"count_status": "exact", "count": 2}}
        state = context["state"]
        base = {"action_id": str(uuid4()), "base_state_version": state["state_version"], "purpose": "测试动作"}
        if self.mode == "invalid_once":
            self.mode = "pause"
            return {"kind": "shell", "command": "forbidden"}
        if self.mode == "empty_once":
            self.mode = "pause"
            raise PlanningError("model_empty_content")
        if self.mode == "finish":
            return {**base, "kind": "finish", "plan_ref": {"plan_id": "invented", "version": 1}}
        if self.mode == "stale":
            return {**base, "base_state_version": 0, "kind": "pause", "reason": "bad version"}
        if self.mode == "invalid":
            return {"kind": "shell", "command": "forbidden"}
        if self.mode == "pause":
            return {**base, "kind": "pause", "reason": "需要 P2 路线求解"}
        if state["intent_snapshot"]["party"]["count_status"] == "unknown":
            return {**base, "kind": "ask_user", "gap_ids": ["party"], "question_goal": "确定人数"}
        if not state["candidate_ids"] or state["candidates_need_refresh"] or self.mode == "repeat":
            return {**base, "kind": "search_candidates", "intent_ref": state["intent_snapshot"]["intent_id"],
                "region": state["intent_snapshot"]["destination"], "categories": ["attraction"],
                "filters": {"keywords": ["豫园", "外滩"]}, "limit": 5}
        return {**base, "kind": "pause", "reason": "候选已准备，P2 才能分天与求解交通"}


class FakeMap:
    def __init__(self, reserve):
        self.reserve = reserve

    def search_places(self, query, region, limit):
        self.reserve()
        return [Place(place_id="p-" + query, name="supplier-" + query, category="attraction", region=region,
            provider_refs=[ProviderRef(provider="amap", provider_place_id="supplier-id-" + query)],
            coordinates=Coordinates(longitude=121.123456, latitude=31.123456, crs="GCJ02"), identity_status="candidate")]

    def get_place(self, provider_place_id, region):
        return self.search_places(provider_place_id.removeprefix("supplier-id-"), region, 1)[0]


@pytest.fixture
def harness(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "p1.sqlite"), connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine)
    migrate_planning(engine)
    settings = Settings(_env_file=None, planning_model_call_limit=20, planning_map_call_limit=12,
        planning_step_limit=12, planning_run_timeout_seconds=5)
    store, model = SessionStore(factory, settings), ScriptedModel()
    runtime = PlanningRuntime(store, PlanAgent(model), ToolRegistry(QuestionAgent(model), settings, FakeMap), settings)
    app.dependency_overrides[get_runtime] = lambda: runtime
    app.state.planning_runtime = runtime
    with TestClient(app) as client:
        yield client, runtime, model, factory, engine
    app.dependency_overrides.clear()
    app.state.planning_runtime = None
    engine.dispose()


def body(known=True):
    return {"intent": {"intent_id": "i1", "destination": {"label": "上海", "country_code": "CN", "timezone": "Asia/Shanghai"},
        "party": {"count_status": "exact", "count": 2} if known else {"count_status": "unknown"},
        "dates": {"start_date": "2026-10-01", "end_date": "2026-10-05", "duration_days": 5, "timezone": "Asia/Shanghai"}},
        "profile": {"travel_style": ["少走路"], "preferences": {"interests": ["建筑"]}},
        "message": "上海五天，喜欢建筑，尽量不走回头路"}


def create(client, known=True):
    response = client.post("/api/sessions", json=body(known))
    assert response.status_code == 201, response.text
    data = response.json()
    session_id = data["state"]["session_id"]
    return "/api/sessions/" + session_id, {"Authorization": "Bearer " + data["access_token"]}, data


def wait_state(client, url, headers, status="needs_attention"):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = client.get(url, headers=headers).json()
        if result["state"]["status"] == status:
            return result
        time.sleep(0.01)
    pytest.fail("state did not reach " + status + ": " + str(result))


def run(client, url, headers, version, request_id=None):
    return client.post(url + "/run", headers=headers, json={"request_id": request_id or str(uuid4()), "base_state_version": version})


def test_plan_q_answer_search_pause_end_to_end(harness):
    client, runtime, model, factory, _ = harness
    url, headers, _ = create(client, known=False)
    assert run(client, url, headers, 0).status_code == 202
    waiting = wait_state(client, url, headers, "waiting_user")
    question = waiting["state"]["pending_questions"][0]
    assert question["gap_ids"] == ["party"]
    answer = {"answer": {"request_id": "answer1", "question_id": question["question_id"],
        "state_version": waiting["state"]["state_version"], "option_ids": ["two"]}}
    accepted = client.post(url + "/answers", headers=headers, json=answer)
    assert accepted.status_code == 200
    assert client.post(url + "/answers", headers=headers, json=answer).json()["replayed"]
    assert run(client, url, headers, accepted.json()["state"]["state_version"]).status_code == 202
    result = wait_state(client, url, headers)
    assert result["state"]["intent_snapshot"]["party"]["count"] == 2
    assert len(result["state"]["candidates"]) == 2
    assert result["usage"] == {"model_calls": 5, "map_calls": 2, "steps": 3,
        "model_limit": 20, "map_limit": 12, "step_limit": 12}
    assert result["state"]["current_plan_ref"] is None
    assert model.contexts[-1]["state"]["intent_snapshot"]["preferences"]["interests"] == ["建筑"]
    events = client.get(url + "/events", headers=headers).json()
    assert [e["data"]["action_kind"] for e in events if e["kind"] == "action_applied"] == ["ask_user", "search_candidates", "pause"]
    assert not client.get(url + "/events?after=" + str(events[-1]["event_id"]), headers=headers).json()


def test_restart_preserves_own_state_and_requires_map_refresh(harness):
    client, runtime, _, factory, _ = harness
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    restored = SessionStore(factory, runtime.settings)
    snapshot = restored.load(result["state"]["session_id"])
    assert snapshot.state.candidate_ids and not snapshot.state.candidates
    assert snapshot.state.candidates_need_refresh
    assert snapshot.profile.preferences.interests == ["建筑"]
    with factory() as db:
        row = db.get(PlanningSession, snapshot.state.session_id)
        persisted = json.dumps(row.state_json) + json.dumps([r.data for r in db.scalars(select(PlanningEvent))])
        assert "121.123456" not in persisted and "supplier-id" not in persisted and "supplier-" not in persisted
        assert headers["Authorization"][7:] not in persisted and headers["Authorization"][7:] != row.token_hash
    runtime.store = restored
    run(client, url, headers, snapshot.state.state_version)
    resumed = wait_state(client, url, headers)
    assert not resumed["state"]["candidates_need_refresh"]
    assert resumed["usage"]["map_calls"] == 4  # Recovery consumes existing budget, not a fresh one.


def test_session_ownership_isolated(harness):
    client, _, _, _, _ = harness
    url, headers, _ = create(client)
    _, foreign_headers, _ = create(client)
    for other in ({}, foreign_headers):
        assert client.get(url, headers=other).status_code == 404
        assert run(client, url, other, 0).status_code == 404
        assert client.get(url + "/events", headers=other).status_code == 404
    assert client.get(url, headers=headers).status_code == 200


def test_command_replay_and_conflicting_request_id(harness):
    client, _, model, _, _ = harness
    model.mode = "pause"
    url, headers, _ = create(client)
    assert run(client, url, headers, 0, "r1").status_code == 202
    result = wait_state(client, url, headers)
    assert run(client, url, headers, 0, "r1").json()["replayed"]
    response = run(client, url, headers, result["state"]["state_version"], "r1")
    assert response.status_code == 409
    assert response.json()["code"] == "request_id_reused_with_different_payload"


@pytest.mark.parametrize("mode,reason", [("finish", "plan_requires_refresh_or_wrong_ref"), ("stale", "stale_action"), ("invalid", "invalid_agent_contract")])
def test_untrusted_model_cannot_finish_or_bypass_contract(harness, mode, reason):
    client, _, model, _, _ = harness
    model.mode = mode
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    state = wait_state(client, url, headers)["state"]
    assert state["attention_reason"] == reason and state["current_plan_ref"] is None


@pytest.mark.parametrize("counter", ["steps", "map_calls", "model_calls"])
def test_budget_exhaustion_is_persistent_and_stops(harness, counter):
    client, runtime, model, factory, _ = harness
    model.mode = "repeat"
    url, headers, created = create(client)
    with factory() as db:
        row = db.get(PlanningSession, created["state"]["session_id"])
        setattr(row, {"steps": "step_limit", "map_calls": "map_limit", "model_calls": "model_limit"}[counter], 1)
        db.commit()
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    assert result["state"]["attention_reason"] == "budget_exhausted_or_obsolete"
    assert result["usage"][counter] == 1
    version = result["state"]["state_version"]
    run(client, url, headers, version)
    resumed = wait_state(client, url, headers)
    assert resumed["usage"][counter] == 1


def test_repeat_search_stops_before_third_paid_map_request(harness):
    client, _, model, _, _ = harness
    model.mode = "repeat"
    url, headers, _ = create(client)
    assert run(client, url, headers, 0).status_code == 202
    result = wait_state(client, url, headers)
    assert result["state"]["attention_reason"] == "search_stalled_no_new_candidates"
    assert result["usage"]["map_calls"] == 4  # two queries, twice
    events = client.get(url + "/events", headers=headers).json()
    assert any(e["data"].get("code") == "search_already_tried_without_new_candidates" for e in events)


def test_one_retry_preserves_intent_and_locked_places_with_new_bounded_usage(harness):
    client, runtime, _, _, _ = harness
    url, headers, created = create(client)
    state = runtime.store.load(created["state"]["session_id"]).state
    point = FakeMap(lambda: None).search_places("豫园", state.intent_snapshot.destination, 1)[0]
    state.candidates = [point]
    state.candidate_ids = [point.place_id]
    state.selections = [Selection(selection_id="lock-1", place_id=point.place_id, decision="lock",
        evidence={"source": "selection", "source_ref": "card", "confirmation": "explicit"})]
    state.status, state.attention_reason = "needs_attention", "search_stalled_no_new_candidates"
    original = runtime.store.save(state, expected=0, kind="fixture")
    response = client.post(url + "/retry", headers=headers,
        json={"request_id": "retry-1", "base_state_version": original.state_version})
    assert response.status_code == 201, response.text
    child = response.json()
    assert child["usage"]["model_calls"] == child["usage"]["map_calls"] == 0
    assert child["state"]["selections"][0]["place_id"] == point.place_id
    assert child["state"]["candidates"][0]["place_id"] == point.place_id
    assert child["state"]["retry_generation"] == 1
    assert client.post(url + "/retry", headers=headers,
        json={"request_id": "retry-2", "base_state_version": original.state_version}).status_code == 409


def test_hotel_search_uses_one_canonical_fallback_and_records_real_yield(harness):
    client, runtime, _, _, _ = harness
    calls = []
    class HotelMap(FakeMap):
        def search_places(self, query, region, limit):
            calls.append(query)
            if query != "酒店":
                return []
            return [Place(place_id="hotel-1", name="酒店", category="hotel", region=region,
                coordinates=Coordinates(longitude=121.1, latitude=31.1, crs="GCJ02"), identity_status="candidate")]
    runtime.registry.provider_factory = HotelMap
    _, _, created = create(client)
    snapshot = runtime.store.load(created["state"]["session_id"])
    action = AGENT_ACTION_ADAPTER.validate_python({"kind": "search_candidates", "action_id": "hotel-search",
        "base_state_version": 0, "purpose": "搜住宿", "intent_ref": snapshot.state.intent_snapshot.intent_id,
        "region": snapshot.state.intent_snapshot.destination.model_dump(mode="json"),
        "categories": ["hotel"], "filters": {"keywords": ["交通便利"]}, "limit": 5})
    next_state, _ = asyncio.run(runtime.registry.execute(action, snapshot, lambda: None, lambda: None))
    assert calls == ["交通便利", "酒店"]
    assert next_state.coverage["hotel"] == 1
    assert next_state.search_recipes[-1].new_count == 1


def test_answer_ids_versions_options_and_field_scope(harness):
    client, _, _, _, _ = harness
    url, headers, _ = create(client, known=False)
    run(client, url, headers, 0)
    state = wait_state(client, url, headers, "waiting_user")["state"]
    question = state["pending_questions"][0]
    base = {"request_id": "a", "question_id": question["question_id"], "state_version": state["state_version"], "option_ids": ["two"]}
    for changes, status in [({"question_id": "wrong"}, 422), ({"state_version": 0}, 409), ({"option_ids": ["bad"]}, 422), ({"option_ids": ["two", "three"]}, 422)]:
        assert client.post(url + "/answers", headers=headers, json={"answer": {**base, **changes}}).status_code == status
    assert client.post(url + "/answers", headers=headers,
        json={"answer": base, "intent_patch": {"preferences": {"pace": "busy"}}}).status_code == 422
    accepted = client.post(url + "/answers", headers=headers,
        json={"answer": base, "intent_patch": {"party": {"count_status": "exact", "count": 2}}})
    assert accepted.status_code == 200 and accepted.json()["state"]["intent_snapshot"]["party"]["count"] == 2
    assert client.post(url + "/answers", headers=headers, json={"answer": {**base, "request_id": "late"}}).status_code == 409


@pytest.mark.parametrize("operation", ["cancel", "edit"])
def test_inflight_result_cannot_overwrite_cancel_or_user_edit(harness, operation):
    client, runtime, model, _, _ = harness
    model.delay = 0.15
    url, headers, _ = create(client)
    ack = run(client, url, headers, 0).json()
    payload = {"request_id": "change", "base_state_version": ack["state"]["state_version"]}
    if operation == "cancel":
        response = client.post(url + "/cancel", headers=headers, json=payload)
    else:
        response = client.patch(url, headers=headers, json={**payload, "intent_patch": {"preferences": {"pace": "relaxed"}}})
    assert response.status_code == 200
    version = response.json()["state"]["state_version"]
    time.sleep(0.25)
    later = client.get(url, headers=headers).json()
    assert later["state"]["state_version"] == version and not later["state"]["candidates"]
    assert later["usage"]["map_calls"] == 0
    assert later["state"]["status"] == ("cancelled" if operation == "cancel" else "draft")


def test_live_lease_blocks_duplicate_worker_then_expired_lease_recovers(harness):
    client, runtime, model, factory, _ = harness
    model.mode, model.delay = "pause", 0.2
    url, headers, created = create(client)
    ack = run(client, url, headers, 0).json()
    assert run(client, url, headers, ack["state"]["state_version"]).status_code == 409
    done = wait_state(client, url, headers)
    with factory() as db:
        db.execute(update(PlanningSession).where(PlanningSession.session_id == created["state"]["session_id"])
            .values(status="running", lease_id="crashed", lease_until=time.time() - 1))
        db.commit()
    assert run(client, url, headers, done["state"]["state_version"]).status_code == 202
    resumed = wait_state(client, url, headers)["state"]
    assert resumed["attention_reason"] == "plan_paused"
    assert resumed["agent_message"] == "需要 P2 路线求解"


def test_lease_loss_discards_old_sql_write(harness):
    _, runtime, _, factory, _ = harness
    sid, _ = runtime.store.create(CreateSession.model_validate(body()))
    state = runtime.store.load(sid).state
    state.status = "running"
    state = runtime.store.save(state, expected=0, kind="claimed", new_lease="old")
    with factory() as db:
        db.execute(update(PlanningSession).where(PlanningSession.session_id == sid).values(lease_id="new"))
        db.commit()
    state.status, state.attention_reason = "needs_attention", "obsolete"
    with pytest.raises(PlanningError):
        runtime.store.save(state, expected=1, kind="late", lease_id="old")


def test_profile_precedence_and_answer_provenance():
    request = CreateSession.model_validate(body())
    profile = ProfileSnapshot(preferences={"pace": "busy", "interests": ["购物"]})
    request.intent.preferences = request.intent.preferences.model_validate({"pace": "relaxed", "interests": []})
    intent = with_profile(request.intent, profile)
    assert intent.preferences.pace == "relaxed" and intent.preferences.interests == []
    updated = apply_patch(intent, IntentPatch(party={"count_status": "exact", "count": 3}),
        source="answer", source_ref="a1", allowed=["party"])
    assert updated.field_evidence["party"].source == "answer"


def test_incremental_migration_preserves_legacy_trip(harness):
    _, _, _, factory, engine = harness
    Base.metadata.create_all(engine)
    with factory() as db:
        db.add(Trip(title="legacy", destination="上海", start_date=date(2026, 10, 1), end_date=date(2026, 10, 2)))
        db.commit()
    migrate_planning(engine)
    migrate_planning(engine)
    with factory() as db:
        assert db.scalar(select(Trip)).title == "legacy"
        assert len(db.scalars(select(PlanningMigration)).all()) == 1


def test_missing_model_key_is_actionable_and_no_secrets(harness):
    client, runtime, _, _, _ = harness
    runtime.plan = PlanAgent(JsonModelClient(Settings(_env_file=None, planning_api_key=None)))
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    assert result["state"]["attention_reason"] == "model_missing_credentials"
    assert result["usage"]["model_calls"] == 1


def test_overseas_deferred_and_provider_errors_do_not_fake_success(harness):
    client, runtime, model, _, _ = harness
    data = body()
    data["intent"]["destination"] = {"label": "新加坡", "country_code": "SG"}
    created = client.post("/api/sessions", json=data).json()
    url, headers = "/api/sessions/" + created["state"]["session_id"], {"Authorization": "Bearer " + created["access_token"]}
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    assert result["state"]["attention_reason"] == "overseas_deferred" and result["usage"]["map_calls"] == 0


def test_timeout_releases_worker_and_does_not_mark_completed(harness):
    client, runtime, model, _, _ = harness
    runtime.settings.planning_run_timeout_seconds = 0.03
    model.delay = 0.2
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    assert result["state"]["attention_reason"] == "run_timeout"
    assert runtime.store.load(result["state"]["session_id"]).lease_id is None


@pytest.mark.parametrize("code", ["permission_denied", "rate_limited", "unavailable", "invalid_response"])
def test_map_failure_stops_without_persisting_provider_body(harness, code):
    client, runtime, _, _, _ = harness

    class FailingMap(FakeMap):
        def search_places(self, query, region, limit):
            self.reserve()
            raise MapError(code, "Fixed provider error")

    runtime.registry.provider_factory = FailingMap
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    assert result["state"]["attention_reason"] == code
    assert result["usage"]["map_calls"] == 1 and result["state"]["candidates"] == []


def test_unknown_selection_and_stale_edit_are_rejected(harness):
    client, _, _, _, _ = harness
    url, headers, _ = create(client)
    payload = {"request_id": "e1", "base_state_version": 0, "selections": [
        {"selection_id": "s1", "place_id": "unknown", "decision": "lock", "evidence":
            {"source": "selection", "source_ref": "click", "confirmation": "explicit"}}]}
    assert client.patch(url, headers=headers, json=payload).status_code == 422
    payload = {"request_id": "e2", "base_state_version": 0, "intent_patch": {"notes": "new"}}
    assert client.patch(url, headers=headers, json=payload).status_code == 200
    assert client.patch(url, headers=headers, json={**payload, "request_id": "e3"}).status_code == 409


def test_question_survives_restart_and_structured_answer_needs_no_model(harness):
    client, runtime, _, factory, _ = harness
    url, headers, _ = create(client, known=False)
    run(client, url, headers, 0)
    waiting = wait_state(client, url, headers, "waiting_user")
    runtime.store = SessionStore(factory, runtime.settings)
    question = waiting["state"]["pending_questions"][0]
    result = client.post(url + "/answers", headers=headers, json={"answer": {
        "request_id": "a", "question_id": question["question_id"], "state_version": waiting["state"]["state_version"], "option_ids": ["two"]},
        "intent_patch": {"party": {"count": 2, "count_status": "exact"}}})
    assert result.status_code == 200
    assert result.json()["usage"]["model_calls"] == waiting["usage"]["model_calls"]
    assert result.json()["state"]["intent_snapshot"]["party"]["count"] == 2


def test_selection_survives_edit_and_cancel(harness):
    client, runtime, _, factory, _ = harness
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    selected = result["state"]["candidate_ids"][0]
    edit = client.patch(url, headers=headers, json={"request_id": "select", "base_state_version": result["state"]["state_version"],
        "selections": [{"selection_id": "s1", "place_id": selected, "decision": "lock", "date": "2026-10-01",
            "evidence": {"source": "selection", "source_ref": "click", "confirmation": "explicit"}}]})
    assert edit.status_code == 200
    cancelled = client.post(url + "/cancel", headers=headers, json={"request_id": "c", "base_state_version": edit.json()["state"]["state_version"]})
    assert cancelled.status_code == 200
    state = SessionStore(factory, runtime.settings).load(result["state"]["session_id"]).state
    assert state.selections[0].place_id == selected and state.selections[0].decision == "lock"


def test_graceful_shutdown_leaves_resumable_state(harness):
    client, runtime, model, _, _ = harness
    model.delay = 1
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    client.portal.call(runtime.shutdown)
    result = wait_state(client, url, headers)
    assert result["state"]["attention_reason"] == "server_shutdown_resume_available"
    assert runtime.store.load(result["state"]["session_id"]).lease_id is None


def test_pause_summary_is_volatile_and_not_a_supplier_cache(harness):
    client, runtime, _, factory, _ = harness
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    sid = result["state"]["session_id"]
    assert result["state"]["agent_message"]
    assert SessionStore(factory, runtime.settings).load(sid).state.agent_message is None
    with factory() as db:
        assert db.get(PlanningSession, sid).state_json["agent_message"] is None


def test_one_contract_repair_is_bounded_and_returns_to_plan(harness):
    client, _, model, _, _ = harness
    model.mode = "invalid_once"
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    assert result["state"]["attention_reason"] == "plan_paused"
    assert result["usage"]["model_calls"] == 2 and result["usage"]["steps"] == 2
    events = client.get(url + "/events", headers=headers).json()
    repair = [e for e in events if e["kind"] == "contract_repair"]
    assert len(repair) == 1 and "command" not in json.dumps(repair)
    assert model.contexts[-1]["last_observation"]["code"] == "invalid_agent_contract"


@pytest.mark.parametrize("status,content,expected", [
    (401, {}, "model_permission_denied"), (403, {}, "model_permission_denied"),
    (429, {}, "model_request_failed"), (500, {}, "model_request_failed"),
    (200, {"choices": [{"message": {"content": "not json"}}]}, "model_invalid_json"),
    (200, {"choices": [{"message": {"content": "[]"}}]}, "model_invalid_json"),
    (200, {"choices": [{"message": {"content": None}}]}, "model_empty_content"),
    (200, {"choices": [{"message": {"content": " "}}]}, "model_empty_content"),
    (200, {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}, "model_output_truncated"),
])
def test_model_http_errors_and_invalid_json_are_sanitized(monkeypatch, status, content, expected):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=content))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    model = JsonModelClient(Settings(_env_file=None, planning_api_key=SecretStr("unit-test-key")))
    with pytest.raises(PlanningError) as error:
        asyncio.run(model.complete("Return JSON", {}, {}))
    assert str(error.value) == expected and "unit-test-key" not in str(error.value)


def test_model_network_error_is_sanitized(monkeypatch):
    original = httpx.AsyncClient

    def fail(request):
        raise httpx.ConnectError("sensitive request details", request=request)

    transport = httpx.MockTransport(fail)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    model = JsonModelClient(Settings(_env_file=None, planning_api_key=SecretStr("unit-test-key")))
    with pytest.raises(PlanningError, match="^model_unavailable_or_timeout$"):
        asyncio.run(model.complete("Return JSON", {}, {}))


@pytest.mark.parametrize("scope,expected", [({"place_ids": ["invented"]}, "unknown_scope_place"),
    ({"dates": ["2026-10-06"]}, "scope_outside_trip_dates")])
def test_action_scope_cannot_escape_current_trip(harness, scope, expected):
    _, runtime, _, _, _ = harness
    sid, _ = runtime.store.create(CreateSession.model_validate(body()))
    state = runtime.store.load(sid).state
    action = AGENT_ACTION_ADAPTER.validate_python({"kind": "pause", "action_id": "a", "base_state_version": 0,
        "purpose": "test", "reason": "pause", "scope": scope})
    with pytest.raises(PlanningError, match=expected):
        check_action(action, state)


def test_empty_model_output_gets_only_one_budgeted_repair(harness):
    client, _, model, _, _ = harness
    model.mode = "empty_once"
    url, headers, _ = create(client)
    run(client, url, headers, 0)
    result = wait_state(client, url, headers)
    assert result["usage"]["model_calls"] == 2 and result["state"]["attention_reason"] == "plan_paused"
    assert model.contexts[-1]["last_observation"]["code"] == "model_empty_content"


@pytest.mark.parametrize("host,has_thinking", [("https://api.deepseek.com", True), ("https://compatible.example.test/v1", False)])
def test_provider_specific_thinking_switch_is_explicit(monkeypatch, host, has_thinking):
    original = httpx.AsyncClient
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    model = JsonModelClient(Settings(_env_file=None, planning_api_key=SecretStr("unit-test-key"), planning_base_url=host))
    assert asyncio.run(model.complete("JSON", {}, {})) == {}
    assert ("thinking" in captured[0]) is has_thinking
    assert captured[0]["max_tokens"] == 4096
    if has_thinking:
        assert captured[0]["thinking"] == {"type": "disabled"}
